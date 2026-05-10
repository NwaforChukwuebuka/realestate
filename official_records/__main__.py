"""
CLI entry-point for the Miami-Dade Official Records scraper.

Usage
-----
    # Single address (pass as positional args)
    python -m official_records 243 NW 10 ST

    # Multiple addresses from a plain-text file (one per line)
    python -m official_records --file addresses.txt

    # Column from an existing CSV (e.g. your pipeline export)
    python -m official_records --csv property_pipeline.csv --address-col property_address

    # Save output
    python -m official_records --file addresses.txt --out results.json
    python -m official_records --file addresses.txt --out results.csv

    # Run headless (no browser window)
    python -m official_records --headless 243 NW 10 ST

    # Save raw results-page HTML for debugging / selector work
    python -m official_records --save-html ./html_dumps 243 NW 10 ST
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from official_records.scraper import OfficialRecordsScraper


def _load_addresses_from_file(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_addresses_from_csv(path: Path, col: str) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row[col].strip() for row in reader if row.get(col, "").strip()]


def _write_json(records, path: Path | None) -> None:
    data = [r.to_dict() for r in records]
    text = json.dumps(data, indent=2)
    if path:
        path.write_text(text, encoding="utf-8")
        print(f"[official_records] Saved {len(data)} record(s) → {path}", file=sys.stderr)
    else:
        print(text)


def _write_csv(records, path: Path | None) -> None:
    if not records:
        print("[]")
        return
    fieldnames = list(records[0].to_dict().keys())
    rows = [r.to_dict() for r in records]
    if path:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"[official_records] Saved {len(rows)} record(s) → {path}", file=sys.stderr)
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m official_records",
        description="Search Miami-Dade Official Records for wholesale lead indicators.",
    )

    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "address",
        nargs="*",
        help="Property address words (e.g. 243 NW 10 ST)",
    )
    src.add_argument("--file", metavar="PATH", help="Text file with one address per line")
    src.add_argument("--csv", metavar="PATH", help="CSV file containing addresses")

    parser.add_argument(
        "--address-col",
        default="property_address",
        metavar="COL",
        help="Column name for the address when using --csv (default: property_address)",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Output file path (.json or .csv). Prints to stdout if omitted.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium without a visible window (default: visible).",
    )
    # Back-compat: previously the default was headless and --no-headless opted
    # into a visible window. Visible is now the default, so this flag is a
    # no-op kept only so old commands don't error out.
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--all-records",
        action="store_true",
        help="Return every record found, not just high-value lead indicators.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        metavar="SEC",
        help="Seconds to pause between address searches (default: 1.5).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30_000,
        metavar="MS",
        help="Per-action timeout in milliseconds (default: 30000).",
    )
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        metavar="MS",
        help=(
            "Pause this many milliseconds before every Playwright action "
            "so you can watch the browser (default: 0). Try 500."
        ),
    )
    parser.add_argument(
        "--save-html",
        metavar="DIR",
        type=Path,
        help=(
            "After each search, write the full results page HTML into this "
            "directory (official_records_<n>_<address>.html)."
        ),
    )

    args = parser.parse_args(argv)

    # Collect addresses
    if args.file:
        addresses = _load_addresses_from_file(Path(args.file))
    elif args.csv:
        addresses = _load_addresses_from_csv(Path(args.csv), args.address_col)
    elif args.address:
        addresses = [" ".join(args.address)]
    else:
        parser.print_help()
        sys.exit(1)

    if not addresses:
        print("No addresses to search.", file=sys.stderr)
        sys.exit(1)

    scraper = OfficialRecordsScraper(
        headless=args.headless,
        timeout_ms=args.timeout,
        delay_s=args.delay,
        slow_mo_ms=args.slow_mo,
        all_records=args.all_records,
        results_html_dir=args.save_html,
    )

    records = scraper.run(addresses)

    # Output
    out_path = Path(args.out) if args.out else None
    fmt = (out_path.suffix.lower() if out_path else ".json")
    if fmt == ".csv":
        _write_csv(records, out_path)
    else:
        _write_json(records, out_path)

    # Summary to stderr
    hv = sum(1 for r in records if r.is_high_value)
    print(
        f"\n[official_records] Done. {len(records)} total record(s), {hv} high-value.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
