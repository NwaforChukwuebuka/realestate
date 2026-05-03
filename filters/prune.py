from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from filters.residential import is_target_residential
from normalize.store import NormalizedStore

DEFAULT_NORMALIZED_TABLE = "properties_normalized"


@dataclass
class PruneResult:
    rows_before: int
    rows_removed: int
    rows_after: int


def prune_non_residential_normalized(
    db_path: Path,
    *,
    normalized_table: str = DEFAULT_NORMALIZED_TABLE,
    read_batch_size: int = 5000,
    delete_batch_size: int = 2000,
) -> PruneResult:
    """Delete rows in ``properties_normalized`` that fail the residential land-use filter.

    ``munroll_raw`` is unchanged so you can rebuild or re-filter later.
    """
    with NormalizedStore(db_path, normalized_table=normalized_table) as store:
        store.init_schema()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {normalized_table};")
        row = cur.fetchone()
        before = int(row[0]) if row else 0

        sel = conn.execute(
            f"SELECT parcel_id, property_type FROM {normalized_table};",
        )
        to_delete: list[str] = []

        def flush_deletes() -> None:
            if not to_delete:
                return
            placeholders = ",".join("?" * len(to_delete))
            conn.execute(
                f"DELETE FROM {normalized_table} WHERE parcel_id IN ({placeholders});",
                to_delete,
            )
            conn.commit()
            to_delete.clear()

        while True:
            chunk = sel.fetchmany(read_batch_size)
            if not chunk:
                break
            for parcel_id, property_type in chunk:
                pid = (parcel_id or "").strip()
                if not pid:
                    continue
                if not is_target_residential(property_type):
                    to_delete.append(pid)
                    if len(to_delete) >= delete_batch_size:
                        flush_deletes()
        flush_deletes()

        cur2 = conn.execute(f"SELECT COUNT(*) FROM {normalized_table};")
        row2 = cur2.fetchone()
        after = int(row2[0]) if row2 else 0
    finally:
        conn.close()

    removed = before - after
    return PruneResult(rows_before=before, rows_removed=removed, rows_after=after)
