from __future__ import annotations

import pytest

from filters.residential import (
    is_target_residential,
    normalized_property_is_target,
)
from normalize.models import NormalizedProperty


@pytest.mark.parametrize(
    ("land_use", "expected"),
    [
        (None, False),
        ("", False),
        ("   ", False),
        (
            "0101 - RESIDENTIAL - SINGLE FAMILY : 1 UNIT",
            True,
        ),
        (
            "0102 - RESIDENTIAL - SINGLE FAMILY : ADDITIONAL LIVING QUARTERS",
            True,
        ),
        (
            "0103 - RESIDENTIAL - SINGLE FAMILY : MULTIFAMILY 3 OR MORE UNITS",
            True,
        ),
        (
            "0104 - RESIDENTIAL - SINGLE FAMILY : RESIDENTIAL - TOTAL VALUE",
            True,
        ),
        (
            "0105 - RESIDENTIAL - SINGLE FAMILY : CLUSTER HOME",
            True,
        ),
        (
            "0176 - RESIDENTIAL - SINGLE FAMILY : RESIDENTIAL W/ ADDITIONAL QUARTERS",
            True,
        ),
        (
            "0802 - MULTIFAMILY 2-9 UNITS : 2 LIVING UNITS",
            True,
        ),
        (
            "0803 - MULTIFAMILY 2-9 UNITS : MULTIFAMILY 3 OR MORE UNITS",
            True,
        ),
        (
            "5001 - IMPR AGRI : RESIDENTIAL - SINGLE FAMILY",
            True,
        ),
        (
            "5002 - IMPR AGRI - NOT HOMESITES : 2 LIVING UNITS",
            True,
        ),
        (
            "0407 - RESIDENTIAL - TOTAL VALUE : CONDOMINIUM - RESIDENTIAL",
            False,
        ),
        (
            "8601 - COUNTY : RESIDENTIAL - SINGLE FAMILY",
            False,
        ),
        (
            "8602 - COUNTY : 2 LIVING UNITS",
            False,
        ),
        (
            "8903 - MUNICIPAL : MULTIFAMILY 3 OR MORE UNITS",
            False,
        ),
        (
            "1081 - VACANT LAND - COMMERCIAL : VACANT LAND",
            False,
        ),
        (
            "0000 - REFERENCE FOLIO : REFERENCE FOLIO",
            False,
        ),
        (
            "2865 - PARKING LOT/MOBILE HOME PARK : PARKING LOT",
            False,
        ),
        (
            "0303 - MULTIFAMILY 10 UNITS PLUS : MULTIFAMILY 3 OR MORE UNITS",
            False,
        ),
        (
            "0410 - RESIDENTIAL - TOTAL VALUE : TOWNHOUSE",
            False,
        ),
    ],
)
def test_is_target_residential(land_use: str | None, expected: bool) -> None:
    assert is_target_residential(land_use) is expected


def test_normalized_property_wrapper() -> None:
    keep = NormalizedProperty(
        parcel_id="1",
        property_address="1 MAIN ST",
        city="Miami",
        state="FL",
        zip="33101",
        owner_name=None,
        mailing_address=None,
        property_type="0802 - MULTIFAMILY 2-9 UNITS : 2 LIVING UNITS",
        year_built=None,
        last_sale_date=None,
        assessed_value=None,
    )
    assert normalized_property_is_target(keep) is True

    drop = NormalizedProperty(
        parcel_id="2",
        property_address="2 MAIN ST",
        city="Miami",
        state="FL",
        zip="33101",
        owner_name=None,
        mailing_address=None,
        property_type="0407 - RESIDENTIAL - TOTAL VALUE : CONDOMINIUM - RESIDENTIAL",
        year_built=None,
        last_sale_date=None,
        assessed_value=None,
    )
    assert normalized_property_is_target(drop) is False
