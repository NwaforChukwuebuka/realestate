from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PipelineStatus = Literal["pending", "done", "failed", "skipped_no_street_view"]


PIPELINE_STATUS_VALUES: frozenset[str] = frozenset(
    {"pending", "done", "failed", "skipped_no_street_view"},
)


def is_pipeline_status(value: str) -> bool:
    return value in PIPELINE_STATUS_VALUES


@dataclass(frozen=True)
class PendingParcelWork:
    """One ``pending`` pipeline row joined to normalized situs fields."""

    parcel_id: str
    property_address: str | None
    city: str | None
    state: str | None
    zip: str | None


@dataclass(frozen=True)
class PipelineRow:
    """Snapshot of one ``property_pipeline`` row for reads."""

    parcel_id: str
    pipeline_status: PipelineStatus
    geocode_cache_key: str | None
    geocode_lat: float | None
    geocode_lng: float | None
    geocode_formatted_address: str | None
    geocode_location_type: str | None
    geocode_partial_match: bool | None
    geocode_api_status: str | None
    geocode_confidence: int | None
    sv_cache_key: str | None
    sv_query_lat: float | None
    sv_query_lng: float | None
    sv_pano_id: str | None
    sv_pano_lat: float | None
    sv_pano_lng: float | None
    sv_image_date: str | None
    sv_api_status: str | None
    image_paths_json: str | None
    verification_json: str | None
    last_error: str | None
    updated_at: str | None
