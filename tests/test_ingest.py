from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ingest.reader import FOLIO_FIELD, iter_munroll_records, peek_folio_field
from ingest.store import MunrollStore


def _write_sample_csv(path: Path, *, include_dup_folio: bool = True) -> None:
    """Minimal MunRoll-shaped CSV: preamble + header + rows."""
    lines = [
        "H Property Appraiser preamble line 1\n",
        "Disclaimer line 2\n",
        "Another disclaimer\n",
        'Folio,Property Address,Property City, Property Zip,Year\n',
        '"0101000000020","16 SE 2 ST","Miami","33131-0000","2025"\n',
        '"0202000000030","100 MAIN ST","Miami","33132-0000","2025"\n',
    ]
    if include_dup_folio:
        # Same Folio as first row; later row should win in SQLite import.
        lines.append('"0101000000020","999 UPDATED ST","Miami","33131-0000","2025"\n')
    path.write_text("".join(lines), encoding="utf-8")


def test_iter_munroll_records_skips_preamble_and_yields_dicts(tmp_path: Path) -> None:
    p = tmp_path / "roll.csv"
    _write_sample_csv(p, include_dup_folio=True)
    rows = list(iter_munroll_records(p))
    assert len(rows) == 3
    assert rows[0][FOLIO_FIELD] == "0101000000020"
    assert rows[0]["Property Address"] == "16 SE 2 ST"
    assert rows[1][FOLIO_FIELD] == "0202000000030"
    assert rows[2][FOLIO_FIELD] == "0101000000020"
    assert rows[2]["Property Address"] == "999 UPDATED ST"


def test_iter_munroll_records_raises_without_header(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text("not a munroll file\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No MunRoll header"):
        list(iter_munroll_records(p))


def test_peek_folio_field(tmp_path: Path) -> None:
    p = tmp_path / "roll.csv"
    _write_sample_csv(p, include_dup_folio=False)
    assert peek_folio_field(p) == "0101000000020"


def test_import_csv_dedupes_by_folio_last_wins(tmp_path: Path) -> None:
    csv_path = tmp_path / "roll.csv"
    db_path = tmp_path / "db.sqlite"
    _write_sample_csv(csv_path, include_dup_folio=True)

    with MunrollStore(db_path) as store:
        stats = store.import_csv(csv_path, batch_size=2)
        assert stats.rows_read == 3
        assert stats.rows_skipped_empty_folio == 0
        assert stats.duplicates_resolved == 1
        assert stats.unique_folios == 2
        assert store.count_rows() == 2

    raw = store.get_row_json("0101000000020")
    assert raw is not None
    data = json.loads(raw)
    assert data["Property Address"] == "999 UPDATED ST"


def test_import_skips_empty_folio(tmp_path: Path) -> None:
    csv_path = tmp_path / "roll.csv"
    db_path = tmp_path / "db.sqlite"
    body = (
        "preamble\n" * 3
        + 'Folio,Property Address\n'
        + '"","MISSING"\n'
        + '"0303000000040","OK ROW"\n'
    )
    csv_path.write_text(body, encoding="utf-8")

    with MunrollStore(db_path) as store:
        stats = store.import_csv(csv_path)
        assert stats.rows_read == 2
        assert stats.rows_skipped_empty_folio == 1
        assert store.count_rows() == 1
