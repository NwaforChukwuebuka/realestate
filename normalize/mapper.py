from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

from normalize.models import NormalizedProperty

# Miami-Dade municipal extract omits situs state; this file is county-wide FL.
DEFAULT_PROPERTY_STATE = "FL"

_MUNROLL_ZIP_KEYS = (" Property Zip", "Property Zip")
_MUNROLL_LAND_USE = "Land Use"


def _s(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _pick(raw: Mapping[str, object | None], *keys: str) -> str:
    for k in keys:
        if k in raw:
            v = _s(raw.get(k))
            if v:
                return v
    return ""


def _parse_md_yyyy_mm_dd(value: object | None) -> str | None:
    text = _s(value)
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_int(value: object | None) -> int | None:
    t = _s(value).replace(",", "")
    if not t:
        return None
    try:
        n = int(t)
    except ValueError:
        return None
    return n


def _parse_year_built(value: object | None) -> int | None:
    n = _parse_int(value)
    if n is None or n <= 0:
        return None
    return n


def _owner_name(raw: Mapping[str, object | None]) -> str | None:
    parts = [_s(raw.get("Owner1")), _s(raw.get("Owner2"))]
    parts = [p for p in parts if p]
    if not parts:
        return None
    return " | ".join(parts)


def format_address_line(
    street: str | None,
    city: str | None,
    state: str | None,
    zip_code: str | None,
) -> str | None:
    """Format situs or mailing parts into one line (same rules as MunRoll mailing export)."""
    street_s = _s(street)
    city_s = _s(city)
    state_s = _s(state)
    z = _s(zip_code)
    tail_parts: list[str] = []
    if city_s or state_s or z:
        mid = ", ".join(p for p in (city_s, state_s) if p)
        if mid and z:
            tail = f"{mid} {z}".strip()
        elif mid:
            tail = mid
        else:
            tail = z
        if tail:
            tail_parts.append(tail)
    if street_s and tail_parts:
        return f"{street_s}, {tail_parts[0]}"
    if street_s:
        return street_s
    if tail_parts:
        return tail_parts[0]
    return None


def _mailing_one_line(raw: Mapping[str, object | None]) -> str | None:
    return format_address_line(
        _pick(raw, "Mailing Address"),
        _pick(raw, "Mailing City"),
        _pick(raw, "Mailing State"),
        _pick(raw, "Mailing Zip"),
    )


def _last_sale_date_iso(raw: Mapping[str, object | None]) -> str | None:
    """Prefer ``Sale Date 1`` (most recent), then 2, then 3."""
    for key in ("Sale Date 1", "Sale Date 2", "Sale Date 3"):
        iso = _parse_md_yyyy_mm_dd(raw.get(key))
        if iso:
            return iso
    return None


def map_munroll_record(raw: Mapping[str, object | None]) -> NormalizedProperty:
    """Map a MunRoll ``DictReader``/JSON row to :class:`NormalizedProperty`."""
    folio = _pick(raw, "Folio")
    if not folio:
        msg = "MunRoll row missing Folio (parcel_id)"
        raise ValueError(msg)

    z = _pick(raw, *_MUNROLL_ZIP_KEYS)
    land_use = _s(raw.get(_MUNROLL_LAND_USE)) or None

    return NormalizedProperty(
        parcel_id=folio,
        property_address=_pick(raw, "Property Address") or None,
        city=_pick(raw, "Property City") or None,
        state=DEFAULT_PROPERTY_STATE,
        zip=z or None,
        owner_name=_owner_name(raw),
        mailing_address=_mailing_one_line(raw),
        property_type=land_use,
        year_built=_parse_year_built(raw.get("YearBuilt")),
        last_sale_date=_last_sale_date_iso(raw),
        assessed_value=_parse_int(raw.get("Assessed")),
    )


def map_munroll_row_json(row_json: str) -> NormalizedProperty:
    """Parse ``row_json`` from ``munroll_raw`` and map it."""
    data = json.loads(row_json)
    if not isinstance(data, dict):
        msg = "row_json must decode to an object"
        raise TypeError(msg)
    return map_munroll_record(data)
