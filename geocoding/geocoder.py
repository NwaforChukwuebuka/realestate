from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from geocoding.cache import GeocodeCache
from geocoding.models import GeocodeResult, GeocodeStatus

GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def normalize_address_key(
    street: str,
    city: str,
    state: str = "FL",
    zip_code: str | None = None,
) -> str:
    """
    Stable key for dedupe + cache. Miami-Dade roll uses city + zip on separate fields.
    """
    parts = [
        _norm_segment(street),
        _norm_segment(city),
        _norm_segment(state),
        _norm_zip(zip_code) if zip_code else "",
    ]
    return "|".join(parts)


def _norm_segment(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_zip(z: str) -> str:
    z = (z or "").strip()
    # 33131-0000 -> 33131 for matching; keep extension if non-zero meaningful? use 5-digit
    m = re.match(r"^(\d{5})", z.replace(" ", ""))
    return m.group(1) if m else z.lower()


def confidence_from_google(
    location_type: str | None, partial_match: bool
) -> int | None:
    if not location_type:
        return None
    base = {
        "ROOFTOP": 100,
        "RANGE_INTERPOLATED": 85,
        "GEOMETRIC_CENTER": 60,
        "APPROXIMATE": 40,
    }.get(location_type, 50)
    if partial_match:
        base = max(0, base - 15)
    return base


class Geocoder:
    def __init__(
        self,
        api_key: str,
        cache_db_path: str | Path | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()
        self._cache: GeocodeCache | None = (
            GeocodeCache(cache_db_path) if cache_db_path else None
        )

    def close(self) -> None:
        if self._cache:
            self._cache.close()

    def geocode(
        self,
        street: str,
        city: str,
        state: str = "FL",
        zip_code: str | None = None,
    ) -> GeocodeResult:
        cache_key = normalize_address_key(street, city, state, zip_code)
        if not _norm_segment(street):
            return GeocodeResult(
                cache_key=cache_key,
                lat=None,
                lng=None,
                formatted_address=None,
                location_type=None,
                partial_match=False,
                api_status="SKIPPED_EMPTY_ADDRESS",
                confidence=None,
            )

        if self._cache:
            row = self._cache.get(cache_key)
            if row is not None:
                return GeocodeResult(
                    cache_key=cache_key,
                    lat=row["lat"],
                    lng=row["lng"],
                    formatted_address=row["formatted_address"],
                    location_type=row["location_type"],
                    partial_match=bool(row["partial_match"]),
                    api_status="CACHED",
                    confidence=row["confidence"],
                )

        query = _build_query(street, city, state, zip_code)
        params = {"address": query, "key": self._api_key}
        r = self._session.get(GOOGLE_GEOCODE_URL, params=params, timeout=30)
        r.raise_for_status()
        payload: dict[str, Any] = r.json()
        status = payload.get("status", "UNKNOWN_ERROR")
        typed_status: GeocodeStatus = (
            status if status in _TERMINAL_STATUSES else "UNKNOWN_ERROR"
        )
        results = payload.get("results") or []
        if status == "OK" and not results:
            typed_status = "ZERO_RESULTS"

        if status != "OK" or not results:
            result = GeocodeResult(
                cache_key=cache_key,
                lat=None,
                lng=None,
                formatted_address=None,
                location_type=None,
                partial_match=False,
                api_status=typed_status,
                confidence=None,
            )
            if self._cache and typed_status == "ZERO_RESULTS":
                self._cache.set(
                    cache_key,
                    None,
                    None,
                    None,
                    None,
                    False,
                    "ZERO_RESULTS",
                    None,
                    payload,
                )
            return result

        first = results[0]
        geom = first.get("geometry") or {}
        loc = geom.get("location") or {}
        lat = loc.get("lat")
        lng = loc.get("lng")
        location_type = geom.get("location_type")
        partial_match = bool(first.get("partial_match"))
        formatted = first.get("formatted_address")
        conf = confidence_from_google(location_type, partial_match)

        result = GeocodeResult(
            cache_key=cache_key,
            lat=float(lat) if lat is not None else None,
            lng=float(lng) if lng is not None else None,
            formatted_address=formatted,
            location_type=location_type,
            partial_match=partial_match,
            api_status="OK",
            confidence=conf,
        )

        if self._cache:
            self._cache.set(
                cache_key,
                result.lat,
                result.lng,
                result.formatted_address,
                result.location_type,
                result.partial_match,
                "OK",
                result.confidence,
                payload,
            )
        return result


_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "OK",
        "ZERO_RESULTS",
        "OVER_QUERY_LIMIT",
        "REQUEST_DENIED",
        "INVALID_REQUEST",
        "UNKNOWN_ERROR",
    }
)


def _build_query(street: str, city: str, state: str, zip_code: str | None) -> str:
    parts = [street.strip(), city.strip(), state.strip()]
    if zip_code and zip_code.strip():
        parts.append(zip_code.strip())
    return ", ".join(parts)
