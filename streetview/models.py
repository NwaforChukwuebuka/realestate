from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StreetViewMetadataStatus = Literal[
    "OK",
    "ZERO_RESULTS",
    "NOT_FOUND",
    "OVER_QUERY_LIMIT",
    "REQUEST_DENIED",
    "INVALID_REQUEST",
    "UNKNOWN_ERROR",
    "CACHED",
    "SKIPPED_NO_COORDINATES",
]


@dataclass(frozen=True)
class StreetViewMetadataResult:
    """Outcome of a Street View Static metadata lookup for a lat/lng."""

    cache_key: str
    query_lat: float | None
    query_lng: float | None
    pano_id: str | None
    pano_lat: float | None
    pano_lng: float | None
    image_date: str | None
    """Capture month from API when present (often YYYY-MM; may be year-only)."""
    api_status: StreetViewMetadataStatus

    @property
    def no_street_view(self) -> bool:
        if self.api_status in ("ZERO_RESULTS", "NOT_FOUND"):
            return True
        # Cache hits reuse api_status "CACHED"; absence of pano means prior ZERO_RESULTS/NOT_FOUND.
        if self.api_status == "CACHED":
            return self.pano_id is None
        return False

    @property
    def ok(self) -> bool:
        return (
            self.pano_id is not None
            and self.pano_lat is not None
            and self.pano_lng is not None
            and self.api_status in ("OK", "CACHED")
        )
