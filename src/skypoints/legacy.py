"""Adapters for the legacy per-country extracts (INDin.csv, USAin.csv, AUS.xlsx).

These files predate the unified feed and each has its own layout and date
format. Each adapter converts its rows into canonical ``RawRecord`` values
(dates re-emitted as YYYYMMDD / MMDDYYYY strings) so they flow through the
*same* staging validation as the main feed — one set of rules, not four.

Issues found in these files (all surfaced by the pipeline, see README):
  * ``ID`` is 1,2,3 in every file -> a per-file row number, NOT a global
    member id. Used as-is, India #1 (Vikas) and USA #1 (Sam) and AUS #1
    (Mike) would be treated as one member who "moved country" twice.
    The adapter namespaces it: ``IND-1``, ``USA-1``, ``AUS-1``.
  * USA dates are MMDDYYYY stored as integers, so the month's leading zero
    is gone (``1052022`` = 01/05/2022). zfill(8) restores it unambiguously.
  * USA has no DOB column; AUS has a blank DOB for Mike.
  * AUS 'Date of Enrollment' for Jonnathan is ``2021-13-13`` (month 13):
    a mandatory field with an impossible value -> rejected.
  * IND carries an extra 'Individual or Corporate' attribute that the
    unified layout has no column for (kept in ``extras``; needs a decision).
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from .parser import RawRecord


def _fmt(d: date | None, layout: str) -> str | None:
    return None if d is None else d.strftime(layout)


def _try(fn, value):
    """Return (parsed date or None, original-string-if-unparseable)."""
    if value is None or str(value).strip() == "":
        return None, None
    try:
        return fn(value), None
    except (ValueError, TypeError):
        return None, str(value).strip()


def _mdy_slash(v) -> date:          # 12/1/1998
    return datetime.strptime(str(v).strip(), "%m/%d/%Y").date()


def _mdy_int(v) -> date:            # 1052022 -> 01052022
    s = str(v).strip()
    if not s.isdigit() or len(s) not in (7, 8):
        raise ValueError(s)
    return datetime.strptime(s.zfill(8), "%m%d%Y").date()


def _excel(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v).strip(), "%Y-%m-%d").date()


def _canonical(country: str, src_id, name, tier, enroll, flight, dob, extras=None) -> dict:
    """Build canonical values. Unparseable dates are passed through verbatim
    so staging rejects them with the original value in the reason."""
    e, e_bad = enroll
    f, f_bad = flight
    b, b_bad = dob
    values = {
        "member_name": (str(name).strip() or None) if name is not None else None,
        "member_id": f"{country}-{str(src_id).strip()}" if src_id not in (None, "") else None,
        "enrollment_date": e_bad or _fmt(e, "%Y%m%d"),
        "last_flight_date": f_bad or _fmt(f, "%Y%m%d"),
        "tier_code": (str(tier).strip() or None) if tier is not None else None,
        "agent_name": None, "state": None, "country": country, "post_code": None,
        "dob": b_bad or _fmt(b, "%m%d%Y"),
        "is_active": None,
    }
    if extras:
        values["extras"] = extras
    return values


def read_india(path: str | Path) -> list[RawRecord]:
    path = Path(path)
    out = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            vals = _canonical(
                "IND", row.get("ID"), row.get("Name"), row.get("TierCode"),
                _try(_mdy_slash, row.get("EnrollmentDate")),
                _try(_mdy_slash, row.get("Flight Date")),
                _try(_mdy_slash, row.get("DOB")),
                extras={"member_type": {"I": "Individual", "C": "Corporate"}.get(
                    (row.get("Individual or Corporate") or "").strip(), None)},
            )
            out.append(RawRecord(vals, path.name, i, ",".join(row.values())))
    return out


def read_usa(path: str | Path) -> list[RawRecord]:
    path = Path(path)
    out = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            vals = _canonical(
                "USA", row.get("ID"), row.get("Name"), row.get("TierCode"),
                _try(_mdy_int, row.get("EnrollmentDate")),
                _try(_mdy_int, row.get("FlightDate")),
                (None, None),                                   # no DOB column
            )
            out.append(RawRecord(vals, path.name, i, ",".join(row.values())))
    return out


def read_australia(path: str | Path) -> list[RawRecord]:
    from openpyxl import load_workbook

    path = Path(path)
    ws = load_workbook(path, read_only=True, data_only=True).active
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    out = []
    for i, values in enumerate(rows, start=2):
        row = dict(zip(header, values))
        if all(v is None for v in values):
            continue
        vals = _canonical(
            "AUS", row.get("Unique ID"), row.get("Member Name"), row.get("Tier Type"),
            _try(_excel, row.get("Date of Enrollment")),
            _try(_excel, row.get("Date of Flight")),
            _try(_excel, row.get("Date of Birth")),
        )
        out.append(RawRecord(vals, path.name, i, repr(values)))
    return out


ADAPTERS = {"INDin.csv": read_india, "USAin.csv": read_usa, "AUS.xlsx": read_australia}
