from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GeocodeStatus = Literal[
    "OK",
    "ZERO_RESULTS",
    "OVER_QUERY_LIMIT",
    "REQUEST_DENIED",
    "INVALID_REQUEST",
    "UNKNOWN_ERROR",
    "CACHED",
    "SKIPPED_EMPTY_ADDRESS",
]


@dataclass(frozen=True)
class GeocodeResult:
    """Single geocode outcome for a property address."""

    cache_key: str
    lat: float | None
    lng: float | None
    formatted_address: str | None
    location_type: str | None
    partial_match: bool
    api_status: GeocodeStatus
    confidence: int | None
    """0–100 heuristic from location_type and partial_match; None if not geocoded."""

    @property
    def ok(self) -> bool:
        return self.lat is not None and self.lng is not None and self.api_status in (
            "OK",
            "CACHED",
        )
