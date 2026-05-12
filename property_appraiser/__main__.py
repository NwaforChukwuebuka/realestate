"""
CLI entry-point for the Miami-Dade Property Appraiser scraper.

Usage
-----
    # Single folio (dashes optional)
    python -m property_appraiser 01-0101-060-1200
    python -m property_appraiser 0101010601200

    # Multiple folios from a text file (one per line)
    python -m property_appraiser --file folios.txt

    # Column from an existing pipeline CSV
    python -m property_appraiser --csv pipeline.csv --folio-col folio_number

    # Save output
    python -m property_appraiser --out results.json 0101010601200
    python -m property_appraiser --out results.csv  0101010601200

    # Run headless, save raw HTML for debugging
    python -m property_appraiser --headless --save-html ./html_dumps 0101010601200
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from property_appraiser.scraper import PropertyRecord, PropertyAppraiserScraper


def _load_file(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _load_csv(path: Path, col: str) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row[col].strip() for row in reader if row.get(col, "").strip()]


def _record_dict(rec: PropertyRecord) -> dict:
    d = rec.to_dict()
    d.pop("search_index", None)
    return d


def _write_json(records: list[PropertyRecord], path: Path | None) -> None:
    data = [_record_dict(r) for r in records]
    text = json.dumps(data, indent=2)
    if path:
        path.write_text(text, encoding="utf-8")
        print(f"[property_appraiser] Saved {len(data)} record(s) → {path}", file=sys.stderr)
    else:
        print(text)


def _write_csv(records: list[PropertyRecord], path: Path | None) -> None:
    if not records:
        print("[]")
        return
    rows = [_record_dict(r) for r in records]
    fieldnames = list(rows[0].keys())
    if path:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"[property_appraiser] Saved {len(rows)} record(s) → {path}", file=sys.stderr)
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m property_appraiser",
        description="Scrape Miami-Dade PA property records by folio number.",
    )

    src = parser.add_mutually_exclusive_group()
    src.add_argument("folio", nargs="*", help="Folio number(s) (dashes optional)")
    src.add_argument("--file", metavar="PATH", help="Text file with one folio per line")
    src.add_argument("--csv",  metavar="PATH", help="CSV file containing folio numbers")

    parser.add_argument(
        "--folio-col", default="folio_number", metavar="COL",
        help="Column name when using --csv (default: folio_number)",
    )
    parser.add_argument("--out",  metavar="PATH", help="Output file (.json or .csv); stdout if omitted")
    parser.add_argument("--headless", action="store_true", help="Run without a visible browser window")
    parser.add_argument("--delay",   type=float, default=1.5, metavar="SEC",
                        help="Seconds between folio searches (default: 1.5)")
    parser.add_argument("--timeout", type=int,   default=30_000, metavar="MS",
                        help="Per-action timeout in ms (default: 30000)")
    parser.add_argument("--slow-mo", type=int,   default=0, metavar="MS",
                        help="Pause this many ms before every Playwright action (default: 0)")
    parser.add_argument("--save-html", metavar="DIR", type=Path,
                        help="Save raw page HTML into this directory after each folio")

    args = parser.parse_args(argv)

    if args.file:
        folios = _load_file(Path(args.file))
    elif args.csv:
        folios = _load_csv(Path(args.csv), args.folio_col)
    elif args.folio:
        folios = args.folio
    else:
        parser.print_help()
        sys.exit(1)

    if not folios:
        print("No folios to search.", file=sys.stderr)
        sys.exit(1)

    scraper = PropertyAppraiserScraper(
        headless=args.headless,
        timeout_ms=args.timeout,
        delay_s=args.delay,
        slow_mo_ms=args.slow_mo,
        results_html_dir=args.save_html,
    )

    records = scraper.run(folios)

    out_path = Path(args.out) if args.out else None
    fmt = out_path.suffix.lower() if out_path else ".json"
    if fmt == ".csv":
        _write_csv(records, out_path)
    else:
        _write_json(records, out_path)

    absentee = sum(1 for r in records if r.absentee_owner)
    entity   = sum(1 for r in records if r.trust_or_llc_owner)
    quality  = sum(1 for r in records if r.quality_lead)
    print(
        f"\n[property_appraiser] Done. {len(folios)} folio(s) searched; "
        f"{len(records)} record(s) returned; "
        f"{absentee} absentee owner(s); {entity} trust/LLC owner(s); "
        f"{quality} quality lead(s).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
