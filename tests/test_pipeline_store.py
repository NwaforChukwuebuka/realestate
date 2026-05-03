from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from geocoding.models import GeocodeResult
from normalize.store import NormalizedStore
from pipeline.models import PendingParcelWork
from pipeline.store import PipelineStore, verification_to_json
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
        """
    )
    for r in rows:
        folio = str(r["Folio"]).strip()
        conn.execute(
            "INSERT INTO munroll_raw (folio, row_json) VALUES (?, ?);",
            (folio, json.dumps(r)),
        )
    conn.commit()
    conn.close()


def test_pipeline_sync_and_apply_flow(tmp_path: Path) -> None:
    db = tmp_path / "roll.sqlite"
    _init_raw_db(db, [_minimal_raw()])

    with NormalizedStore(db) as nstore:
        nstore.rebuild_from_raw(batch_size=10, motivation_as_of_year=2026)

    with PipelineStore(db) as pstore:
        pstore.init_schema()
        n1 = pstore.sync_from_normalized()
        n2 = pstore.sync_from_normalized()
        assert n1 == 1
        assert n2 == 0
        assert pstore.count_rows() == 1

        g_ok = GeocodeResult(
            cache_key="k1",
            lat=25.77,
            lng=-80.19,
            formatted_address="16 SE 2nd St, Miami, FL 33131, USA",
            location_type="ROOFTOP",
            partial_match=False,
            api_status="OK",
            confidence=100,
        )
        pstore.apply_geocode("0101000000020", g_ok)
        row = pstore.get("0101000000020")
        assert row is not None
        assert row.pipeline_status == "pending"
        assert row.geocode_lat == 25.77
        assert row.last_error is None

        m_ok = StreetViewMetadataResult(
            cache_key="sv1",
            query_lat=25.77,
            query_lng=-80.19,
            pano_id="pano1",
            pano_lat=25.7701,
            pano_lng=-80.1901,
            image_date="2024-01",
            api_status="OK",
        )
        pstore.apply_streetview_metadata("0101000000020", m_ok)
        row = pstore.get("0101000000020")
        assert row is not None
        assert row.pipeline_status == "pending"
        assert row.sv_pano_id == "pano1"

        pstore.apply_images("0101000000020", [Path("a.jpg"), Path("b.jpg")])
        row = pstore.get("0101000000020")
        assert row is not None
        assert json.loads(row.image_paths_json or "[]") == ["a.jpg", "b.jpg"]

        v = PropertyVerificationResult(
            target_confidence=85,
            distress_score=10,
            visible_signs=("overgrown grass",),
            condition_summary="ok",
            recommended_action="verify",
        )
        pstore.apply_verification("0101000000020", v)
        row = pstore.get("0101000000020")
        assert row is not None
        assert row.pipeline_status == "done"
        parsed = json.loads(row.verification_json or "{}")
        assert parsed["target_confidence"] == 85
        assert parsed["visible_signs"] == ["overgrown grass"]


def test_geocode_failure_marks_failed(tmp_path: Path) -> None:
    db = tmp_path / "f.sqlite"
    _init_raw_db(db, [_minimal_raw()])
    with NormalizedStore(db) as nstore:
        nstore.rebuild_from_raw(batch_size=10, motivation_as_of_year=2026)

    with PipelineStore(db) as pstore:
        pstore.init_schema()
        pstore.sync_from_normalized()
        g_bad = GeocodeResult(
            cache_key="k0",
            lat=None,
            lng=None,
            formatted_address=None,
            location_type=None,
            partial_match=False,
            api_status="ZERO_RESULTS",
            confidence=None,
        )
        pstore.apply_geocode("0101000000020", g_bad)
        row = pstore.get("0101000000020")
        assert row is not None
        assert row.pipeline_status == "failed"
        assert row.geocode_api_status == "ZERO_RESULTS"


def test_streetview_no_pano_skipped(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite"
    _init_raw_db(db, [_minimal_raw()])
    with NormalizedStore(db) as nstore:
        nstore.rebuild_from_raw(batch_size=10, motivation_as_of_year=2026)

    with PipelineStore(db) as pstore:
        pstore.init_schema()
        pstore.sync_from_normalized()
        g_ok = GeocodeResult(
            cache_key="k1",
            lat=25.77,
            lng=-80.19,
            formatted_address="x",
            location_type="ROOFTOP",
            partial_match=False,
            api_status="OK",
            confidence=100,
        )
        pstore.apply_geocode("0101000000020", g_ok)
        m_none = StreetViewMetadataResult(
            cache_key="sv0",
            query_lat=25.77,
            query_lng=-80.19,
            pano_id=None,
            pano_lat=None,
            pano_lng=None,
            image_date=None,
            api_status="ZERO_RESULTS",
        )
        pstore.apply_streetview_metadata("0101000000020", m_none)
        row = pstore.get("0101000000020")
        assert row is not None
        assert row.pipeline_status == "skipped_no_street_view"


def test_fetch_pending_with_addresses(tmp_path: Path) -> None:
    db = tmp_path / "p.sqlite"
    _init_raw_db(db, [_minimal_raw()])
    with NormalizedStore(db) as nstore:
        nstore.rebuild_from_raw(batch_size=10, motivation_as_of_year=2026)
    with PipelineStore(db) as pstore:
        pstore.init_schema()
        pstore.sync_from_normalized()
        rows = pstore.fetch_pending_with_addresses(10)
        assert len(rows) == 1
        assert isinstance(rows[0], PendingParcelWork)
        assert rows[0].parcel_id == "0101000000020"
        assert rows[0].property_address == "16 SE 2 ST"


def test_verification_to_json_roundtrip() -> None:
    v = PropertyVerificationResult(
        target_confidence=70,
        distress_score=0,
        visible_signs=(),
        condition_summary="",
        recommended_action="skip",
    )
    s = verification_to_json(v)
    d = json.loads(s)
    assert d["recommended_action"] == "skip"
    assert d["visible_signs"] == []
