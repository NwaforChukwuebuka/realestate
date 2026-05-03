from __future__ import annotations

import math

# Order matches plan: base, +15, -15, +30, -30 (degrees relative to pano→property bearing).
HEADING_OFFSETS_DEG: tuple[int, ...] = (0, 15, -15, 30, -30)


def normalize_heading_degrees(heading: float) -> float:
    """Compass heading in [0, 360)."""
    h = heading % 360.0
    if h < 0:
        h += 360.0
    return h


def initial_bearing_degrees(
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
) -> float:
    """
    Initial (forward) azimuth from the first point to the second, in degrees
    clockwise from north (0–360), matching Google Street View `heading`.
    """
    φ1 = math.radians(from_lat)
    φ2 = math.radians(to_lat)
    Δλ = math.radians(to_lng - from_lng)

    y = math.sin(Δλ) * math.cos(φ2)
    x = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(Δλ)
    θ = math.atan2(y, x)
    return normalize_heading_degrees(math.degrees(θ))


def headings_for_offsets(base_heading_deg: float) -> list[tuple[int, float]]:
    """Return (offset, normalized_heading) for each planned capture angle."""
    return [
        (off, normalize_heading_degrees(base_heading_deg + off))
        for off in HEADING_OFFSETS_DEG
    ]
