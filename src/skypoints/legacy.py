"""Load INDin.csv / USAin.csv / AUS.xlsx into the same RawRecord shape."""

import csv
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from .parser import RawRecord


def _fmt(d, pattern):
    return None if d is None else d.strftime(pattern)


def _try(fn, raw):
    if raw is None or str(raw).strip() == "":
        return None, None
    try:
        return fn(raw), None
    except Exception:
        return None, str(raw).strip()


def _mdy_slash(s):
    return datetime.strptime(str(s).strip(), "%m/%d/%Y").date()


def _mdy_int(s):
    # USA file stored dates as ints so leading zeros are gone
    return datetime.strptime(str(int(s)).zfill(8), "%m%d%Y").date()


def _excel(s):
    if isinstance(s, datetime):
        return s.date()
    return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()


def _row(src, line, mid, name, enroll, flight, dob, tier, country, extras=None):
    e, e_bad = enroll
    f, f_bad = flight
    d, d_bad = dob
    values = {
        "member_name": name,
        "member_id": mid,
        "enrollment_date": e_bad or _fmt(e, "%Y%m%d"),
        "last_flight_date": f_bad or _fmt(f, "%Y%m%d"),
        "tier_code": tier,
        "agent_name": None,
        "state": None,
        "country": country,
        "post_code": None,
        "dob": d_bad or _fmt(d, "%m%d%Y"),
        "is_active": "A",
        "extras": extras or {},
    }
    return RawRecord(values, src, line, "")


def read_india(path: Path) -> list[RawRecord]:
    out = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            out.append(_row(
                path.name, i, f"IND-{row['ID']}", row["Name"],
                _try(_mdy_slash, row.get("EnrollmentDate")),
                _try(_mdy_slash, row.get("Flight Date")),
                _try(_mdy_slash, row.get("DOB")),
                row.get("TierCode"), "IND",
                {"member_type": row.get("Individual or Corporate")},
            ))
    return out


def read_usa(path: Path) -> list[RawRecord]:
    out = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            out.append(_row(
                path.name, i, f"USA-{row['ID']}", row["Name"],
                _try(_mdy_int, row.get("EnrollmentDate")),
                _try(_mdy_int, row.get("FlightDate")),
                (None, None),
                row.get("TierCode"), "USA",
            ))
    return out


def read_australia(path: Path) -> list[RawRecord]:
    wb = load_workbook(path, read_only=True, data_only=True)
    rows = list(wb.active.iter_rows(values_only=True))
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    out = []
    for i, raw in enumerate(rows[1:], start=2):
        row = dict(zip(header, raw))
        dob_raw = row.get("Date of Birth")
        if isinstance(dob_raw, str) and dob_raw.strip().upper() in {"NULL", "N/A", "NA"}:
            dob_pair, dob_literal = (None, None), dob_raw.strip()
        else:
            dob_pair, dob_literal = _try(_excel, dob_raw), None
        rec = _row(
            path.name, i, f"AUS-{row['Unique ID']}", row.get("Member Name"),
            _try(_excel, row.get("Date of Enrollment")),
            _try(_excel, row.get("Date of Flight")),
            dob_pair,
            row.get("Tier Type"), "AUS",
        )
        if dob_literal is not None:
            rec.values["dob"] = dob_literal
        out.append(rec)
    return out


ADAPTERS = {"INDin.csv": read_india, "USAin.csv": read_usa, "AUS.xlsx": read_australia}
