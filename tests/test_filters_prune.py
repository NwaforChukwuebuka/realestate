from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from filters.prune import prune_non_residential_normalized
from normalize.store import NormalizedStore


def _raw(folio: str, land_use: str) -> dict[str, object | None]:
    return {
        "Folio": folio,
        "Property Address": "1 MAIN ST",
        "Property City": "Miami",
        " Property Zip": "33101",
        "Owner1": "X",
        "Owner2": "",
        "Mailing Address": "",
        "Mailing City": "",
        "Mailing State": "FL",
        "Mailing Zip": "",
        "Land Use": land_use,
        "YearBuilt": "2000",
        "Sale Date 1": "01/01/2020",
        "Assessed": "100",
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


def test_prune_removes_non_residential(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    sfr = _raw("0101000000020", "0101 - RESIDENTIAL - SINGLE FAMILY : 1 UNIT")
    condo = _raw("0202000000030", "0407 - RESIDENTIAL - TOTAL VALUE : CONDOMINIUM - RESIDENTIAL")
    _init_raw_db(db, [sfr, condo])

    with NormalizedStore(db) as store:
        store.rebuild_from_raw(clear_first=True)
        assert store.count_normalized() == 2

    stats = prune_non_residential_normalized(db)
    assert stats.rows_before == 2
    assert stats.rows_removed == 1
    assert stats.rows_after == 1

    conn = sqlite3.connect(db)
    one = conn.execute("SELECT parcel_id FROM properties_normalized;").fetchone()
    conn.close()
    assert one is not None
    assert one[0] == "0101000000020"
