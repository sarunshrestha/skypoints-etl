import io
from datetime import date, datetime

from skypoints.parser import iter_member_file
from skypoints.router import CountryTables
from skypoints.staging import stage_record

from conftest import HEADER, detail

AS_OF = date(2024, 6, 1)


def rec(ts, row=2, **kw):
    (raw,) = iter_member_file(io.StringIO(HEADER + detail(**kw) + "\n"), f"f_{ts}.dat")
    raw.src_row_number = row
    return stage_record(raw, datetime.fromisoformat(ts), AS_OF)


def test_split_by_country():
    t = CountryTables()
    t.apply([rec("2024-01-01", mid="1", country="IND"), rec("2024-01-01", mid="2", country="USA")])
    assert "1" in t.tables["TABLE_INDIA"]
    assert "2" in t.tables["TABLE_USA"]


def test_country_move():
    t = CountryTables()
    t.apply([rec("2024-01-01", mid="7", country="IND")])
    t.apply([rec("2024-02-01", mid="7", country="AU")])
    assert t.where_is("7") == ["TABLE_AUSTRALIA"]
    assert t.moves[0].from_country == "IND"


def test_newer_file_wins():
    t = CountryTables()
    t.apply([rec("2024-03-01", mid="7", country="CAN", tier="PLT")])
    res = t.apply([rec("2024-01-01", mid="7", country="IND", tier="SLV")])
    assert res.ignored_older == 1
    assert t.tables["TABLE_CANADA"]["7"].tier_code == "PLT"


def test_later_line_wins_in_same_file():
    t = CountryTables()
    t.apply([rec("2024-01-01", row=2, mid="7", country="IND"),
             rec("2024-01-01", row=9, mid="7", country="CAN")])
    assert t.where_is("7") == ["TABLE_CANADA"]
