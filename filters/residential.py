from __future__ import annotations

# Miami-Dade ``Land Use`` (mapped to ``NormalizedProperty.property_type``) codes we
# keep for the street-view pipeline: single-family, duplex / 2–9 unit multifamily,
# and improved-agricultural rows that are still dwelling-scale (plan step 3).
_ALLOWED_LAND_USE_CODES: frozenset[str] = frozenset(
    {
        "0100",  # legacy / extract variants
        "0101",
        "0102",
        "0103",
        "0104",
        "0105",
        "0176",
        "0802",
        "0803",
        "5001",
        "5002",
    }
)

# Substrings that disqualify a row even when a code might look residential (condos,
# government / institutional ownership in the land-use label, vacant land, etc.).
_REJECTION_MARKERS: tuple[str, ...] = (
    "CONDOMINIUM",
    "COUNTY :",
    "MUNICIPAL :",
    "STATE :",
    "FEDERAL :",
    "VACANT GOVERNMENTAL",
    "SCHOOL BOARD",
    "MILITARY :",
    "HOSPITAL - GOVERNMENTAL",
    "PENAL INSTITUTION",
    "REFERENCE FOLIO",
    "VACANT LAND",
)


def _land_use_code(land_use: str) -> str | None:
    """Return the leading four-digit Miami-Dade land-use code when present."""
    s = land_use.strip()
    if len(s) >= 4 and s[:4].isdigit():
        return s[:4]
    head, _, _ = s.partition(" - ")
    head = head.strip()
    if len(head) == 4 and head.isdigit():
        return head
    return None


def is_target_residential(property_type: str | None) -> bool:
    """Return True when ``property_type`` (MunRoll ``Land Use``) is in-scope residential.

    Keeps single-family, duplex / two-unit, and small (2–9 unit) multifamily codes
    used on the Miami-Dade roll. Drops condos, commercial / industrial / vacant land,
    government and institutional land-use buckets, and everything outside the
    explicit allowlist.
    """
    if property_type is None:
        return False
    raw = property_type.strip()
    if not raw:
        return False

    upper = raw.upper()
    if any(marker in upper for marker in _REJECTION_MARKERS):
        return False

    code = _land_use_code(raw)
    if code is None:
        return False
    return code in _ALLOWED_LAND_USE_CODES


def normalized_property_is_target(prop: object) -> bool:
    """True when ``prop.property_type`` passes :func:`is_target_residential` (e.g. ``NormalizedProperty``)."""
    pt = getattr(prop, "property_type", None)
    if pt is None or isinstance(pt, str):
        return is_target_residential(pt)
    return is_target_residential(str(pt))
