from datetime import date

import pytest

from skypoints.staging import age_on, parse_mmddyyyy, parse_yyyymmdd


def test_dates():
    assert parse_yyyymmdd("20101012") == date(2010, 10, 12)
    assert parse_mmddyyyy("03051985") == date(1985, 3, 5)
    assert parse_mmddyyyy("3051985") == date(1985, 3, 5)  # leading zero dropped in sample staging
    with pytest.raises(ValueError):
        parse_yyyymmdd("20211313")


def test_age():
    assert age_on(date(1985, 3, 5), date(2024, 3, 4)) == 38
    assert age_on(date(1985, 3, 5), date(2024, 3, 5)) == 39
