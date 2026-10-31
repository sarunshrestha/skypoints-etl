"""RAW -> STAGING: typing, row-level validation, derived columns.

Mirrors sql/04_load_staging.sql. Each raw record ends up either as a
``StagedMember`` (possibly carrying soft warnings) or as a ``Reject`` with
every blocking reason listed — not just the first one found, so a source
team can fix a row in one round-trip.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from . import countries, dates
from .parser import RawRecord

VALID_TIERS = {"SLV", "GLD", "PLT"}
VALID_ACTIVE_FLAGS = {"A", "I"}
MANDATORY = ("member_name", "member_id", "enrollment_date")
MAX_LENGTH = {
    "member_name": 255, "member_id": 18, "tier_code": 5, "agent_name": 255,
    "state": 5, "country": 5, "post_code": 10, "is_active": 1,
}
EXPECTED_MIN_MEMBER_ID_LEN = 6
NULL_SENTINELS = {"NULL", "N/A", "NA", "NONE", "NIL", "-"}
_MEMBER_ID = re.compile(r"^[A-Za-z0-9-]+$")


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
    def order_key(self) -> tuple[datetime, int]:
        """Ordering for "latest record wins": file timestamp, then line number."""
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
    payload = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode()).hexdigest()


def _upper(v: str | None) -> str | None:
    return v.strip().upper() if v and v.strip() else None


def stage_record(raw: RawRecord, src_file_ts: datetime, as_of: date) -> StagedMember | Reject:
    reasons: list[str] = list(raw.issues)
    warnings: list[str] = []

    # Literal "NULL"/"N/A" strings are missing values, not data (AUS.xlsx
    # sends DOB = "NULL"). Normalise them up front, but record that we did.
    v = dict(raw.values)
    for k, val in v.items():
        if isinstance(val, str) and val.strip().upper() in NULL_SENTINELS:
            v[k] = None
            warnings.append(f"NULL_SENTINEL_STRING:{k}={val.strip()}")

    # --- mandatory -------------------------------------------------------
    for f in MANDATORY:
        if not v.get(f):
            reasons.append(f"MANDATORY_MISSING:{f}")

    # --- lengths / format ---------------------------------------------------
    for f, n in MAX_LENGTH.items():
        if v.get(f) and len(v[f]) > n:
            (reasons if f in ("member_name", "member_id") else warnings).append(f"LENGTH_EXCEEDED:{f}")
    member_id = v.get("member_id")
    if member_id and not _MEMBER_ID.match(member_id):
        reasons.append("INVALID_FORMAT:member_id")
    elif member_id and member_id.isdigit() and len(member_id) < EXPECTED_MIN_MEMBER_ID_LEN:
        warnings.append("MEMBER_ID_SHORTER_THAN_EXPECTED")

    # --- dates ------------------------------------------------------------
    def _parse(name: str, fn):
        try:
            return fn(v.get(name))
        except ValueError:
            reasons.append(f"INVALID_DATE:{name}={v.get(name)}")
            return None

    enrollment = _parse("enrollment_date", dates.parse_yyyymmdd)
    last_flight = _parse("last_flight_date", dates.parse_yyyymmdd)
    dob = _parse("dob", dates.parse_mmddyyyy)
    if dob and v.get("dob") and len(v["dob"].strip()) == 7:
        warnings.append("DOB_LEADING_ZERO_RESTORED")

    # --- country ----------------------------------------------------------
    country_raw = _upper(v.get("country"))
    country = countries.resolve(country_raw)
    if country is None:
        reasons.append(f"UNKNOWN_COUNTRY:{country_raw or '<null>'}")
    elif country.code != country_raw:
        warnings.append(f"COUNTRY_CODE_NORMALISED:{country_raw}->{country.code}")

    if reasons:
        return Reject(raw.src_file_name, raw.src_row_number, member_id, reasons, dict(raw.values))

    # --- soft checks ------------------------------------------------------
    tier = _upper(v.get("tier_code"))
    active = _upper(v.get("is_active"))
    if tier and tier not in VALID_TIERS:
        warnings.append(f"UNKNOWN_TIER:{tier}")
    if active and active not in VALID_ACTIVE_FLAGS:
        warnings.append(f"UNKNOWN_ACTIVE_FLAG:{active}")
    if last_flight and last_flight < enrollment:
        warnings.append("FLIGHT_BEFORE_ENROLLMENT")
    if dob and dob >= enrollment:
        warnings.append("DOB_NOT_BEFORE_ENROLLMENT")
    if last_flight and last_flight > as_of:
        warnings.append("FLIGHT_DATE_IN_FUTURE")
    if enrollment > as_of:
        warnings.append("ENROLLMENT_DATE_IN_FUTURE")

    age = dates.age_on(dob, as_of)
    if age is not None and not 0 <= age <= 120:
        warnings.append(f"AGE_OUT_OF_RANGE:{age}")
    days = dates.days_since_flight(last_flight, enrollment, as_of)

    member = StagedMember(
        member_id=member_id,
        member_name=v["member_name"].strip(),
        enrollment_date=enrollment,
        last_flight_date=last_flight,
        tier_code=tier,
        agent_name=(v.get("agent_name") or None),
        state=_upper(v.get("state")),
        country_code_raw=country_raw,
        country_code=country.code,
        target_table=country.target_table,
        post_code=v.get("post_code"),
        dob=dob,
        is_active=active,
        age=age,
        days_since_flight=days,
        stale_member=dates.is_stale(days),
        as_of_date=as_of,
        src_file_name=raw.src_file_name,
        src_file_ts=src_file_ts,
        src_row_number=raw.src_row_number,
        dq_warnings=warnings,
    )
    member.record_hash = _hash(member)
    return member


def stage_records(
    raws, src_file_ts: datetime, as_of: date
) -> tuple[list[StagedMember], list[Reject]]:
    staged, rejects = [], []
    for raw in raws:
        out = stage_record(raw, src_file_ts, as_of)
        (staged if isinstance(out, StagedMember) else rejects).append(out)
    return staged, rejects
