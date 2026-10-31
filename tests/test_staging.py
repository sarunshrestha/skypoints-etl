import io
from datetime import date, datetime

from skypoints.parser import iter_member_file
from skypoints.staging import Reject, StagedMember, stage_record

from conftest import HEADER, detail

TS = datetime(2024, 1, 15, 2, 30)


def stage(line, as_of=date(2024, 1, 15)):
    (raw,) = iter_member_file(io.StringIO(HEADER + line + "\n"), "f.dat")
    return stage_record(raw, TS, as_of)


def test_happy_path_derives_age_and_stale():
    m = stage(detail())
    assert isinstance(m, StagedMember)
    assert m.age == 38                      # born 1985-03-05, as of 2024-01-15
    assert m.stale_member is True           # last flight 2012
    assert m.country_code == "USA" and m.target_table == "TABLE_USA"
    assert m.dq_warnings == []


def test_recent_flyer_not_stale():
    m = stage(detail(flight="20231201"))
    assert m.days_since_flight == 45 and m.stale_member is False


def test_country_aliases_from_sample_are_normalised_with_warning():
    m = stage(detail(country="PHIL"))
    assert m.country_code == "PHL" and m.target_table == "TABLE_PHILIPPINES"
    assert "COUNTRY_CODE_NORMALISED:PHIL->PHL" in m.dq_warnings
    assert stage(detail(country="AU")).target_table == "TABLE_AUSTRALIA"


def test_unknown_country_is_rejected_because_it_cannot_be_routed():
    r = stage(detail(country="XYZ"))
    assert isinstance(r, Reject) and "UNKNOWN_COUNTRY:XYZ" in r.reasons


def test_all_mandatory_failures_are_reported_together():
    r = stage(detail(name="", mid="", enroll=""))
    assert isinstance(r, Reject)
    assert {"MANDATORY_MISSING:member_name", "MANDATORY_MISSING:member_id",
            "MANDATORY_MISSING:enrollment_date"} <= set(r.reasons)


def test_optional_fields_may_be_blank():
    m = stage(detail(flight="", tier="", agent="", state="", dob="", active=""))
    assert isinstance(m, StagedMember)
    assert m.age is None
    assert m.days_since_flight == (date(2024, 1, 15) - date(2010, 10, 12)).days


def test_invalid_optional_date_still_rejects():
    # a value that is present but garbage is not "missing" — don't silently null it
    r = stage(detail(flight="20121313"))
    assert isinstance(r, Reject) and "INVALID_DATE:last_flight_date=20121313" in r.reasons


def test_short_member_ids_from_sample_are_warned():
    assert "MEMBER_ID_SHORTER_THAN_EXPECTED" in stage(detail(mid="2256")).dq_warnings


def test_member_id_too_long_is_rejected():
    assert "LENGTH_EXCEEDED:member_id" in stage(detail(mid="1" * 19)).reasons


def test_logical_date_checks():
    m = stage(detail(enroll="20150101", flight="20140101", dob="01012020"))
    assert "FLIGHT_BEFORE_ENROLLMENT" in m.dq_warnings
    assert "DOB_NOT_BEFORE_ENROLLMENT" in m.dq_warnings


def test_unknown_tier_and_flag_warned():
    m = stage(detail(tier="BRZ", active="X"))
    assert "UNKNOWN_TIER:BRZ" in m.dq_warnings and "UNKNOWN_ACTIVE_FLAG:X" in m.dq_warnings


def test_null_sentinel_string_treated_as_missing():
    m = stage(detail(dob="NULL"))
    assert isinstance(m, StagedMember) and m.dob is None
    assert "NULL_SENTINEL_STRING:dob=NULL" in m.dq_warnings


def test_record_hash_changes_with_content_only():
    a, b, c = stage(detail()), stage(detail()), stage(detail(tier="PLT"))
    assert a.record_hash == b.record_hash != c.record_hash
