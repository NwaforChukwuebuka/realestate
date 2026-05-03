from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from geocoding.cache import GeocodeCache
from geocoding.geocoder import (
    Geocoder,
    confidence_from_google,
    normalize_address_key,
)


def test_normalize_address_key_dedupes_spacing_and_case() -> None:
    a = normalize_address_key("  16 SE 2 ST  ", "Miami", "FL", "33131-0000")
    b = normalize_address_key("16 se 2 st", "miami", "fl", "33131")
    assert a == b


def test_normalize_empty_street_still_stable_key() -> None:
    k = normalize_address_key("", "Miami", "FL", "33132")
    assert "miami" in k


def test_confidence_rooftop_vs_approximate() -> None:
    assert confidence_from_google("ROOFTOP", False) == 100
    assert confidence_from_google("APPROXIMATE", False) == 40
    assert confidence_from_google("ROOFTOP", True) == 85


def test_geocoder_skips_empty_street() -> None:
    g = Geocoder(api_key="dummy")
    r = g.geocode("", "Miami", "FL", "33131")
    assert r.api_status == "SKIPPED_EMPTY_ADDRESS"
    assert r.lat is None


def test_geocoder_cache_second_call_no_http() -> None:
    ok_body = {
        "status": "OK",
        "results": [
            {
                "formatted_address": "16 SE 2nd St, Miami, FL 33131, USA",
                "geometry": {
                    "location": {"lat": 25.7741, "lng": -80.1936},
                    "location_type": "ROOFTOP",
                },
                "partial_match": False,
            }
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "geo.sqlite"
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = ok_body
        mock_get.return_value = mock_resp
        session = requests.Session()
        with patch.object(session, "get", mock_get):
            g = Geocoder(api_key="k", cache_db_path=db, session=session)
            r1 = g.geocode("16 SE 2 ST", "Miami", "FL", "33131-0000")
            r2 = g.geocode("16 se 2 st", "miami", "FL", "33131")
            g.close()
        assert r1.ok and r1.api_status == "OK"
        assert r2.ok and r2.api_status == "CACHED"
        assert r1.lat == r2.lat == pytest.approx(25.7741)
        assert mock_get.call_count == 1


def test_geocoder_zero_results_cached() -> None:
    body = {"status": "ZERO_RESULTS", "results": []}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "geo.sqlite"
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = body
        mock_get.return_value = mock_resp
        session = requests.Session()
        with patch.object(session, "get", mock_get):
            g = Geocoder(api_key="k", cache_db_path=db, session=session)
            r1 = g.geocode("99999 NONEXISTENT ST", "Miami", "FL", "33101")
            r2 = g.geocode("99999 NONEXISTENT ST", "Miami", "FL", "33101")
            g.close()
        assert not r1.ok and r1.api_status == "ZERO_RESULTS"
        assert not r2.ok and r2.api_status == "CACHED"
        assert mock_get.call_count == 1


def test_cache_persists_raw_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "geo.sqlite"
        c = GeocodeCache(db)
        c.set(
            "k",
            1.0,
            2.0,
            "addr",
            "ROOFTOP",
            False,
            "OK",
            100,
            {"status": "OK"},
        )
        row = c.get("k")
        c.close()
        assert row is not None
        raw = json.loads(row["raw_json"])
        assert raw["status"] == "OK"


@pytest.mark.integration
def test_live_geocode_if_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        pytest.skip("Set GOOGLE_MAPS_API_KEY for live Google Geocoding test")
    with tempfile.TemporaryDirectory() as tmp:
        g = Geocoder(api_key=key, cache_db_path=Path(tmp) / "live.sqlite")
        try:
            r = g.geocode("200 S Miami Ave", "Miami", "FL", "33130-0000")
        finally:
            g.close()
        assert r.ok
        assert r.lat is not None and r.lng is not None
        assert r.confidence is not None
