import io
from datetime import datetime

import pytest

from skypoints.parser import (
    FileNameError, FlatFileError, FileSummary, iter_member_file, parse_file_name, read_member_file,
)

from conftest import HEADER, SAMPLE, detail


def test_parse_filename():
    f = parse_file_name("SKYPOINTS_MBR_20240115_02300045.dat")
    assert f.kind == "MBR"
    assert f.produced_at == datetime(2024, 1, 15, 2, 30, 0, 450_000)


def test_bad_filename():
    with pytest.raises(FileNameError):
        parse_file_name("members.dat")


def test_sample_file():
    records, summary = read_member_file(SAMPLE / "SKYPOINTS_MBR_20240115_02300000.dat")
    assert len(records) == 5
    assert records[0].values["member_name"] == "Elena"
    assert records[0].values["country"] == "USA"


def test_extra_column_doesnt_shift_fields():
    header = HEADER.replace("|Country|", "|Country|Post_Code|")
    text = header + "|D|Elena|223457|20101012|20121013|GLD|Sam|CA|USA|90210|03051985|A\n"
    (rec,) = iter_member_file(io.StringIO(text), "f")
    assert rec.values["post_code"] == "90210"
    assert rec.values["dob"] == "03051985"


def test_trailer():
    s = FileSummary()
    list(iter_member_file(io.StringIO(HEADER + detail() + "\n|T|1\n"), "f", s))
    assert s.trailer_count == 1


def test_no_header():
    with pytest.raises(FlatFileError):
        list(iter_member_file(io.StringIO(detail() + "\n"), "f"))
