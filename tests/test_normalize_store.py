from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from normalize.store import NormalizedStore


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


def test_rebuild_populates_normalized_and_joins_to_raw(tmp_path: Path) -> None:
    db = tmp_path / "roll.sqlite"
    raw = _minimal_raw()
    _init_raw_db(db, [raw])

    with NormalizedStore(db) as store:
        stats = store.rebuild_from_raw(batch_size=10, motivation_as_of_year=2026)
        assert stats.rows_read == 1
        assert stats.rows_written == 1
        assert stats.rows_failed == 0
        assert store.count_normalized() == 1

    conn = sqlite3.connect(db)
    cur = conn.execute(
        """
        SELECT n.parcel_id, n.property_address, r.folio,
               n.absentee_owner, n.out_of_state_owner, n.years_owned, n.old_property
        FROM properties_normalized n
        JOIN munroll_raw r ON r.folio = n.parcel_id
        WHERE n.parcel_id = ?;
        """,
        ("0101000000020",),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == row[2] == "0101000000020"
    assert row[1] == "16 SE 2 ST"
    assert row[3] == 1  # absentee (mailing differs from situs)
    assert row[4] == 0  # in-state FL mailing
    assert row[5] == 5  # 2026 - 2021 sale
    assert row[6] == 0  # year_built 1985
    conn.close()


def test_clear_first_removes_stale_parcels(tmp_path: Path) -> None:
    db = tmp_path / "roll.sqlite"
    a = _minimal_raw()
    b = {**_minimal_raw(), "Folio": "0202000000030", "Property Address": "OTHER ST"}
    _init_raw_db(db, [a, b])

    with NormalizedStore(db) as store:
        store.rebuild_from_raw(clear_first=True)
        assert store.count_normalized() == 2

    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM munroll_raw WHERE folio = '0202000000030';")
    conn.commit()
    conn.close()

    with NormalizedStore(db) as store:
        store.rebuild_from_raw(clear_first=True)
        assert store.count_normalized() == 1


def test_rebuild_residential_only_skips_non_residential(tmp_path: Path) -> None:
    db = tmp_path / "roll.sqlite"
    sfr = _minimal_raw()
    condo = {
        **_minimal_raw(),
        "Folio": "0202000000030",
        "Land Use": "0407 - RESIDENTIAL - TOTAL VALUE : CONDOMINIUM - RESIDENTIAL",
    }
    _init_raw_db(db, [sfr, condo])

    with NormalizedStore(db) as store:
        stats = store.rebuild_from_raw(batch_size=10, residential_only=True)
        assert stats.rows_read == 2
        assert stats.rows_written == 1
        assert store.count_normalized() == 1

    conn = sqlite3.connect(db)
    pid = conn.execute("SELECT parcel_id FROM properties_normalized;").fetchone()[0]
    conn.close()
    assert pid == "0101000000020"


def test_incremental_no_truncate_updates_existing(tmp_path: Path) -> None:
    db = tmp_path / "roll.sqlite"
    _init_raw_db(db, [_minimal_raw()])

    with NormalizedStore(db) as store:
        store.rebuild_from_raw(clear_first=True)
        assert store.count_normalized() == 1

    updated = {**_minimal_raw(), "Property Address": "999 NEW ST"}
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE munroll_raw SET row_json = ? WHERE folio = ?;",
        (json.dumps(updated), "0101000000020"),
    )
    conn.commit()
    conn.close()

    with NormalizedStore(db) as store:
        store.rebuild_from_raw(clear_first=False, motivation_as_of_year=2026)
        assert store.count_normalized() == 1

    conn = sqlite3.connect(db)
    addr = conn.execute(
        "SELECT property_address FROM properties_normalized WHERE parcel_id = ?;",
        ("0101000000020",),
    ).fetchone()[0]
    conn.close()
    assert addr == "999 NEW ST"


def test_legacy_table_gains_motivation_columns_then_backfill(tmp_path: Path) -> None:
    """Pre-motivation SQLite files get new columns via init_schema + backfill."""
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE properties_normalized (
            parcel_id TEXT PRIMARY KEY NOT NULL,
            property_address TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,
            owner_name TEXT,
            mailing_address TEXT,
            property_type TEXT,
            year_built INTEGER,
            last_sale_date TEXT,
            assessed_value INTEGER
        );
        """,
    )
    conn.execute(
        """
        INSERT INTO properties_normalized (
            parcel_id, property_address, city, state, zip,
            owner_name, mailing_address, property_type,
            year_built, last_sale_date, assessed_value
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            "0101000000020",
            "16 SE 2 ST",
            "Miami",
            "FL",
            "33131-0000",
            None,
            "100 MAIN ST, Miami, FL 33130",
            "0100 - SINGLE FAMILY",
            1979,
            "2021-06-23",
            1000,
        ),
    )
    conn.commit()
    conn.close()

    with NormalizedStore(db) as store:
        store.init_schema()
        stats = store.backfill_motivation(
            batch_size=10,
            motivation_as_of_year=2026,
        )
        assert stats.rows_read == 1
        assert stats.rows_written == 1

    conn = sqlite3.connect(db)
    row = conn.execute(
        """
        SELECT absentee_owner, out_of_state_owner, years_owned, old_property
        FROM properties_normalized WHERE parcel_id = ?;
        """,
        ("0101000000020",),
    ).fetchone()
    conn.close()
    assert row == (1, 0, 5, 1)
