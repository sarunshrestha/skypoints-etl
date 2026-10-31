import io
from datetime import datetime

import pytest

from skypoints.filename import FileNameError, parse_file_name
from skypoints.parser import FileSummary, FlatFileError, iter_member_file, read_member_file

from conftest import HEADER, SAMPLE, detail


def test_file_name_spec():
    f = parse_file_name("SKYPOINTS_MBR_20240115_02300045.dat")
    assert f.kind == "MBR"
    assert f.produced_at == datetime(2024, 1, 15, 2, 30, 0, 450_000)


@pytest.mark.parametrize("name", [
    "members.dat",
    "SKYPOINTS_MBR_20240115_0230.dat",
    "SKYPOINTS_MBR_20241315_02300000.dat",     # month 13
    "SKYPOINTS_RDM_20240115_02300000.dat",     # wrong extension for kind
])
def test_bad_file_names_rejected(name):
    with pytest.raises(FileNameError):
        parse_file_name(name)


def test_sample_file_parses_all_five_rows():
    records, summary = read_member_file(SAMPLE / "SKYPOINTS_MBR_20240115_02300000.dat")
    assert [r.values["member_name"] for r in records] == ["Elena", "Ravi", "Mateo", "Nora", "Jacob"]
    assert records[0].values["country"] == "USA"
    assert records[0].src_row_number == 2
    assert summary.header_issues == []


def test_header_driven_mapping_survives_added_column():
    header = HEADER.replace("|Country|", "|Country|Post_Code|")
    text = header + "|D|Elena|223457|20101012|20121013|GLD|Sam|CA|USA|90210|03051985|A\n"
    (rec,) = iter_member_file(io.StringIO(text), "f")
    assert rec.values["post_code"] == "90210"
    assert rec.values["dob"] == "03051985"


def test_column_count_mismatch_flagged():
    (rec,) = iter_member_file(io.StringIO(HEADER + "|D|Elena|223457|20101012\n"), "f")
    assert any(i.startswith("COLUMN_COUNT_MISMATCH") for i in rec.issues)


def test_trailer_is_read():
    s = FileSummary()
    list(iter_member_file(io.StringIO(HEADER + detail() + "\n|T|1\n"), "f", s))
    assert s.trailer_count == 1 and s.detail_rows == 1


def test_detail_before_header_is_an_error():
    with pytest.raises(FlatFileError):
        list(iter_member_file(io.StringIO(detail() + "\n"), "f"))


def test_empty_fields_become_none():
    (rec,) = iter_member_file(io.StringIO(HEADER + detail(agent="", flight="") + "\n"), "f")
    assert rec.values["agent_name"] is None and rec.values["last_flight_date"] is None
