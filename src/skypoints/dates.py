"""Date parsing and the two derived metrics (age, staleness).

The feed uses two different date layouts:
  * enrollment / flight dates : YYYYMMDD
  * date of birth             : MMDDYYYY
and somewhere upstream DOB is sometimes cast to an integer, dropping the
leading zero of the month (``03051985`` -> ``3051985``). Because only a
*leading* zero can be lost, a 7-digit value is always a single-digit month
and padding back to 8 digits is unambiguous.
"""
from __future__ import annotations

from datetime import date, datetime

STALE_AFTER_DAYS = 90


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def parse_yyyymmdd(value: str | None) -> date | None:
    """Parse ``YYYYMMDD``. Returns None for blank; raises ValueError if invalid."""
    value = _strip(value)
    if value is None:
        return None
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"expected YYYYMMDD, got {value!r}")
    return datetime.strptime(value, "%Y%m%d").date()


def parse_mmddyyyy(value: str | None) -> date | None:
    """Parse ``MMDDYYYY`` restoring a lost leading zero (7 digits -> 8)."""
    value = _strip(value)
    if value is None:
        return None
    if not value.isdigit() or len(value) not in (7, 8):
        raise ValueError(f"expected MMDDYYYY, got {value!r}")
    return datetime.strptime(value.zfill(8), "%m%d%Y").date()


def age_on(dob: date | None, as_of: date) -> int | None:
    """Completed years between ``dob`` and ``as_of`` (birthday-aware)."""
    if dob is None:
        return None
    had_birthday = (as_of.month, as_of.day) >= (dob.month, dob.day)
    return as_of.year - dob.year - (0 if had_birthday else 1)


def days_since_flight(last_flight: date | None, enrollment: date | None, as_of: date) -> int | None:
    """Days since last flight; a member who never flew is measured from enrollment."""
    anchor = last_flight or enrollment
    return None if anchor is None else (as_of - anchor).days


def is_stale(days: int | None) -> bool | None:
    return None if days is None else days > STALE_AFTER_DAYS
