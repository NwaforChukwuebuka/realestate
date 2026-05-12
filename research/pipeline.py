"""
Research pipeline: runs all three scrapers for a folio and merges the results.

Flow
----
1. Property Appraiser  (folio)                    → ownership, absentee, sale date
2. Regulation Cases    folio → address → owner    → code enforcement cases
3. Official Records    (property address from 1)  → deed / lien / probate records
4. Signal evaluation                              → combos, categories, lead score
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Optional

from official_records.scraper import OfficialRecord, OfficialRecordsScraper
from property_appraiser.scraper import PropertyAppraiserScraper, PropertyRecord
from regulation_cases.scraper import RegulationCase, RegulationCasesScraper

from research.signals import MAX_SCORE, evaluate

_OPPORTUNITY_THRESHOLD = 2
_W = 62  # report width

CATEGORY_LABELS = {
    "motivated_sellers":               "Motivated Sellers",
    "financial_distress":              "Financial Distress",
    "inherited_properties":            "Inherited / Probate",
    "legal_pressure":                  "Legal Pressure",
    "distressed_ownership":            "Distressed Ownership",
    "potential_wholesale_opportunity": "Wholesale Opportunity",
}


@dataclass
class ResearchReport:
    folio: str
    property_record: Optional[PropertyRecord]
    regulation_cases: list[RegulationCase]
    rer_search_method: str                 # "folio" | "address" | "owner" | "none"
    official_records: list[OfficialRecord]
    triggered_signals: list[str]
    categories: list[str]
    lead_score: int
    is_wholesale_opportunity: bool
    summary: str

    def to_dict(self) -> dict:
        pa = self.property_record
        return {
            "folio":  self.folio,
            "property": {
                "address":        pa.property_address if pa else "",
                "owner":          pa.owner_name if pa else "",
                "mailing":        pa.mailing_address if pa else "",
                "subdivision":    pa.subdivision if pa else "",
                "year_built":     pa.year_built if pa else "",
                "last_sale_date": pa.previous_sale_date if pa else "",
                "last_sale_price":pa.previous_sale_price if pa else "",
                "absentee_owner": pa.absentee_owner if pa else None,
                "trust_or_llc":   pa.trust_or_llc_owner if pa else None,
                "quality_lead":   pa.quality_lead if pa else None,
            },
            "regulation_cases": [
                {
                    "case_number": c.case_number,
                    "case_type":   c.case_type,
                    "address":     c.address,
                    "owner":       c.owner_name,
                    "violator":    c.violator,
                    "folio":       c.folio_number,
                    "strong_lead": c.is_strong_lead,
                }
                for c in self.regulation_cases
            ],
            "rer_found_by": self.rer_search_method,
            "official_records": _group_official_records(self.official_records),
            "triggered_signals":       self.triggered_signals,
            "categories":              self.categories,
            "lead_score":              self.lead_score,
            "max_score":               MAX_SCORE,
            "is_wholesale_opportunity":self.is_wholesale_opportunity,
            "summary":                 self.summary,
        }


# ---------------------------------------------------------------------------
# Official-records grouping
# ---------------------------------------------------------------------------

def _group_official_records(records: list[OfficialRecord]) -> list[dict]:
    """Collapse repeated doc types into one entry per code."""
    buckets: dict[str, list[OfficialRecord]] = defaultdict(list)
    for r in records:
        buckets[r.doc_type_code or "?"].append(r)

    grouped = []
    for code, recs in sorted(buckets.items(), key=lambda x: -len(x[1])):
        dates = sorted(r.recorded_date for r in recs if r.recorded_date)
        if len(dates) > 1:
            date_range = f"{dates[0]} – {dates[-1]}"
        elif dates:
            date_range = dates[0]
        else:
            date_range = ""

        raw_label = recs[0].doc_type_label or code
        label = raw_label.split(" - ")[0].title() if " - " in raw_label else raw_label.title()
        parties = recs[0].party_names or ""

        grouped.append({
            "code":       code,
            "count":      len(recs),
            "label":      label,
            "date_range": date_range,
            "parties":    parties,
        })
    return grouped


# ---------------------------------------------------------------------------
# Report card formatter
# ---------------------------------------------------------------------------

def _build_summary(
    folio: str,
    pa: Optional[PropertyRecord],
    rer: list[RegulationCase],
    rer_method: str,
    official: list[OfficialRecord],
    signals: list[str],
    categories: list[str],
    score: int,
    is_opportunity: bool,
) -> str:
    thick = "═" * _W
    thin  = "─" * (_W - 2)
    lines: list[str] = []

    # Header
    lines += [thick, f"  FOLIO:   {folio}"]
    if pa:
        lines.append(f"  ADDRESS: {pa.property_address}")
        entity = "  [Trust / LLC]" if pa.trust_or_llc_owner else ""
        lines.append(f"  OWNER:   {pa.owner_name}{entity}")
        lines.append(f"  MAILING: {pa.mailing_address}")
    lines += [thick, ""]

    # Ownership signals
    lines += ["  OWNERSHIP", f"  {thin[:35]}"]
    if pa:
        lines.append(f"  Absentee Owner  : {'YES ← mailing differs from property' if pa.absentee_owner else 'no'}")
        lines.append(f"  Quality Lead    : {'YES ← individual non-entity owner' if pa.quality_lead else 'no'}")
        lines.append(f"  Year Built      : {pa.year_built or '—'}")
        lines.append(f"  Last Sale       : {pa.previous_sale_date or '—'}  {pa.previous_sale_price or ''}")
    else:
        lines.append("  PA data not available")
    lines.append("")

    # Lead score
    bar = "█" * score + "░" * (MAX_SCORE - score)
    opp = "  ★ WHOLESALE OPPORTUNITY" if is_opportunity else ""
    lines += ["  LEAD ASSESSMENT", f"  {thin[:35]}"]
    lines.append(f"  Score      : {score}/{MAX_SCORE}  [{bar}]{opp}")
    if categories:
        cat_labels = "  ·  ".join(CATEGORY_LABELS.get(c, c) for c in categories)
        lines.append(f"  Categories : {cat_labels}")
    if signals:
        lines.append("  Signals    :")
        for sig in signals:
            lines.append(f"    ✓  {sig}")
    else:
        lines.append("  Signals    : none triggered")
    lines.append("")

    # Regulation cases
    method_tag = f"found via {rer_method}" if rer_method != "none" else "not found"
    lines += [f"  CODE ENFORCEMENT  ({len(rer)} case(s) — {method_tag})", f"  {thin[:50]}"]
    if rer:
        for c in rer:
            star = "★ " if c.is_strong_lead else "  "
            lines.append(f"  {star}{c.case_number:<14}  {c.case_type}")
    else:
        lines.append("  none found")
    lines.append("")

    # Official records — grouped
    lines += [f"  OFFICIAL RECORDS  ({len(official)} document(s))", f"  {thin[:50]}"]
    if official:
        for g in _group_official_records(official):
            count_str = f"×{g['count']}" if g["count"] > 1 else "  "
            label = g["label"][:22].ljust(22)
            dr    = g["date_range"][:20].ljust(20)
            pty   = g["parties"][:38]
            lines.append(f"  {g['code']:<5} {count_str:<4} {label}  {dr}  {pty}")
    else:
        lines.append("  none found")

    lines.append(thick)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class ResearchPipeline:
    """
    Runs Property Appraiser → Regulation Cases (with folio/address/owner fallback)
    → Official Records for each folio and returns a ``ResearchReport``.
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        timeout_ms: int = 30_000,
        delay_s: float = 1.5,
        slow_mo_ms: int = 0,
        save_html_dir=None,
    ) -> None:
        self.headless     = headless
        self.timeout_ms   = timeout_ms
        self.delay_s      = delay_s
        self.slow_mo_ms   = slow_mo_ms
        self.save_html_dir = save_html_dir

    def _html_subdir(self, name: str):
        if self.save_html_dir is None:
            return None
        from pathlib import Path
        d = Path(self.save_html_dir) / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _rer_run(self, search_by: str, values: list[str]) -> list[RegulationCase]:
        scraper = RegulationCasesScraper(
            headless=self.headless,
            timeout_ms=self.timeout_ms,
            slow_mo_ms=self.slow_mo_ms,
            search_by=search_by,
            all_records=True,
            results_html_dir=self._html_subdir("regulation_cases"),
        )
        return scraper.run(values)

    def research_one(self, folio: str, index: int = 1) -> ResearchReport:
        common = dict(
            headless=self.headless,
            timeout_ms=self.timeout_ms,
            slow_mo_ms=self.slow_mo_ms,
        )

        # ── Step 1: Property Appraiser ──────────────────────────────────────
        print(f"\n[research] 1/3  Property Appraiser", file=sys.stderr, flush=True)
        pa_scraper = PropertyAppraiserScraper(
            **common,
            results_html_dir=self._html_subdir("property_appraiser"),
        )
        pa_records = pa_scraper.run([folio])
        pa = pa_records[0] if pa_records else None
        property_address = pa.property_address if pa else ""
        owner_name       = pa.owner_name       if pa else ""

        # ── Step 2: Regulation Cases (folio → address → owner) ──────────────
        print(f"\n[research] 2/3  Regulation Cases", file=sys.stderr, flush=True)
        rer_cases: list[RegulationCase] = []
        rer_method = "none"

        rer_cases = self._rer_run("folio", [folio])
        if rer_cases:
            rer_method = "folio"
        elif property_address:
            print(f"[research]   folio returned 0 — retrying by address", file=sys.stderr, flush=True)
            rer_cases = self._rer_run("address", [property_address])
            if rer_cases:
                rer_method = "address"

        if not rer_cases and owner_name:
            print(f"[research]   address returned 0 — retrying by owner name", file=sys.stderr, flush=True)
            rer_cases = self._rer_run("owner", [owner_name])
            if rer_cases:
                rer_method = "owner"

        # ── Step 3: Official Records ─────────────────────────────────────────
        print(f"\n[research] 3/3  Official Records", file=sys.stderr, flush=True)
        official: list[OfficialRecord] = []
        if property_address:
            or_scraper = OfficialRecordsScraper(
                **common,
                all_records=True,
                results_html_dir=self._html_subdir("official_records"),
            )
            official = or_scraper.run([property_address])
        else:
            print("[research]   skipping — no address available", file=sys.stderr, flush=True)

        # ── Step 4: Signal evaluation ────────────────────────────────────────
        signals, categories = evaluate(pa, rer_cases, official)
        score = len(signals)
        is_opportunity = score >= _OPPORTUNITY_THRESHOLD

        summary = _build_summary(
            folio, pa, rer_cases, rer_method, official,
            signals, categories, score, is_opportunity,
        )

        print(f"\n{summary}", file=sys.stderr, flush=True)

        return ResearchReport(
            folio=folio,
            property_record=pa,
            regulation_cases=rer_cases,
            rer_search_method=rer_method,
            official_records=official,
            triggered_signals=signals,
            categories=categories,
            lead_score=score,
            is_wholesale_opportunity=is_opportunity,
            summary=summary,
        )

    def run(self, folios: list[str]) -> list[ResearchReport]:
        folios = [f.strip() for f in folios if f.strip()]
        reports: list[ResearchReport] = []
        total = len(folios)

        for i, folio in enumerate(folios, start=1):
            print(
                f"\n[research] {'═'*50}\n"
                f"[research]  {i}/{total}  Folio: {folio}\n"
                f"[research] {'═'*50}",
                file=sys.stderr, flush=True,
            )
            try:
                reports.append(self.research_one(folio, index=i))
            except Exception as exc:
                print(f"[research]   ERROR: {exc}", file=sys.stderr, flush=True)
            if i < total:
                time.sleep(self.delay_s)

        opps = sum(1 for r in reports if r.is_wholesale_opportunity)
        print(
            f"\n[research] Done — {total} folio(s), "
            f"{opps} wholesale opportunit{'y' if opps == 1 else 'ies'}.",
            file=sys.stderr, flush=True,
        )
        return reports
