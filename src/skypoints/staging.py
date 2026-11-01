"""Type / validate member rows, compute age + stale flag."""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .parser import RawRecord

VALID_TIERS = {"SLV", "GLD", "PLT"}
VALID_ACTIVE = {"A", "I"}
MANDATORY = ("member_name", "member_id", "enrollment_date")
MAX_LEN = {
    "member_name": 255, "member_id": 18, "tier_code": 5, "agent_name": 255,
    "state": 5, "country": 5, "post_code": 10, "is_active": 1,
}
NULL_WORDS = {"NULL", "N/A", "NA", "NONE", "NIL", "-"}
_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")

# sample mixes ISO codes with PHIL / AU etc.
_COUNTRY = {
    "USA": ("USA", "TABLE_USA"), "US": ("USA", "TABLE_USA"),
    "IND": ("IND", "TABLE_INDIA"), "IN": ("IND", "TABLE_INDIA"),
    "PHL": ("PHL", "TABLE_PHILIPPINES"), "PH": ("PHL", "TABLE_PHILIPPINES"),
    "PHIL": ("PHL", "TABLE_PHILIPPINES"),
    "CAN": ("CAN", "TABLE_CANADA"), "CA": ("CAN", "TABLE_CANADA"),
    "AUS": ("AUS", "TABLE_AUSTRALIA"), "AU": ("AUS", "TABLE_AUSTRALIA"),
}


def parse_yyyymmdd(value):
    if value is None or not str(value).strip():
        return None
    s = str(value).strip()
    if len(s) != 8 or not s.isdigit():
        raise ValueError(s)
    return datetime.strptime(s, "%Y%m%d").date()


def parse_mmddyyyy(value):
    # DOB is MMDDYYYY; staging sample had 3051985 (lost leading 0)
    if value is None or not str(value).strip():
        return None
    s = str(value).strip()
    if not s.isdigit() or len(s) not in (7, 8):
        raise ValueError(s)
    return datetime.strptime(s.zfill(8), "%m%d%Y").date()


def age_on(dob, as_of):
    if dob is None:
        return None
    years = as_of.year - dob.year
    if (as_of.month, as_of.day) < (dob.month, dob.day):
        years -= 1
    return years


@dataclass
class StagedMember:
    member_id: str
    member_name: str
    enrollment_date: date
    last_flight_date: date | None
    tier_code: str | None
    agent_name: str | None
    state: str | None
    country_code_raw: str | None
    country_code: str
    target_table: str
    post_code: str | None
    dob: date | None
    is_active: str | None
    age: int | None
    days_since_flight: int | None
    stale_member: bool | None
    as_of_date: date
    src_file_name: str
    src_file_ts: datetime
    src_row_number: int
    record_hash: str = ""
    dq_warnings: list[str] = field(default_factory=list)

    @property
    def order_key(self):
        return (self.src_file_ts, self.src_row_number)


@dataclass
class Reject:
    src_file_name: str
    src_row_number: int
    member_id: str | None
    reasons: list[str]
    raw_record: dict


def _hash(m: StagedMember) -> str:
    parts = [m.member_name, m.member_id, m.enrollment_date, m.last_flight_date, m.tier_code,
             m.agent_name, m.state, m.country_code, m.post_code, m.dob, m.is_active]
    return hashlib.sha256("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()


def stage_record(raw: RawRecord, src_file_ts: datetime, as_of: date) -> StagedMember | Reject:
    reasons = list(raw.issues)
    warnings = []
    v = dict(raw.values)
    for k, val in v.items():
        if isinstance(val, str) and val.strip().upper() in NULL_WORDS:
            warnings.append(f"NULL_SENTINEL_STRING:{k}={val.strip()}")
            v[k] = None

    for f in MANDATORY:
        if not v.get(f):
            reasons.append(f"MANDATORY_MISSING:{f}")

    for f, n in MAX_LEN.items():
        if v.get(f) and len(v[f]) > n:
            (reasons if f in ("member_name", "member_id") else warnings).append(f"LENGTH_EXCEEDED:{f}")

    mid = v.get("member_id")
    if mid and not _ID_RE.match(mid):
        reasons.append("INVALID_FORMAT:member_id")
    elif mid and mid.isdigit() and len(mid) < 6:
        warnings.append("MEMBER_ID_SHORTER_THAN_EXPECTED")

    def _parse(name, fn):
        try:
            return fn(v.get(name))
        except ValueError:
            reasons.append(f"INVALID_DATE:{name}={v.get(name)}")
            return None

    enrollment = _parse("enrollment_date", parse_yyyymmdd)
    last_flight = _parse("last_flight_date", parse_yyyymmdd)
    dob = _parse("dob", parse_mmddyyyy)
    if dob and v.get("dob") and len(v["dob"].strip()) == 7:
        warnings.append("DOB_LEADING_ZERO_RESTORED")

    country_raw = v.get("country").strip().upper() if v.get("country") and v.get("country").strip() else None
    mapped = _COUNTRY.get(country_raw) if country_raw else None
    if mapped is None:
        reasons.append(f"UNKNOWN_COUNTRY:{country_raw or '<null>'}")
    elif mapped[0] != country_raw:
        warnings.append(f"COUNTRY_CODE_NORMALISED:{country_raw}->{mapped[0]}")

    if reasons:
        return Reject(raw.src_file_name, raw.src_row_number, mid, reasons, dict(raw.values))

    tier = v.get("tier_code").strip().upper() if v.get("tier_code") and v.get("tier_code").strip() else None
    active = v.get("is_active").strip().upper() if v.get("is_active") and v.get("is_active").strip() else None
    if tier and tier not in VALID_TIERS:
        warnings.append(f"UNKNOWN_TIER:{tier}")
    if active and active not in VALID_ACTIVE:
        warnings.append(f"UNKNOWN_ACTIVE_FLAG:{active}")
    if last_flight and last_flight < enrollment:
        warnings.append("FLIGHT_BEFORE_ENROLLMENT")
    if dob and dob >= enrollment:
        warnings.append("DOB_NOT_BEFORE_ENROLLMENT")
    if last_flight and last_flight > as_of:
        warnings.append("FLIGHT_DATE_IN_FUTURE")
    if enrollment > as_of:
        warnings.append("ENROLLMENT_DATE_IN_FUTURE")

    age = age_on(dob, as_of)
    if age is not None and not 0 <= age <= 120:
        warnings.append(f"AGE_OUT_OF_RANGE:{age}")
    anchor = last_flight or enrollment
    days = (as_of - anchor).days if anchor else None
    stale = None if days is None else days > 90

    member = StagedMember(
        member_id=mid,
        member_name=v["member_name"].strip(),
        enrollment_date=enrollment,
        last_flight_date=last_flight,
        tier_code=tier,
        agent_name=(v.get("agent_name") or None),
        state=v.get("state").strip().upper() if v.get("state") and v.get("state").strip() else None,
        country_code_raw=country_raw,
        country_code=mapped[0],
        target_table=mapped[1],
        post_code=v.get("post_code"),
        dob=dob,
        is_active=active,
        age=age,
        days_since_flight=days,
        stale_member=stale,
        as_of_date=as_of,
        src_file_name=raw.src_file_name,
        src_file_ts=src_file_ts,
        src_row_number=raw.src_row_number,
        dq_warnings=warnings,
    )
    member.record_hash = _hash(member)
    return member


def stage_records(raws, src_file_ts: datetime, as_of: date):
    staged, rejects = [], []
    for raw in raws:
        out = stage_record(raw, src_file_ts, as_of)
        (staged if isinstance(out, StagedMember) else rejects).append(out)
    return staged, rejects
