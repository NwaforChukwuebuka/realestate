from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from motivation.signals import compute_motivation_signals
from normalize.mapper import map_munroll_row_json
from normalize.models import NormalizedProperty

DEFAULT_RAW_TABLE = "munroll_raw"
DEFAULT_NORMALIZED_TABLE = "properties_normalized"

_MOTIVATION_DDL: tuple[tuple[str, str], ...] = (
    ("absentee_owner", "INTEGER"),
    ("out_of_state_owner", "INTEGER"),
    ("years_owned", "INTEGER"),
    ("old_property", "INTEGER NOT NULL DEFAULT 0"),
)


def _motivation_db_tuple(
    n: NormalizedProperty,
    *,
    as_of_year: int | None,
) -> tuple[object, object, object, int]:
    m = compute_motivation_signals(n, as_of_year=as_of_year)
    return (
        None if m.absentee_owner is None else (1 if m.absentee_owner else 0),
        None if m.out_of_state_owner is None else (1 if m.out_of_state_owner else 0),
        m.years_owned,
        1 if m.old_property else 0,
    )


def _normalized_row(
    n: NormalizedProperty,
    *,
    motivation_as_of_year: int | None = None,
) -> tuple[object, ...]:
    mot = _motivation_db_tuple(n, as_of_year=motivation_as_of_year)
    return (
        n.parcel_id,
        n.property_address,
        n.city,
        n.state,
        n.zip,
        n.owner_name,
        n.mailing_address,
        n.property_type,
        n.year_built,
        n.last_sale_date,
        n.assessed_value,
        *mot,
    )


@dataclass
class RebuildResult:
    """Counters for ``rebuild_from_raw`` / ``backfill_motivation``."""

    rows_read: int = 0
    rows_written: int = 0
    rows_failed: int = 0


