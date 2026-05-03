from __future__ import annotations

import pytest

from motivation.signals import (
    MotivationSignals,
    compute_motivation_signals,
    mailing_state_from_line,
    situs_one_line,
)
from normalize.models import NormalizedProperty


def _prop(**kwargs: object) -> NormalizedProperty:
    defaults: dict[str, object] = {
        "parcel_id": "1",
        "property_address": "100 MAIN ST",
        "city": "Miami",
        "state": "FL",
        "zip": "33130",
        "owner_name": None,
        "mailing_address": "100 MAIN ST, Miami, FL 33130",
        "property_type": "0101 - RESIDENTIAL",
        "year_built": 1990,
        "last_sale_date": "2020-01-15",
        "assessed_value": None,
    }
    defaults.update(kwargs)
    return NormalizedProperty(**defaults)  # type: ignore[arg-type]


def test_absentee_false_when_situs_matches_mailing() -> None:
    s = compute_motivation_signals(_prop(), as_of_year=2026)
    assert s.absentee_owner is False


def test_absentee_true_when_mailing_differs() -> None:
    s = compute_motivation_signals(
        _prop(mailing_address="200 OTHER RD, Atlanta, GA 30303"),
        as_of_year=2026,
    )
    assert s.absentee_owner is True


def test_absentee_none_when_mailing_missing() -> None:
    s = compute_motivation_signals(_prop(mailing_address=None), as_of_year=2026)
    assert s.absentee_owner is None


def test_zip_plus_four_normalization_no_false_absentee() -> None:
    s = compute_motivation_signals(
        _prop(zip="33130-1234", mailing_address="100 MAIN ST, Miami, FL 33130"),
        as_of_year=2026,
    )
    assert s.absentee_owner is False


def test_out_of_state_true() -> None:
    s = compute_motivation_signals(
        _prop(mailing_address="PO BOX 1, New York, NY 10001"),
        as_of_year=2026,
    )
    assert s.out_of_state_owner is True


def test_out_of_state_false_fl_mailing() -> None:
    s = compute_motivation_signals(
        _prop(mailing_address="Tampa, FL 33601"),
        as_of_year=2026,
    )
    assert s.out_of_state_owner is False


def test_out_of_state_none_when_unparsed() -> None:
    s = compute_motivation_signals(_prop(mailing_address="UNKNOWN"), as_of_year=2026)
    assert s.out_of_state_owner is None


def test_years_owned_from_last_sale() -> None:
    s = compute_motivation_signals(_prop(last_sale_date="2021-06-23"), as_of_year=2026)
    assert s.years_owned == 5


def test_years_owned_none_without_sale() -> None:
    s = compute_motivation_signals(_prop(last_sale_date=None), as_of_year=2026)
    assert s.years_owned is None


def test_old_property_threshold() -> None:
    assert compute_motivation_signals(_prop(year_built=1980), as_of_year=2026).old_property is True
    assert compute_motivation_signals(_prop(year_built=1981), as_of_year=2026).old_property is False
    assert compute_motivation_signals(_prop(year_built=None), as_of_year=2026).old_property is False


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (None, None),
        ("", None),
        ("100 MAIN ST, Miami, FL 33130", "FL"),
        ("Tampa, FL 33601", "FL"),
        ("NO STATE HERE", None),
    ],
)
def test_mailing_state_from_line(line: str | None, expected: str | None) -> None:
    assert mailing_state_from_line(line) == expected


def test_situs_one_line_matches_mapper_shape() -> None:
    p = _prop(
        property_address="16 SE 2 ST",
        city="Miami",
        state="FL",
        zip="33131-0000",
    )
    assert situs_one_line(p) == "16 SE 2 ST, Miami, FL 33131-0000"


def test_motivation_signals_to_dict() -> None:
    s = MotivationSignals(
        absentee_owner=False,
        out_of_state_owner=False,
        years_owned=3,
        old_property=True,
    )
    assert s.to_dict() == {
        "absentee_owner": False,
        "out_of_state_owner": False,
        "years_owned": 3,
        "old_property": True,
    }
