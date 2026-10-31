from datetime import date

import pytest

from skypoints import dates


def test_yyyymmdd_parses_and_rejects_impossible_dates():
    assert dates.parse_yyyymmdd("20101012") == date(2010, 10, 12)
    assert dates.parse_yyyymmdd("  ") is None
    with pytest.raises(ValueError):
        dates.parse_yyyymmdd("20211313")
    with pytest.raises(ValueError):
        dates.parse_yyyymmdd("2021-12-13")


def test_dob_is_mmddyyyy_not_yyyymmdd():
    assert dates.parse_mmddyyyy("03051985") == date(1985, 3, 5)


def test_dob_with_lost_leading_zero_is_restored():
    # brief's staging table shows 3051985 for source 03051985
    assert dates.parse_mmddyyyy("3051985") == date(1985, 3, 5)


@pytest.mark.parametrize("bad", ["305198", "123456789", "13011985", "02301985", "abc"])
def test_dob_invalid(bad):
    with pytest.raises(ValueError):
        dates.parse_mmddyyyy(bad)


@pytest.mark.parametrize(
    "dob, as_of, expected",
    [
        (date(1985, 3, 5), date(2024, 3, 4), 38),   # day before birthday
        (date(1985, 3, 5), date(2024, 3, 5), 39),   # on birthday
        (date(2000, 12, 31), date(2001, 1, 1), 0),  # calendar-year diff would say 1
        (date(2000, 2, 29), date(2023, 2, 28), 22), # leap-day birthday, non-leap year
        (date(2000, 2, 29), date(2023, 3, 1), 23),
    ],
)
def test_age_is_birthday_aware(dob, as_of, expected):
    assert dates.age_on(dob, as_of) == expected


def test_age_none_without_dob():
    assert dates.age_on(None, date(2024, 1, 1)) is None


def test_stale_boundary_is_strictly_greater_than_90():
    assert dates.is_stale(90) is False
    assert dates.is_stale(91) is True


def test_never_flown_member_measured_from_enrollment():
    assert dates.days_since_flight(None, date(2024, 1, 1), date(2024, 4, 1)) == 91
    assert dates.days_since_flight(date(2024, 3, 1), date(2024, 1, 1), date(2024, 4, 1)) == 31
