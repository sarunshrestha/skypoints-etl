from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"
LEGACY = ROOT / "data" / "legacy"
AS_OF = date(2024, 1, 15)

HEADER = "|H|Member_Name|Member_Id|Enrollment_Date|Last_Flight_Date|Tier_Code|Agent_Name|State|Country|DOB|Is_Active\n"


@pytest.fixture
def as_of():
    return AS_OF


@pytest.fixture
def make_feed(tmp_path):
    """Write a member flat file with the spec's naming convention."""
    def _make(lines, ts="20240115_02300000", header=HEADER):
        p = tmp_path / f"SKYPOINTS_MBR_{ts}.dat"
        p.write_text(header + "".join(l if l.endswith("\n") else l + "\n" for l in lines))
        return p
    return _make


def detail(name="Elena", mid="223457", enroll="20101012", flight="20121013", tier="GLD",
           agent="Sam", state="CA", country="USA", dob="03051985", active="A"):
    return f"|D|{name}|{mid}|{enroll}|{flight}|{tier}|{agent}|{state}|{country}|{dob}|{active}"


def ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y%m%d%H%M%S")
