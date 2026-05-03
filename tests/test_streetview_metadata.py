from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from streetview.cache import StreetViewMetadataCache
from streetview.metadata import StreetViewMetadataClient, normalize_coord_cache_key


def test_normalize_coord_cache_key_stable() -> None:
    a = normalize_coord_cache_key(25.7742694, -80.1936589)
    b = normalize_coord_cache_key(25.77426941, -80.19365891)
    assert a == b


def test_lookup_skips_none_coordinates() -> None:
    c = StreetViewMetadataClient(api_key="dummy")
    r = c.lookup(None, -80.19)
    assert r.api_status == "SKIPPED_NO_COORDINATES"
    assert not r.ok
    assert not r.no_street_view


def test_lookup_ok_parses_pano_location_date() -> None:
    body = {
        "status": "OK",
        "copyright": "© Google",
        "date": "2019-05",
        "location": {"lat": 25.775, "lng": -80.194},
        "pano_id": "abc123xyz",
    }
    mock_get = MagicMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = body
    mock_get.return_value = mock_resp
    session = requests.Session()
    with patch.object(session, "get", mock_get):
        c = StreetViewMetadataClient(api_key="k", session=session)
        r = c.lookup(25.774, -80.193)
    assert r.ok
    assert r.api_status == "OK"
    assert r.pano_id == "abc123xyz"
    assert r.pano_lat == pytest.approx(25.775)
    assert r.pano_lng == pytest.approx(-80.194)
    assert r.image_date == "2019-05"
    assert not r.no_street_view
    assert mock_get.call_count == 1


def test_lookup_zero_results() -> None:
    body = {"status": "ZERO_RESULTS"}
    mock_get = MagicMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = body
    mock_get.return_value = mock_resp
    session = requests.Session()
    with patch.object(session, "get", mock_get):
        c = StreetViewMetadataClient(api_key="k", session=session)
        r = c.lookup(78.648401, 14.194336)
    assert not r.ok
    assert r.no_street_view
    assert r.api_status == "ZERO_RESULTS"


def test_cache_second_call_no_http() -> None:
    ok_body = {
        "status": "OK",
        "date": "2020-01",
        "location": {"lat": 25.1, "lng": -80.2},
        "pano_id": "pano1",
    }
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sv.sqlite"
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = ok_body
        mock_get.return_value = mock_resp
        session = requests.Session()
        with patch.object(session, "get", mock_get):
            c = StreetViewMetadataClient(api_key="k", cache_db_path=db, session=session)
            r1 = c.lookup(25.1, -80.2)
            r2 = c.lookup(25.1, -80.2)
            c.close()
        assert r1.ok and r1.api_status == "OK"
        assert r2.ok and r2.api_status == "CACHED"
        assert r1.pano_id == r2.pano_id == "pano1"
        assert mock_get.call_count == 1


def test_zero_results_cached_second_call_no_http() -> None:
    body = {"status": "ZERO_RESULTS"}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sv.sqlite"
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = body
        mock_get.return_value = mock_resp
        session = requests.Session()
        with patch.object(session, "get", mock_get):
            c = StreetViewMetadataClient(api_key="k", cache_db_path=db, session=session)
            r1 = c.lookup(78.648401, 14.194336)
            r2 = c.lookup(78.648401, 14.194336)
            c.close()
        assert not r1.ok and r1.api_status == "ZERO_RESULTS"
        assert not r2.ok and r2.api_status == "CACHED"
        assert r1.no_street_view and r2.no_street_view
        assert mock_get.call_count == 1


def test_cache_persists_raw_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sv.sqlite"
        cache = StreetViewMetadataCache(db)
        cache.set(
            "k",
            1.0,
            2.0,
            "pid",
            1.1,
            2.1,
            "2019-05",
            "OK",
            {"status": "OK"},
        )
        row = cache.get("k")
        cache.close()
        assert row is not None
        raw = json.loads(row["raw_json"])
        assert raw["status"] == "OK"


@pytest.mark.integration
def test_live_metadata_if_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        pytest.skip("Set GOOGLE_MAPS_API_KEY for live Street View metadata test")
    with tempfile.TemporaryDirectory() as tmp:
        c = StreetViewMetadataClient(api_key=key, cache_db_path=Path(tmp) / "live.sqlite")
        try:
            # Downtown Miami — expect outdoor Street View coverage.
            r = c.lookup(25.7742694, -80.1936589)
        finally:
            c.close()
        assert r.ok
        assert r.pano_id
        assert r.pano_lat is not None and r.pano_lng is not None
