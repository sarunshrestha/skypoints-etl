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


def test_members_land_in_their_country_table():
    t = CountryTables()
    t.apply([rec("2024-01-01", mid="1", country="IND"), rec("2024-01-01", mid="2", country="USA")])
    assert set(t.tables["TABLE_INDIA"]) == {"1"}
    assert set(t.tables["TABLE_USA"]) == {"2"}


def test_member_who_moves_country_is_removed_from_old_table():
    t = CountryTables()
    t.apply([rec("2024-01-01", mid="7", country="IND")])
    res = t.apply([rec("2024-02-01", mid="7", country="AU")])
    assert res.moved == 1
    assert t.where_is("7") == ["TABLE_AUSTRALIA"]
    assert t.moves[0].from_country == "IND" and t.moves[0].to_country == "AUS"


def test_latest_wins_within_a_single_batch_by_row_number():
    t = CountryTables()
    res = t.apply([rec("2024-01-01", row=2, mid="7", country="IND"),
                   rec("2024-01-01", row=9, mid="7", country="CAN")])
    assert t.where_is("7") == ["TABLE_CANADA"]
    assert res.superseded_in_batch == 1


def test_late_arriving_older_file_does_not_overwrite_newer_data():
    t = CountryTables()
    t.apply([rec("2024-03-01", mid="7", country="CAN", tier="PLT")])
    res = t.apply([rec("2024-01-01", mid="7", country="IND", tier="SLV")])
    assert res.ignored_older == 1
    assert t.where_is("7") == ["TABLE_CANADA"]
    assert t.tables["TABLE_CANADA"]["7"].tier_code == "PLT"


def test_result_is_independent_of_file_arrival_order():
    files = [
        [rec("2024-01-01", mid="7", country="IND")],
        [rec("2024-02-01", mid="7", country="USA")],
        [rec("2024-03-01", mid="7", country="PHIL")],
    ]
    forward, backward = CountryTables(), CountryTables()
    for f in files:
        forward.apply(f)
    for f in reversed(files):
        backward.apply(f)
    assert forward.where_is("7") == backward.where_is("7") == ["TABLE_PHILIPPINES"]


def test_replaying_the_same_file_is_idempotent():
    t = CountryTables()
    batch = [rec("2024-01-01", mid="1", country="IND")]
    t.apply(batch)
    res = t.apply(batch)
    assert res.ignored_older == 1 and res.inserted == 0
    assert len(t.tables["TABLE_INDIA"]) == 1


def test_update_vs_unchanged_detected_by_hash():
    t = CountryTables()
    t.apply([rec("2024-01-01", mid="1", country="IND", tier="SLV")])
    assert t.apply([rec("2024-01-02", mid="1", country="IND", tier="SLV")]).unchanged == 1
    assert t.apply([rec("2024-01-03", mid="1", country="IND", tier="GLD")]).updated == 1