class NormalizedStore:
    """Persist :class:`NormalizedProperty` rows for fast queries and joins to ``munroll_raw``."""

    def __init__(
        self,
        db_path: Path,
        *,
        raw_table: str = DEFAULT_RAW_TABLE,
        normalized_table: str = DEFAULT_NORMALIZED_TABLE,
    ) -> None:
        self.db_path = db_path
        self.raw_table = raw_table
        self.normalized_table = normalized_table
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL;")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> NormalizedStore:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def init_schema(self) -> None:
        conn = self.connect()
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.normalized_table} (
                parcel_id TEXT PRIMARY KEY NOT NULL,
                property_address TEXT,
                city TEXT,
                state TEXT,
                zip TEXT,
                owner_name TEXT,
                mailing_address TEXT,
                property_type TEXT,
                year_built INTEGER,
                last_sale_date TEXT,
                assessed_value INTEGER,
                absentee_owner INTEGER,
                out_of_state_owner INTEGER,
                years_owned INTEGER,
                old_property INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._ensure_motivation_columns(conn)
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self.normalized_table}_property_type
            ON {self.normalized_table} (property_type);
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self.normalized_table}_absentee_owner
            ON {self.normalized_table} (absentee_owner);
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self.normalized_table}_out_of_state_owner
            ON {self.normalized_table} (out_of_state_owner);
            """
        )
        conn.commit()

    def _ensure_motivation_columns(self, conn: sqlite3.Connection) -> None:
        """ALTER legacy ``properties_normalized`` tables to add motivation columns."""
        cur = conn.execute(f'PRAGMA table_info("{self.normalized_table}");')
        have = {str(row[1]) for row in cur.fetchall()}
        for col, ddl in _MOTIVATION_DDL:
            if col in have:
                continue
            conn.execute(
                f'ALTER TABLE "{self.normalized_table}" ADD COLUMN {col} {ddl};',
            )

    def count_normalized(self) -> int:
        conn = self.connect()
        cur = conn.execute(f"SELECT COUNT(*) FROM {self.normalized_table};")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def clear_normalized(self) -> None:
        conn = self.connect()
        conn.execute(f"DELETE FROM {self.normalized_table};")
        conn.commit()

    def backfill_motivation(
        self,
        *,
        batch_size: int = 2000,
        motivation_as_of_year: int | None = None,
        result: RebuildResult | None = None,
    ) -> RebuildResult:
        """Recompute motivation columns from existing situs/mailing fields.

        Run after ``init_schema`` adds new columns to an older database, or to
        refresh flags without re-reading ``munroll_raw``.
        """
        self.init_schema()
        out = result or RebuildResult()
        conn = self.connect()
        prev_factory = conn.row_factory
        conn.row_factory = sqlite3.Row

        select_sql = f"""
            SELECT parcel_id, property_address, city, state, zip,
                   owner_name, mailing_address, property_type, year_built,
                   last_sale_date, assessed_value
            FROM {self.normalized_table};
            """
        update_sql = f"""
            UPDATE {self.normalized_table}
            SET absentee_owner = ?,
                out_of_state_owner = ?,
                years_owned = ?,
                old_property = ?
            WHERE parcel_id = ?;
            """

        cur = conn.execute(select_sql)
        batch: list[tuple[object, ...]] = []

        def flush() -> None:
            if not batch:
                return
            n_done = len(batch)
            conn.executemany(update_sql, batch)
            conn.commit()
            batch.clear()
            out.rows_written += n_done

        try:
            while True:
                chunk = cur.fetchmany(batch_size)
                if not chunk:
                    break
                for row in chunk:
                    out.rows_read += 1
                    n = NormalizedProperty(
                        parcel_id=str(row["parcel_id"]),
                        property_address=row["property_address"],
                        city=row["city"],
                        state=row["state"],
                        zip=row["zip"],
                        owner_name=row["owner_name"],
                        mailing_address=row["mailing_address"],
                        property_type=row["property_type"],
                        year_built=row["year_built"],
                        last_sale_date=row["last_sale_date"],
                        assessed_value=row["assessed_value"],
                    )
                    a, o, y, old = _motivation_db_tuple(
                        n,
                        as_of_year=motivation_as_of_year,
                    )
                    batch.append((a, o, y, old, n.parcel_id))
                    if len(batch) >= batch_size:
                        flush()
            flush()
        finally:
            conn.row_factory = prev_factory
        return out

    def rebuild_from_raw(
        self,
        *,
        batch_size: int = 2000,
        clear_first: bool = True,
        residential_only: bool = False,
        motivation_as_of_year: int | None = None,
        result: RebuildResult | None = None,
    ) -> RebuildResult:
        """Fill ``properties_normalized`` from ``munroll_raw.row_json``.

        Join key: ``munroll_raw.folio`` = ``properties_normalized.parcel_id``.

        If ``clear_first`` is True (default), delete all normalized rows before
        loading so removed folios do not linger.

        If ``residential_only`` is True, only rows passing
        :func:`filters.residential.is_target_residential` on ``property_type``
        (MunRoll ``Land Use``) are inserted.

        Motivation columns (plan step 4) are computed on each insert. Pass
        ``motivation_as_of_year`` for reproducible ``years_owned`` (default:
        calendar year of the run date).
        """
        self.init_schema()
        out = result or RebuildResult()
        conn = self.connect()
        if clear_first:
            self.clear_normalized()

        res_check = None
        if residential_only:
            from filters.residential import is_target_residential

            res_check = is_target_residential

        insert_sql = f"""
            INSERT OR REPLACE INTO {self.normalized_table} (
                parcel_id,
                property_address,
                city,
                state,
                zip,
                owner_name,
                mailing_address,
                property_type,
                year_built,
                last_sale_date,
                assessed_value,
                absentee_owner,
                out_of_state_owner,
                years_owned,
                old_property
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """

        cur = conn.execute(f"SELECT row_json FROM {self.raw_table};")
        batch: list[tuple[object, ...]] = []

        def flush() -> None:
            if not batch:
                return
            conn.executemany(insert_sql, batch)
            conn.commit()
            batch.clear()

        while True:
            chunk = cur.fetchmany(batch_size)
            if not chunk:
                break
            for (row_json,) in chunk:
                out.rows_read += 1
                try:
                    n = map_munroll_row_json(str(row_json))
                    if res_check is not None and not res_check(n.property_type):
                        continue
                    batch.append(
                        _normalized_row(
                            n,
                            motivation_as_of_year=motivation_as_of_year,
                        ),
                    )
                    out.rows_written += 1
                except (TypeError, ValueError, json.JSONDecodeError):
                    out.rows_failed += 1
                    continue
                if len(batch) >= batch_size:
                    flush()
        flush()
        return out
