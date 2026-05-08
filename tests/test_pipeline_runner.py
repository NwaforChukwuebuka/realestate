from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from geocoding.models import GeocodeResult
from normalize.store import NormalizedStore
from pipeline.models import PendingParcelWork
from pipeline.runner import process_one_parcel
from pipeline.store import PipelineStore
from streetview.images import StreetViewAngleCapture, StreetViewImageFetchResult
from streetview.models import StreetViewMetadataResult
from verification.models import PropertyVerificationResult


def _minimal_raw() -> dict[str, object | None]:
    return {
        "Folio": "0101000000020",
        "Property Address": "16 SE 2 ST",
        "Property City": "Miami",
        " Property Zip": "33131-0000",
        "Owner1": "ACME LLC",
        "Owner2": "",
        "Mailing Address": "100 MAIN ST",
        "Mailing City": "Miami",
        "Mailing State": "FL",
        "Mailing Zip": "33130",
        "Land Use": "0100 - SINGLE FAMILY",
        "YearBuilt": "1985",
        "Sale Date 1": "06/23/2021",
        "Assessed": "1000",
    }


def _init_raw_db(db_path: Path, rows: list[dict[str, object | None]]) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE munroll_raw (
            folio TEXT PRIMARY KEY NOT NULL,
            row_json TEXT NOT NULL
        );
        """,
    )
    for r in rows:
        folio = str(r["Folio"]).strip()
        conn.execute(
            "INSERT INTO munroll_raw (folio, row_json) VALUES (?, ?);",
            (folio, json.dumps(r)),
        )
    conn.commit()
    conn.close()


def test_process_one_parcel_with_mocks(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    _init_raw_db(db, [_minimal_raw()])
    with NormalizedStore(db) as nstore:
        nstore.rebuild_from_raw(batch_size=10, motivation_as_of_year=2026)
    with PipelineStore(db) as pstore:
        pstore.init_schema()
        pstore.sync_from_normalized()

    work = PendingParcelWork(
        parcel_id="0101000000020",
        property_address="16 SE 2 ST",
        city="Miami",
        state="FL",
        zip="33131",
    )

    geocoder = MagicMock()
    geocoder.geocode.return_value = GeocodeResult(
        cache_key="k",
        lat=25.77,
        lng=-80.19,
        formatted_address="x",
        location_type="ROOFTOP",
        partial_match=False,
        api_status="OK",
        confidence=100,
    )

    sv_meta = MagicMock()
    sv_meta.lookup.return_value = StreetViewMetadataResult(
        cache_key="sv",
        query_lat=25.77,
        query_lng=-80.19,
        pano_id="pano_x",
        pano_lat=25.7701,
        pano_lng=-80.1901,
        image_date="2024-01",
        api_status="OK",
    )

    img_center = tmp_path / "off+000_heading_090_fov_90.jpg"
    img_side = tmp_path / "off+015_heading_105_fov_90.jpg"
    img_center.write_bytes(b"\xff\xd8\xff\xd9")
    img_side.write_bytes(b"\xff\xd8\xff\xd9")

    fetcher = MagicMock()
    fetcher.fetch_multi_angle_set.return_value = StreetViewImageFetchResult(
        pano_id="pano_x",
        base_heading_deg=90.0,
        pano_lat=25.7701,
        pano_lng=-80.1901,
        property_lat=25.77,
        property_lng=-80.19,
        fov=90,
        size=(640, 640),
        captures=(
            StreetViewAngleCapture(offset_deg=0, heading_deg=90.0, image_url="u1", local_path=img_center),
            StreetViewAngleCapture(offset_deg=15, heading_deg=105.0, image_url="u2", local_path=img_side),
        ),
    )

    verifier = MagicMock()
    verifier.analyze_images.return_value = PropertyVerificationResult(
        target_confidence=80,
        distress_score=5,
        visible_signs=(),
        condition_summary="ok",
        recommended_action="verify",
    )

    images_root = tmp_path / "sv_out"

    with PipelineStore(db) as pstore:
        out = process_one_parcel(
            pstore=pstore,
            geocoder=geocoder,
            sv_meta=sv_meta,
            fetcher=fetcher,
            verifier=verifier,
            work=work,
            images_root=images_root,
            stop_after_images=False,
        )
        assert out == "done"
        row = pstore.get("0101000000020")
        assert row is not None
        assert row.pipeline_status == "done"
        assert row.verification_json is not None

    geocoder.geocode.assert_called_once()
    sv_meta.lookup.assert_called_once()
    fetcher.fetch_multi_angle_set.assert_called_once()
    verifier.analyze_images.assert_called_once()
    positional, _kwargs = verifier.analyze_images.call_args
    assert positional[0] == [img_center]
