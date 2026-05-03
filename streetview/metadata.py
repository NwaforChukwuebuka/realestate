from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from streetview.cache import StreetViewMetadataCache
from streetview.models import StreetViewMetadataResult, StreetViewMetadataStatus

GOOGLE_STREETVIEW_METADATA_URL = (
    "https://maps.googleapis.com/maps/api/streetview/metadata"
)


def normalize_coord_cache_key(lat: float, lng: float) -> str:
    """Stable key for dedupe + cache (~1 cm precision)."""
    return f"{lat:.7f}|{lng:.7f}"


class StreetViewMetadataClient:
    def __init__(
        self,
        api_key: str,
        cache_db_path: str | Path | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()
        self._cache: StreetViewMetadataCache | None = (
            StreetViewMetadataCache(cache_db_path) if cache_db_path else None
        )

    def close(self) -> None:
        if self._cache:
            self._cache.close()

    def lookup(self, lat: float | None, lng: float | None) -> StreetViewMetadataResult:
        if lat is None or lng is None:
            return StreetViewMetadataResult(
                cache_key="",
                query_lat=lat,
                query_lng=lng,
                pano_id=None,
                pano_lat=None,
                pano_lng=None,
                image_date=None,
                api_status="SKIPPED_NO_COORDINATES",
            )

        cache_key = normalize_coord_cache_key(lat, lng)

        if self._cache:
            row = self._cache.get(cache_key)
            if row is not None:
                return StreetViewMetadataResult(
                    cache_key=cache_key,
                    query_lat=row["query_lat"],
                    query_lng=row["query_lng"],
                    pano_id=row["pano_id"],
                    pano_lat=row["pano_lat"],
                    pano_lng=row["pano_lng"],
                    image_date=row["image_date"],
                    api_status="CACHED",
                )

        location = f"{lat},{lng}"
        params: dict[str, str] = {"location": location, "key": self._api_key}
        r = self._session.get(GOOGLE_STREETVIEW_METADATA_URL, params=params, timeout=30)
        r.raise_for_status()
        payload: dict[str, Any] = r.json()
        status = str(payload.get("status", "UNKNOWN_ERROR"))
        typed_status: StreetViewMetadataStatus = (
            status if status in _TERMINAL_STATUSES else "UNKNOWN_ERROR"
        )

        if typed_status != "OK":
            result = StreetViewMetadataResult(
                cache_key=cache_key,
                query_lat=lat,
                query_lng=lng,
                pano_id=None,
                pano_lat=None,
                pano_lng=None,
                image_date=None,
                api_status=typed_status,
            )
            if self._cache and typed_status in _CACHEABLE_FAILURES:
                self._cache.set(
                    cache_key,
                    lat,
                    lng,
                    None,
                    None,
                    None,
                    None,
                    typed_status,
                    payload,
                )
            return result

        loc = payload.get("location") or {}
        plat = loc.get("lat")
        plng = loc.get("lng")
        pano_id = payload.get("pano_id")
        image_date = payload.get("date")
        if isinstance(image_date, str):
            date_str: str | None = image_date
        else:
            date_str = None

        result = StreetViewMetadataResult(
            cache_key=cache_key,
            query_lat=lat,
            query_lng=lng,
            pano_id=str(pano_id) if pano_id is not None else None,
            pano_lat=float(plat) if plat is not None else None,
            pano_lng=float(plng) if plng is not None else None,
            image_date=date_str,
            api_status="OK",
        )

        if self._cache:
            self._cache.set(
                cache_key,
                lat,
                lng,
                result.pano_id,
                result.pano_lat,
                result.pano_lng,
                result.image_date,
                "OK",
                payload,
            )
        return result


_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "OK",
        "ZERO_RESULTS",
        "NOT_FOUND",
        "OVER_QUERY_LIMIT",
        "REQUEST_DENIED",
        "INVALID_REQUEST",
        "UNKNOWN_ERROR",
    }
)

_CACHEABLE_FAILURES: frozenset[str] = frozenset(
    {
        "ZERO_RESULTS",
        "NOT_FOUND",
    }
)
