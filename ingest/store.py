from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ingest.reader import FOLIO_FIELD, iter_munroll_records

DEFAULT_TABLE = "munroll_raw"


@dataclass
class ImportResult:
    """Summary counters for a MunRoll CSV import."""

    rows_read: int = 0
    """Data rows seen from the CSV (including duplicates by Folio)."""

    rows_stored: int = 0
    """Rows written with non-empty Folio (each batch insert counts one per row)."""

    rows_skipped_empty_folio: int = 0
    """Rows skipped because Folio was missing or whitespace-only."""

    duplicates_resolved: int = 0
    """Rows where Folio was already present before this insert in the same run (last wins)."""

    _folios_seen: set[str] = field(default_factory=set, repr=False)

    @property
    def unique_folios(self) -> int:
        """Distinct Folio values that were stored at least once in this import."""
        return len(self._folios_seen)


class MunrollStore:
    """SQLite-backed storage for raw MunRoll rows, one row per Folio (deduped)."""

    def __init__(
        self,
        db_path: Path,
        *,
        table: str = DEFAULT_TABLE,
    ) -> None:
        self.db_path = db_path
        self.table = table
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

    def __enter__(self) -> MunrollStore:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def init_schema(self) -> None:
        conn = self.connect()
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table} (
                folio TEXT PRIMARY KEY NOT NULL,
                row_json TEXT NOT NULL
            );
            """
        )
        conn.commit()

    def import_csv(
        self,
        csv_path: Path,
        *,
        batch_size: int = 2000,
        result: ImportResult | None = None,
    ) -> ImportResult:
        """Stream ``csv_path`` into SQLite; duplicate Folio values keep the last row."""
        self.init_schema()
        out = result or ImportResult()
        conn = self.connect()
        sql = f"INSERT OR REPLACE INTO {self.table} (folio, row_json) VALUES (?, ?)"

        batch: list[tuple[str, str]] = []

        def flush() -> None:
            if not batch:
                return
            conn.executemany(sql, batch)
            conn.commit()
            batch.clear()

        for rec in iter_munroll_records(csv_path):
            out.rows_read += 1
            raw_folio = rec.get(FOLIO_FIELD)
            folio = (raw_folio or "").strip()
            if not folio:
                out.rows_skipped_empty_folio += 1
                continue
            if folio in out._folios_seen:
                out.duplicates_resolved += 1
            out._folios_seen.add(folio)
            payload = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
            batch.append((folio, payload))
            out.rows_stored += 1
            if len(batch) >= batch_size:
                flush()

        flush()
        return out

    def count_rows(self) -> int:
        conn = self.connect()
        cur = conn.execute(f"SELECT COUNT(*) FROM {self.table};")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def get_row_json(self, folio: str) -> str | None:
        conn = self.connect()
        cur = conn.execute(
            f"SELECT row_json FROM {self.table} WHERE folio = ?;",
            (folio.strip(),),
        )
        r = cur.fetchone()
        return str(r[0]) if r else None
