"""Build ``properties_normalized`` from ``munroll_raw``: python -m normalize --db .munroll_raw.sqlite"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Rebuild normalized property table from munroll_raw (same SQLite DB). "
            "Computes motivation columns (absentee, out-of-state, years_owned, old_property) on each row."
        ),
    )
    p.add_argument(
        "--db",
        type=Path,
        default=Path(".munroll_raw.sqlite"),
        help="SQLite DB containing munroll_raw",
    )
    p.add_argument("--batch-size", type=int, default=2000, help="Rows per read/write batch")
    p.add_argument(
        "--no-truncate",
        action="store_true",
        help="Upsert only; do not DELETE FROM properties_normalized first",
    )
    p.add_argument(
        "--residential-only",
        action="store_true",
        help="Only insert rows that pass the residential land-use filter (same rules as: python -m filters apply)",
    )
    p.add_argument(
        "--motivation-as-of-year",
        type=int,
        default=None,
        help="Year for years_owned (default: current calendar year)",
    )
    p.add_argument(
        "--motivation-backfill-only",
        action="store_true",
        help="Skip munroll_raw rebuild; recompute motivation columns from existing normalized rows",
    )
    args = p.parse_args(argv)

    if not args.db.is_file():
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 1

    from normalize.store import NormalizedStore

    with NormalizedStore(args.db) as store:
        if args.motivation_backfill_only:
            stats = store.backfill_motivation(
                batch_size=args.batch_size,
                motivation_as_of_year=args.motivation_as_of_year,
            )
            total = store.count_normalized()
        else:
            stats = store.rebuild_from_raw(
                batch_size=args.batch_size,
                clear_first=not args.no_truncate,
                residential_only=args.residential_only,
                motivation_as_of_year=args.motivation_as_of_year,
            )
            total = store.count_normalized()

    print(
        "rows_read",
        stats.rows_read,
        "rows_written",
        stats.rows_written,
        "rows_failed",
        stats.rows_failed,
        "normalized_rows",
        total,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
