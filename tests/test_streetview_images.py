from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from urllib.parse import parse_qs, urlparse

from streetview.heading import (
    HEADING_OFFSETS_DEG,
    headings_for_offsets,
    initial_bearing_degrees,
    normalize_heading_degrees,
)
from streetview.images import (
    StreetViewImageFetcher,
    build_streetview_static_url,
)


def test_normalize_heading_degrees() -> None:
    assert normalize_heading_degrees(0) == 0.0
    assert normalize_heading_degrees(360) == 0.0
    assert normalize_heading_degrees(-10) == 350.0
    assert normalize_heading_degrees(370) == 10.0


def test_initial_bearing_cardinals() -> None:
    # Small step north from equator / prime meridian → ~0° (north).
    assert initial_bearing_degrees(0.0, 0.0, 0.001, 0.0) == pytest.approx(0.0, abs=0.5)
    # Small step east → ~90°.
    assert initial_bearing_degrees(0.0, 0.0, 0.0, 0.001) == pytest.approx(90.0, abs=0.5)


def test_headings_for_offsets_order_and_wrap() -> None:
    assert HEADING_OFFSETS_DEG == (0, 15, -15, 30, -30)
    pairs = headings_for_offsets(10.0)
    assert [p[0] for p in pairs] == [0, 15, -15, 30, -30]
    assert pairs[0][1] == pytest.approx(10.0)
    assert pairs[1][1] == pytest.approx(25.0)
    assert pairs[2][1] == pytest.approx(355.0)
    assert pairs[3][1] == pytest.approx(40.0)
    assert pairs[4][1] == pytest.approx(340.0)


def test_build_streetview_static_url_query() -> None:
    u = build_streetview_static_url(
        api_key="k",
        pano_id="pano1",
        heading=12.5,
        fov=90,
        size=(640, 480),
        pitch=0,
    )
    assert u.startswith("https://maps.googleapis.com/maps/api/streetview?")
    q = parse_qs(urlparse(u).query)
    assert q["pano"] == ["pano1"]
    assert q["key"] == ["k"]
    assert q["fov"] == ["90"]
    assert q["size"] == ["640x480"]
    assert q["pitch"] == ["0"]
    assert float(q["heading"][0]) == pytest.approx(12.5)


def test_fetch_multi_angle_set_writes_jpegs() -> None:
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # minimal-ish JPEG header prefix

    def fake_get(url: str, *_args: object, **_kwargs: object) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.headers = {"Content-Type": "image/jpeg"}
        resp.content = jpeg
        resp.text = ""
        return resp

    session = requests.Session()
    with patch.object(session, "get", side_effect=fake_get):
        fetcher = StreetViewImageFetcher(api_key="key", session=session)
        with tempfile.TemporaryDirectory() as tmp:
            r = fetcher.fetch_multi_angle_set(
                pano_id="abc/pano",
                pano_lat=25.775,
                pano_lng=-80.194,
                property_lat=25.776,
                property_lng=-80.193,
                output_dir=Path(tmp),
            )
            assert r.pano_id == "abc/pano"
            assert r.fov == 90
            assert r.size == (640, 640)
            assert len(r.captures) == 5
            for c in r.captures:
                assert c.local_path is not None
                assert c.local_path.read_bytes() == jpeg
                assert "maps.googleapis.com" in c.image_url
                assert "heading=" in c.image_url
            assert session.get.call_count == 5  # type: ignore[attr-defined]


@pytest.mark.integration
def test_live_fetch_if_key_present() -> None:
    import os

    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        pytest.skip("Set GOOGLE_MAPS_API_KEY for live Street View image fetch test")

    from streetview.metadata import StreetViewMetadataClient

    prop_lat, prop_lng = 25.7742694, -80.1936589
    meta = StreetViewMetadataClient(api_key=key)
    try:
        m = meta.lookup(prop_lat, prop_lng)
    finally:
        meta.close()
    if not m.ok or not m.pano_id or m.pano_lat is None or m.pano_lng is None:
        pytest.skip("No Street View pano for default Miami coordinates")

    with tempfile.TemporaryDirectory() as tmp:
        fetcher = StreetViewImageFetcher(api_key=key)
        r = fetcher.fetch_multi_angle_set(
            pano_id=m.pano_id,
            pano_lat=m.pano_lat,
            pano_lng=m.pano_lng,
            property_lat=prop_lat,
            property_lng=prop_lng,
            output_dir=Path(tmp),
        )
    assert len(r.captures) == 5
    for c in r.captures:
        assert c.local_path is not None
        data = c.local_path.read_bytes()
        assert data[:2] == b"\xff\xd8"
