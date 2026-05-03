from __future__ import annotations

import json

import pytest

from normalize.mapper import map_munroll_record, map_munroll_row_json
from normalize.models import NormalizedProperty


def _minimal_raw(**overrides: object) -> dict[str, object | None]:
    base: dict[str, object | None] = {
        "Folio": "0101000000020",
        "Property Address": "16 SE 2 ST",
        "Property City": "Miami",
        " Property Zip": "33131-0000",
        "Owner1": "ACME LLC",
        "Owner2": "C/O JANE DOE",
        "Mailing Address": "100 MAIN ST",
        "Mailing City": "Miami",
        "Mailing State": "FL",
        "Mailing Zip": "33130",
        "Land Use": "0100 - SINGLE FAMILY",
        "YearBuilt": "1985",
        "Sale Date 1": "06/23/2021",
        "Assessed": "32788019",
    }
    base.update(overrides)
    return base


def test_map_munroll_record_full() -> None:
    n = map_munroll_record(_minimal_raw())
    assert n.parcel_id == "0101000000020"
    assert n.property_address == "16 SE 2 ST"
    assert n.city == "Miami"
    assert n.state == "FL"
    assert n.zip == "33131-0000"
    assert n.owner_name == "ACME LLC | C/O JANE DOE"
    assert n.mailing_address == "100 MAIN ST, Miami, FL 33130"
    assert n.property_type == "0100 - SINGLE FAMILY"
    assert n.year_built == 1985
    assert n.last_sale_date == "2021-06-23"
    assert n.assessed_value == 32788019


def test_zip_falls_back_to_property_zip_without_leading_space() -> None:
    raw = _minimal_raw()
    del raw[" Property Zip"]
    raw["Property Zip"] = "33132"
    n = map_munroll_record(raw)
    assert n.zip == "33132"


def test_year_built_zero_becomes_none() -> None:
    n = map_munroll_record(_minimal_raw(YearBuilt="0"))
    assert n.year_built is None


def test_owner_single_and_empty_second() -> None:
    n = map_munroll_record(_minimal_raw(Owner2=""))
    assert n.owner_name == "ACME LLC"


def test_mailing_address_partial() -> None:
    n = map_munroll_record(
        _minimal_raw(
            **{
                "Mailing Address": "",
                "Mailing City": "Tampa",
                "Mailing State": "FL",
                "Mailing Zip": "33601",
            },
        )
    )
    assert n.mailing_address == "Tampa, FL 33601"


def test_last_sale_date_falls_back_to_sale_date_2() -> None:
    n = map_munroll_record(
        _minimal_raw(
            **{
                "Sale Date 1": "",
                "Sale Date 2": "05/24/2013",
            },
        )
    )
    assert n.last_sale_date == "2013-05-24"


def test_map_munroll_row_json_roundtrip() -> None:
    raw = _minimal_raw()
    n = map_munroll_row_json(json.dumps(raw))
    assert isinstance(n, NormalizedProperty)
    assert n.parcel_id == "0101000000020"


def test_missing_folio_raises() -> None:
    raw = _minimal_raw(Folio="")
    with pytest.raises(ValueError, match="missing Folio"):
        map_munroll_record(raw)
