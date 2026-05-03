"""Prune normalized table to in-scope residential land uses: ``python -m filters apply --db .munroll_raw.sqlite``"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m filters",
        description="Pre-filter SQLite pipeline tables (MunRoll land use)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    apply_p = sub.add_parser(
        "apply",
        help="Remove non-residential rows from properties_normalized (run after normalize)",
    )
    apply_p.add_argument(
        "--db",
        type=Path,
        default=Path(".munroll_raw.sqlite"),
        help="SQLite DB with properties_normalized",
    )
    apply_p.add_argument(
        "--read-batch",
        type=int,
        default=5000,
        help="Rows per SELECT fetchmany",
    )
    apply_p.add_argument(
        "--delete-batch",
        type=int,
        default=2000,
        help="Rows per DELETE batch",
    )

    args = p.parse_args(argv)

    if args.cmd == "apply":
        if not args.db.is_file():
            print(f"Database not found: {args.db}", file=sys.stderr)
            return 1
        from filters.prune import prune_non_residential_normalized

        stats = prune_non_residential_normalized(
            args.db,
            read_batch_size=args.read_batch,
            delete_batch_size=args.delete_batch,
        )
        print(
            "rows_before",
            stats.rows_before,
            "rows_removed",
            stats.rows_removed,
            "rows_after",
            stats.rows_after,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
