"""
CLI entry-point for the Miami-Dade wholesale property research pipeline.

Usage
-----
    # ── Pre-score ALL 419k rows instantly (no browser) ──────────────────────
    python -m research --pre-score

    # Export the full pre-scored table to CSV
    python -m research --export-scores --out all_leads.csv
    python -m research --export-scores --min-score 2 --out strong_leads.csv
    python -m research --export-scores --category motivated_sellers --out motivated.csv

    # ── Run the full 3-scraper pipeline on top pre-scored leads ─────────────
    python -m research --db --absentee-only --min-years 10 --limit 50
    python -m research --db --absentee-only --old-property --limit 100 --out opps.csv
    python -m research --db --count-only --absentee-only --min-years 15

    # Run with multiple parallel workers (persistent browser, crash-safe)
    python -m research --db --absentee-only --workers 4 --log-dir logs/
    python -m research --db --workers 8 --headless --log-dir logs/ --out opps.csv

    # Export already-researched opportunities
    python -m research --db --export-opportunities --out opportunities.csv

    # ── Single / file / CSV input ────────────────────────────────────────────
    python -m research 0101010501080
    python -m research --file folios.txt
    python -m research --csv pipeline.csv --folio-col folio_number
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from research.pipeline import ResearchReport, ResearchPipeline


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------

def _load_file(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _load_csv(path: Path, col: str) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        return [row[col].strip() for row in csv.DictReader(f) if row.get(col, "").strip()]


def _load_db(args) -> list[str]:
    from research.db import load_candidates
    rows = load_candidates(
        absentee_only=args.absentee_only,
        min_years_owned=args.min_years,
        old_property_only=args.old_property,
        out_of_state_only=args.out_of_state,
        skip_researched=not args.rerun,
        limit=args.limit,
        offset=args.offset,
    )
    return [r["parcel_id"] for r in rows]


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _flat_dict(report: ResearchReport) -> dict:
    pa = report.property_record
    doc_types = ", ".join(
        r.doc_type_code for r in report.official_records
        if r.is_high_value and r.doc_type_code
    )
    return {
        "folio":                    report.folio,
        "property_address":         pa.property_address if pa else "",
        "owner_name":               pa.owner_name if pa else "",
        "mailing_address":          pa.mailing_address if pa else "",
        "year_built":               pa.year_built if pa else "",
        "last_sale_date":           pa.previous_sale_date if pa else "",
        "last_sale_price":          pa.previous_sale_price if pa else "",
        "absentee_owner":           pa.absentee_owner if pa else "",
        "trust_or_llc":             pa.trust_or_llc_owner if pa else "",
        "quality_lead":             pa.quality_lead if pa else "",
        "rer_found_by":             report.rer_search_method,
        "regulation_cases":         len(report.regulation_cases),
        "strong_violations":        sum(1 for c in report.regulation_cases if c.is_strong_lead),
        "official_records":         len(report.official_records),
        "high_value_doc_types":     doc_types,
        "triggered_signals":        " | ".join(report.triggered_signals),
        "categories":               " | ".join(report.categories),
        "lead_score":               report.lead_score,
        "is_wholesale_opportunity": report.is_wholesale_opportunity,
    }


def _write_json(reports: list[ResearchReport], path: Path | None) -> None:
    data = [r.to_dict() for r in reports]
    text = json.dumps(data, indent=2, default=str)
    if path:
        path.write_text(text, encoding="utf-8")
        print(f"[research] Saved {len(data)} report(s) → {path}", file=sys.stderr)
    else:
        print(text)


def _write_csv(rows: list[dict], path: Path | None) -> None:
    if not rows:
        print("(no rows)", file=sys.stderr)
        return
    fieldnames = list(rows[0].keys())
    if path:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"[research] Saved {len(rows)} row(s) → {path}", file=sys.stderr)
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m research",
        description="Miami-Dade wholesale research: PA + RER + Official Records pipeline.",
    )

    # ── Pre-score mode (no browser) ─────────────────────────────────────────
    parser.add_argument("--pre-score", action="store_true",
                        help="Score ALL rows in properties_normalized instantly and exit")
    parser.add_argument("--export-scores", action="store_true",
                        help="Export pre-scored rows to --out (CSV/JSON) and exit")
    parser.add_argument("--min-score", type=int, default=1, metavar="N",
                        help="Minimum pre_score when exporting (default: 1)")
    parser.add_argument("--category", metavar="NAME",
                        choices=["motivated_sellers", "inherited_properties",
                                 "distressed_ownership", "potential_wholesale"],
                        help="Filter export to one category")

    # Input source
    parser.add_argument("folio", nargs="*", help="Folio number(s)")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--file", metavar="PATH", help="Text file, one folio per line")
    src.add_argument("--csv",  metavar="PATH", help="CSV file with a folio column")
    src.add_argument("--db",   action="store_true",
                     help="Read folios from .munroll_raw.sqlite (properties_normalized)")

    # --db filters
    db_grp = parser.add_argument_group("--db filters")
    db_grp.add_argument("--absentee-only",  action="store_true", help="Only absentee owners")
    db_grp.add_argument("--old-property",   action="store_true", help="Only old_property = 1")
    db_grp.add_argument("--out-of-state",   action="store_true", help="Only out-of-state owners")
    db_grp.add_argument("--min-years",      type=int, default=0, metavar="N",
                        help="Only properties owned ≥ N years")
    db_grp.add_argument("--limit",          type=int, default=None, metavar="N",
                        help="Max folios to process from DB")
    db_grp.add_argument("--offset",         type=int, default=0, metavar="N",
                        help="Skip first N rows (for resuming batches)")
    db_grp.add_argument("--rerun",          action="store_true",
                        help="Re-research parcels that already have a result")
    db_grp.add_argument("--count-only",     action="store_true",
                        help="Print candidate count matching filters and exit")
    db_grp.add_argument("--export-opportunities", action="store_true",
                        help="Export already-researched wholesale opportunities and exit")

    # CSV input option
    parser.add_argument("--folio-col", default="folio_number", metavar="COL")

    # Output
    parser.add_argument("--out",              metavar="PATH", help="Output file (.json or .csv)")
    parser.add_argument("--opportunities-only", action="store_true",
                        help="Only output properties with is_wholesale_opportunity=True")

    # Browser / scraper options
    parser.add_argument("--headless",  action="store_true")
    parser.add_argument("--delay",     type=float, default=1.5, metavar="SEC")
    parser.add_argument("--timeout",   type=int,   default=30_000, metavar="MS")
    parser.add_argument("--slow-mo",   type=int,   default=0, metavar="MS")
    parser.add_argument("--save-html", metavar="DIR", type=Path)

    # Parallel worker options
    parser.add_argument("--workers",  type=int, default=1, metavar="N",
                        help="Number of parallel browser workers (default: 1)")
    parser.add_argument("--log-dir",  metavar="DIR", type=Path,
                        help="Directory for per-worker log files")

    args = parser.parse_args(argv)
    out_path = Path(args.out) if args.out else None

    # ── Pre-score: score all 419k rows with no browser ──────────────────────
    if args.pre_score:
        from research.prescore import run_prescore
        summary = run_prescore()
        print("\n[prescore] Complete. Summary:")
        for label, count in summary.items():
            print(f"  {label:<35} {count:>8,}")
        return

    # ── Export pre-scores ────────────────────────────────────────────────────
    if args.export_scores:
        from research.prescore import export_prescores
        rows = export_prescores(
            min_score=args.min_score,
            category=args.category,
            limit=args.limit if hasattr(args, "limit") else None,
        )
        fmt = out_path.suffix.lower() if out_path else ".csv"
        if fmt == ".json":
            text = json.dumps(rows, indent=2, default=str)
            if out_path:
                out_path.write_text(text, encoding="utf-8")
                print(f"[research] {len(rows):,} row(s) → {out_path}", file=sys.stderr)
            else:
                print(text)
        else:
            _write_csv(rows, out_path)
            if out_path:
                print(f"[research] {len(rows):,} row(s) → {out_path}", file=sys.stderr)
        return

    # ── Special --db modes that don't run the pipeline ─────────────────────
    if args.db and args.count_only:
        from research.db import count_candidates
        n = count_candidates(
            absentee_only=args.absentee_only,
            min_years_owned=args.min_years,
            old_property_only=args.old_property,
            out_of_state_only=args.out_of_state,
            skip_researched=not args.rerun,
        )
        print(f"{n} candidate(s) match the filters.")
        return

    if args.db and args.export_opportunities:
        from research.db import load_opportunities
        rows = load_opportunities()
        fmt = out_path.suffix.lower() if out_path else ".csv"
        if fmt == ".json":
            text = json.dumps(rows, indent=2, default=str)
            if out_path:
                out_path.write_text(text, encoding="utf-8")
                print(f"[research] {len(rows)} opportunity record(s) → {out_path}", file=sys.stderr)
            else:
                print(text)
        else:
            _write_csv(rows, out_path)
        return

    # ── Load folios ─────────────────────────────────────────────────────────
    if args.db:
        folios = _load_db(args)
        print(
            f"[research] {len(folios)} candidate(s) loaded from DB "
            f"(absentee={args.absentee_only} old={args.old_property} "
            f"min_years={args.min_years} limit={args.limit})",
            file=sys.stderr,
        )
    elif args.file:
        folios = _load_file(Path(args.file))
    elif args.csv:
        folios = _load_csv(Path(args.csv), args.folio_col)
    elif args.folio:
        folios = args.folio
    else:
        parser.print_help()
        sys.exit(1)

    if not folios:
        print("No folios to research.", file=sys.stderr)
        sys.exit(1)

    # ── Run pipeline ─────────────────────────────────────────────────────────
    n_workers = args.workers if hasattr(args, "workers") else 1
    log_dir   = args.log_dir  if hasattr(args, "log_dir")  else None

    if n_workers > 1 or (hasattr(args, "db") and args.db):
        # Fast persistent-browser path: one browser per worker, results saved
        # to DB after every folio (crash-safe), no in-memory report list.
        from research.db import DB_PATH
        from research.worker import run_workers
        run_workers(
            folios,
            n_workers=n_workers,
            headless=args.headless,
            timeout_ms=args.timeout,
            db_path=DB_PATH,
            log_dir=log_dir,
        )
        # After the workers finish, export from DB if an output file was requested.
        if out_path:
            from research.db import load_opportunities
            rows = load_opportunities()
            fmt = out_path.suffix.lower()
            if fmt == ".json":
                import json as _json
                out_path.write_text(_json.dumps(rows, indent=2, default=str), encoding="utf-8")
            else:
                _write_csv(rows, out_path)
        return

    # ── Single-worker interactive path (small batches, no --db flag) ─────────
    pipeline = ResearchPipeline(
        headless=args.headless,
        timeout_ms=args.timeout,
        delay_s=args.delay,
        slow_mo_ms=args.slow_mo,
        save_html_dir=args.save_html,
    )

    import time
    reports: list[ResearchReport] = []
    for i, folio in enumerate(folios, start=1):
        print(
            f"\n[research] {'═'*50}\n"
            f"[research]  {i}/{len(folios)}  Folio: {folio}\n"
            f"[research] {'═'*50}",
            file=sys.stderr, flush=True,
        )
        try:
            report = pipeline.research_one(folio, index=i)
            reports.append(report)
        except Exception as exc:
            print(f"[research]   ERROR: {exc}", file=sys.stderr, flush=True)

        if i < len(folios):
            time.sleep(args.delay)

    # ── Output ───────────────────────────────────────────────────────────────
    if args.opportunities_only:
        reports = [r for r in reports if r.is_wholesale_opportunity]

    fmt = out_path.suffix.lower() if out_path else ".json"
    if fmt == ".csv":
        _write_csv([_flat_dict(r) for r in reports], out_path)
    else:
        _write_json(reports, out_path)

    opps = sum(1 for r in reports if r.is_wholesale_opportunity)
    print(
        f"\n[research] Done — {len(folios)} researched, "
        f"{opps} wholesale opportunit{'y' if opps == 1 else 'ies'}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
