import io
from datetime import date, datetime

from skypoints.parser import iter_member_file
from skypoints.staging import Reject, StagedMember, stage_record

from conftest import HEADER, detail

TS = datetime(2024, 1, 15, 2, 30)


def stage(line, as_of=date(2024, 1, 15)):
    (raw,) = iter_member_file(io.StringIO(HEADER + line + "\n"), "f.dat")
    return stage_record(raw, TS, as_of)


def test_age_and_stale():
    m = stage(detail())
    assert m.age == 38
    assert m.stale_member is True
    assert m.country_code == "USA"


def test_phil_and_au_aliases():
    m = stage(detail(country="PHIL"))
    assert m.country_code == "PHL"
    assert "COUNTRY_CODE_NORMALISED:PHIL->PHL" in m.dq_warnings
    assert stage(detail(country="AU")).target_table == "TABLE_AUSTRALIA"


def test_rejects():
    assert isinstance(stage(detail(country="XYZ")), Reject)
    r = stage(detail(name="", mid="", enroll=""))
    assert "MANDATORY_MISSING:member_name" in r.reasons
    assert "INVALID_DATE:last_flight_date=20121313" in stage(detail(flight="20121313")).reasons


def test_warnings_from_sample_quirks():
    assert "MEMBER_ID_SHORTER_THAN_EXPECTED" in stage(detail(mid="2256")).dq_warnings
    m = stage(detail(dob="NULL"))
    assert m.dob is None
