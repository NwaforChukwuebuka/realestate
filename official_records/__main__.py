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

    # Headerless CSV (one column per line, like address.csv)
    python -m official_records --csv official_records/address.csv --csv-no-header

    # Broad search but only keep high-value rows; skip addresses with none
    python -m official_records --csv ... --csv-no-header --all-records --high-value-only

    # Keep input CSV columns on each output row (one row per high-value hit)
    python -m official_records --csv pipeline.csv --address-col property_address --merge-csv

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

from official_records.scraper import OfficialRecord, OfficialRecordsScraper


def _load_addresses_from_file(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_addresses_from_csv(path: Path, col: str) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row[col].strip() for row in reader if row.get(col, "").strip()]


def _load_csv_rows_with_addresses(
    path: Path,
    *,
    address_col: str,
    no_header: bool,
    column_index: int,
) -> tuple[list[dict[str, str]], list[str]]:
    """
    Return (input_rows, addresses) in the same order. Each input row is a
    string-keyed dict suitable for CSV merge output.
    """
    rows: list[dict[str, str]] = []
    addresses: list[str] = []

    with path.open(newline="", encoding="utf-8") as f:
        if no_header:
            for row in csv.reader(f):
                if column_index >= len(row):
                    continue
                addr = row[column_index].strip()
                if not addr:
                    continue
                d = {f"col_{j}": (row[j] if j < len(row) else "") for j in range(len(row))}
                d["_csv_address"] = addr
                rows.append(d)
                addresses.append(addr)
        else:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or ()
            for raw in reader:
                row = {k: (raw.get(k) or "").strip() for k in fieldnames}
                addr = (raw.get(address_col) or "").strip()
                if not addr:
                    continue
                rows.append(row)
                addresses.append(addr)

    return rows, addresses


def _record_output_dict(rec: OfficialRecord) -> dict:
    d = rec.to_dict()
    d.pop("search_index", None)
    return d


def _merge_input_row_with_record(input_row: dict[str, str], rec: OfficialRecord) -> dict[str, str]:
    """Input columns first; record fields added, with ``or_`` prefix on key clashes."""
    out: dict[str, str] = dict(input_row)
    for k, v in _record_output_dict(rec).items():
        sval = "" if v is None else str(v)
        if k not in out:
            out[k] = sval
        else:
            out[f"or_{k}"] = sval
    return out


def _write_json(records, path: Path | None, *, row_dicts: list[dict] | None = None) -> None:
    if row_dicts is not None:
        data = row_dicts
    else:
        data = [_record_output_dict(r) for r in records]
    text = json.dumps(data, indent=2)
    if path:
        path.write_text(text, encoding="utf-8")
        print(f"[official_records] Saved {len(data)} record(s) → {path}", file=sys.stderr)
    else:
        print(text)


def _write_csv(
    records,
    path: Path | None,
    *,
    row_dicts: list[dict] | None = None,
) -> None:
    if row_dicts is not None:
        if not row_dicts:
            print("[]")
            return
        fieldnames = list(row_dicts[0].keys())
        rows = row_dicts
    else:
        if not records:
            print("[]")
            return
        fieldnames = list(_record_output_dict(records[0]).keys())
        rows = [_record_output_dict(r) for r in records]
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
        "--csv-no-header",
        action="store_true",
        help=(
            "Treat --csv as a headerless table: read the address from --csv-column "
            "(default 0). Other columns are kept as col_0, col_1, … when using --merge-csv."
        ),
    )
    parser.add_argument(
        "--csv-column",
        type=int,
        default=0,
        metavar="N",
        help="0-based address column index when using --csv-no-header (default: 0).",
    )
    parser.add_argument(
        "--high-value-only",
        action="store_true",
        help=(
            "After each search, drop non-high-value rows. Use with --all-records to "
            "scan the full listing but only output wholesale-indicator documents. "
            "Addresses with no high-value rows contribute nothing (skipped)."
        ),
    )
    parser.add_argument(
        "--merge-csv",
        action="store_true",
        help=(
            "With --csv, include every input column on each output row (one row per "
            "returned record). Requires --csv; best with a headered export or "
            "--merge-csv together with --csv-no-header (synthetic col_N keys)."
        ),
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
        help=(
            "Return every listing row from the broad search (same single query); "
            "default mode only returns rows whose Document Type is a wholesale indicator."
        ),
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

    if args.merge_csv and not args.csv:
        print("--merge-csv requires --csv.", file=sys.stderr)
        sys.exit(2)

    csv_input_rows: list[dict[str, str]] | None = None

    # Collect addresses
    if args.file:
        addresses = _load_addresses_from_file(Path(args.file))
    elif args.csv:
        csv_path = Path(args.csv)
        if args.merge_csv or args.csv_no_header:
            csv_input_rows, addresses = _load_csv_rows_with_addresses(
                csv_path,
                address_col=args.address_col,
                no_header=args.csv_no_header,
                column_index=args.csv_column,
            )
        else:
            addresses = _load_addresses_from_csv(csv_path, args.address_col)
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

    if args.high_value_only:
        records = [r for r in records if r.is_high_value]

    if args.merge_csv and csv_input_rows is not None:
        merged_rows = []
        for rec in records:
            idx = rec.search_index - 1
            if idx < 0 or idx >= len(csv_input_rows):
                continue
            merged_rows.append(_merge_input_row_with_record(csv_input_rows[idx], rec))
    else:
        merged_rows = None

    # Output
    out_path = Path(args.out) if args.out else None
    fmt = (out_path.suffix.lower() if out_path else ".json")
    if fmt == ".csv":
        _write_csv(records, out_path, row_dicts=merged_rows)
    else:
        _write_json(records, out_path, row_dicts=merged_rows)

    # Summary to stderr
    if merged_rows is not None:
        hv = sum(1 for r in records if r.is_high_value)
        n_addr = len(addresses)
        n_with_hit = len({r.search_index for r in records if r.is_high_value})
        print(
            f"\n[official_records] Done. {n_addr} address(es) searched; "
            f"{n_with_hit} with ≥1 high-value row; {len(merged_rows)} output row(s). "
            f"({hv} high-value record(s) in output).",
            file=sys.stderr,
        )
    else:
        hv = sum(1 for r in records if r.is_high_value)
        print(
            f"\n[official_records] Done. {len(records)} total record(s), {hv} high-value.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
