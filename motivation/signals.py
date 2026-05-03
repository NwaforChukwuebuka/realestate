from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date

from normalize.mapper import format_address_line
from normalize.models import NormalizedProperty

_ZIP_PLUS_FOUR = re.compile(r"(\d{5})-\d{4}\b")

# Two-letter situs/mailing codes we treat as a parsed state (avoids "NO" from "NO STATE HERE").
_USPS_STATE_CODES: frozenset[str] = frozenset(
    {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "DC",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "PR",
        "VI",
        "GU",
        "AS",
        "MP",
    }
)


@dataclass(frozen=True)
class MotivationSignals:
    """Derived lead signals from normalized MunRoll fields (plan step 4)."""

    absentee_owner: bool | None
    out_of_state_owner: bool | None
    years_owned: int | None
    old_property: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _normalize_address_compare(text: str) -> str:
    t = " ".join(text.upper().split())
    return _ZIP_PLUS_FOUR.sub(r"\1", t)


def situs_one_line(prop: NormalizedProperty) -> str | None:
    """Single-line situs formatted like :func:`normalize.mapper.format_address_line` mailing."""
    return format_address_line(
        prop.property_address,
        prop.city,
        prop.state,
        prop.zip,
    )


def mailing_state_from_line(mailing_one_line: str | None) -> str | None:
    """Best-effort two-letter state from a comma-separated mailing one-liner."""
    if mailing_one_line is None:
        return None
    s = mailing_one_line.strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        return None
    last = parts[-1]
    tokens = last.split()
    if not tokens:
        return None
    head = tokens[0].upper()
    if len(head) != 2 or not head.isalpha() or head not in _USPS_STATE_CODES:
        return None
    if len(tokens) >= 2 and tokens[1][:1].isdigit():
        return head
    if len(tokens) == 1:
        return head
    return None


def compute_motivation_signals(
    prop: NormalizedProperty,
    *,
    as_of_year: int | None = None,
) -> MotivationSignals:
    """Compute motivation flags for one normalized property."""
    ref_year = as_of_year if as_of_year is not None else date.today().year

    situs = situs_one_line(prop)
    mail = prop.mailing_address
    if situs is None or mail is None:
        absentee: bool | None = None
    else:
        absentee = _normalize_address_compare(situs) != _normalize_address_compare(mail)

    m_state = mailing_state_from_line(mail)
    if m_state is None:
        oos: bool | None = None
    else:
        oos = m_state != "FL"

    years: int | None = None
    if prop.last_sale_date:
        try:
            sale_year = int(prop.last_sale_date[:4])
        except ValueError:
            years = None
        else:
            years = max(0, ref_year - sale_year)

    old = prop.year_built is not None and prop.year_built <= 1980

    return MotivationSignals(
        absentee_owner=absentee,
        out_of_state_owner=oos,
        years_owned=years,
        old_property=old,
    )
