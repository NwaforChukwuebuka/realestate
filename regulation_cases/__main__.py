"""
CLI entry-point for the Miami-Dade RER Regulation Cases scraper.

Usage
-----
    # Single folio number
    python -m regulation_cases 3021350210590

    # Multiple folios from a text file (one per line)
    python -m regulation_cases --file folios.txt

    # Search by address instead of folio
    python -m regulation_cases --by address "243 NW 10 ST"

    # Column from an existing CSV
    python -m regulation_cases --csv pipeline.csv --folio-col folio_number

    # Return every case row (not just strong leads)
    python -m regulation_cases --all-records 3021350210590

    # Save output
    python -m regulation_cases --out results.json 3021350210590
    python -m regulation_cases --out results.csv 3021350210590

    # Run headless
    python -m regulation_cases --headless 3021350210590

    # Save raw HTML snapshots for debugging
    python -m regulation_cases --save-html ./html_dumps 3021350210590
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from regulation_cases.scraper import RegulationCase, RegulationCasesScraper


def _load_from_file(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _load_from_csv(path: Path, col: str) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row[col].strip() for row in reader if row.get(col, "").strip()]


def _record_dict(case: RegulationCase) -> dict:
    d = case.to_dict()
    d.pop("search_index", None)
    return d


def _write_json(cases: list[RegulationCase], path: Path | None) -> None:
    data = [_record_dict(c) for c in cases]
    text = json.dumps(data, indent=2)
    if path:
        path.write_text(text, encoding="utf-8")
        print(f"[regulation_cases] Saved {len(data)} record(s) → {path}", file=sys.stderr)
    else:
        print(text)


def _write_csv(cases: list[RegulationCase], path: Path | None) -> None:
    if not cases:
        print("[]")
        return
    rows = [_record_dict(c) for c in cases]
    fieldnames = list(rows[0].keys())
    if path:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"[regulation_cases] Saved {len(rows)} record(s) → {path}", file=sys.stderr)
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m regulation_cases",
        description="Search Miami-Dade RER Regulation Cases for wholesale lead indicators.",
    )

    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "value",
        nargs="*",
        help="Folio number(s) or address words to search",
    )
    src.add_argument("--file", metavar="PATH", help="Text file with one folio/address per line")
    src.add_argument("--csv", metavar="PATH", help="CSV file containing folio numbers or addresses")

    parser.add_argument(
        "--folio-col",
        default="folio_number",
        metavar="COL",
        help="Column name when using --csv (default: folio_number). Use owner_name or property_address as needed.",
    )
    parser.add_argument(
        "--by",
        choices=["folio", "address", "owner"],
        default="folio",
        help="Search mode: 'folio' (default), 'address', or 'owner'",
    )
    parser.add_argument(
        "--all-records",
        action="store_true",
        help="Return every case row, not just strong lead indicators.",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Output file (.json or .csv). Prints to stdout if omitted.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium without a visible window (default: visible).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        metavar="SEC",
        help="Seconds between searches (default: 1.5).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30_000,
        metavar="MS",
        help="Per-action timeout in ms (default: 30000).",
    )
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        metavar="MS",
        help="Pause this many ms before every Playwright action (default: 0). Try 500.",
    )
    parser.add_argument(
        "--save-html",
        metavar="DIR",
        type=Path,
        help="Save raw results-page HTML into this directory after each search.",
    )

    args = parser.parse_args(argv)

    if args.file:
        values = _load_from_file(Path(args.file))
    elif args.csv:
        values = _load_from_csv(Path(args.csv), args.folio_col)
    elif args.value:
        values = [" ".join(args.value)]
    else:
        parser.print_help()
        sys.exit(1)

    if not values:
        print("No values to search.", file=sys.stderr)
        sys.exit(1)

    scraper = RegulationCasesScraper(
        search_by=args.by,
        headless=args.headless,
        timeout_ms=args.timeout,
        delay_s=args.delay,
        slow_mo_ms=args.slow_mo,
        all_records=args.all_records,
        results_html_dir=args.save_html,
    )

    cases = scraper.run(values)

    out_path = Path(args.out) if args.out else None
    fmt = out_path.suffix.lower() if out_path else ".json"
    if fmt == ".csv":
        _write_csv(cases, out_path)
    else:
        _write_json(cases, out_path)

    strong = sum(1 for c in cases if c.is_strong_lead)
    print(
        f"\n[regulation_cases] Done. {len(values)} search(es); "
        f"{len(cases)} case(s) returned; {strong} strong lead(s).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
