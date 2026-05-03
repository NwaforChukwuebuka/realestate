"""Import MunRoll CSV into SQLite: python -m ingest path/to/file.csv"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Import Miami-Dade MunRoll CSV into SQLite (dedupe by Folio)")
    p.add_argument("csv", type=Path, help="Path to MunRoll CSV (preamble + Folio header)")
    p.add_argument(
        "--db",
        type=Path,
        default=Path(".munroll_raw.sqlite"),
        help="SQLite database path",
    )
    p.add_argument("--batch-size", type=int, default=2000, help="Rows per transaction batch")
    args = p.parse_args(argv)

    if not args.csv.is_file():
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        return 1

    from ingest.store import MunrollStore

    with MunrollStore(args.db) as store:
        stats = store.import_csv(args.csv, batch_size=args.batch_size)
        total_in_db = store.count_rows()

    print(
        "rows_read",
        stats.rows_read,
        "rows_stored",
        stats.rows_stored,
        "skipped_empty_folio",
        stats.rows_skipped_empty_folio,
        "duplicates_in_file",
        stats.duplicates_resolved,
        "unique_folios",
        stats.unique_folios,
        "db_rows",
        total_in_db,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
