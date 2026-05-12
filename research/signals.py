"""
Combo signal evaluation.

Each rule maps one project-playbook combination (e.g. "LIS + high equity")
to one or more lead categories. A rule fires when ALL of its conditions are
met from the merged PA / RER / Official-Records data.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from official_records.scraper import OfficialRecord
from property_appraiser.scraper import PropertyRecord
from regulation_cases.scraper import RegulationCase

_CURRENT_YEAR = date.today().year

# ---------------------------------------------------------------------------
# Primitive checks
# ---------------------------------------------------------------------------

def _sale_year(pa: Optional[PropertyRecord]) -> Optional[int]:
    if not pa or not pa.previous_sale_date:
        return None
    m = re.search(r"\d{4}", pa.previous_sale_date)
    return int(m.group()) if m else None


def _years_owned(pa: Optional[PropertyRecord]) -> int:
    yr = _sale_year(pa)
    return (_CURRENT_YEAR - yr) if yr else 0


def _long_ownership(pa: Optional[PropertyRecord], min_years: int = 10) -> bool:
    return _years_owned(pa) >= min_years


def _high_equity(pa: Optional[PropertyRecord]) -> bool:
    """Proxy: owned ≥ 10 years → likely significant equity build-up."""
    return _long_ownership(pa, 10)


def _absentee(pa: Optional[PropertyRecord]) -> bool:
    return bool(pa and pa.absentee_owner)


def _has_doc(*codes: str) -> "Callable[[list[OfficialRecord]], bool]":
    code_set = {c.upper() for c in codes}
    def _check(records: list[OfficialRecord]) -> bool:
        return any(
            r.doc_type_code and r.doc_type_code.upper() in code_set
            for r in records
        )
    return _check


def _has_violation(cases: list[RegulationCase]) -> bool:
    return bool(cases)


def _has_case_matching(pattern: str) -> "Callable[[list[RegulationCase]], bool]":
    pat = re.compile(pattern, re.IGNORECASE)
    def _check(cases: list[RegulationCase]) -> bool:
        return any(pat.search(c.case_type) for c in cases)
    return _check


def _has_lien(records: list[OfficialRecord]) -> bool:
    return _has_doc("LIE", "FTL", "NTL", "LNJUD", "LIS")(records)


# Pre-compile reusable checkers
_unsafe_structure  = _has_case_matching(r"unsafe\s+structure")
_abandoned_vacant  = _has_case_matching(r"abandoned|vacant")

# ---------------------------------------------------------------------------
# Signal rules
# ---------------------------------------------------------------------------
# Each entry:
#   name        — display label
#   check(pa, rer, or_) → bool
#   categories  — which lead categories this signal contributes to

SIGNAL_RULES: list[dict] = [
    {
        "name": "PAD + absentee owner",
        "check": lambda pa, rer, or_: _has_doc("PAD")(or_) and _absentee(pa),
        "categories": ["inherited_properties", "motivated_sellers"],
    },
    {
        "name": "LIS + high equity",
        "check": lambda pa, rer, or_: _has_doc("LIS")(or_) and _high_equity(pa),
        "categories": ["financial_distress", "motivated_sellers"],
    },
    {
        "name": "FTL + distressed property",
        "check": lambda pa, rer, or_: _has_doc("FTL")(or_) and _has_violation(rer),
        "categories": ["financial_distress"],
    },
    {
        "name": "DOM + long ownership",
        "check": lambda pa, rer, or_: _has_doc("DOM")(or_) and _long_ownership(pa, 10),
        "categories": ["motivated_sellers", "distressed_ownership"],
    },
    {
        "name": "QCD + vacant property",
        "check": lambda pa, rer, or_: _has_doc("QCD")(or_) and _abandoned_vacant(rer),
        "categories": ["potential_wholesale_opportunity"],
    },
    {
        "name": "BAN + code violations",
        "check": lambda pa, rer, or_: _has_doc("BAN")(or_) and _has_violation(rer),
        "categories": ["financial_distress", "distressed_ownership"],
    },
    {
        "name": "NTL + distressed property",
        "check": lambda pa, rer, or_: _has_doc("NTL")(or_) and _has_violation(rer),
        "categories": ["financial_distress"],
    },
    {
        "name": "JUD + absentee owner",
        "check": lambda pa, rer, or_: _has_doc("JUD", "LNJUD")(or_) and _absentee(pa),
        "categories": ["legal_pressure", "motivated_sellers"],
    },
    {
        "name": "CVP + neglected property",
        "check": lambda pa, rer, or_: _has_doc("CVP")(or_) and _has_violation(rer),
        "categories": ["legal_pressure"],
    },
    {
        "name": "Code violations + absentee owner",
        "check": lambda pa, rer, or_: _has_violation(rer) and _absentee(pa),
        "categories": ["distressed_ownership", "potential_wholesale_opportunity"],
    },
    {
        "name": "Unsafe structure + long ownership",
        "check": lambda pa, rer, or_: _unsafe_structure(rer) and _long_ownership(pa, 10),
        "categories": ["distressed_ownership", "motivated_sellers"],
    },
    {
        "name": "Liens + distressed property",
        "check": lambda pa, rer, or_: _has_lien(or_) and _has_violation(rer),
        "categories": ["financial_distress", "potential_wholesale_opportunity"],
    },
]

MAX_SCORE = len(SIGNAL_RULES)


def evaluate(
    pa: Optional[PropertyRecord],
    rer: list[RegulationCase],
    official: list[OfficialRecord],
) -> tuple[list[str], list[str]]:
    """
    Run every signal rule and return (triggered_signal_names, sorted_categories).
    """
    triggered: list[str] = []
    categories: set[str] = set()

    for rule in SIGNAL_RULES:
        try:
            if rule["check"](pa, rer, official):
                triggered.append(rule["name"])
                categories.update(rule["categories"])
        except Exception:
            pass

    return triggered, sorted(categories)
