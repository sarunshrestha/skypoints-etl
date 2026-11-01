import csv
import json
from datetime import date

from skypoints import legacy
from skypoints.pipeline import run
from skypoints.staging import Reject, StagedMember, stage_records

from conftest import LEGACY, SAMPLE, ts

AS_OF = date(2024, 1, 15)


def test_usa_integer_dates_with_lost_leading_zero():
    recs = legacy.read_usa(LEGACY / "USAin.csv")
    john = next(r for r in recs if r.values["member_name"] == "John")
    assert john.values["enrollment_date"] == "20220105"     # 1052022 -> 01/05/2022
    assert john.values["last_flight_date"] == "20220115"


def test_legacy_ids_are_namespaced_so_they_do_not_collide():
    ids = [r.values["member_id"] for f in (legacy.read_india, legacy.read_usa)
           for r in f(LEGACY / ("INDin.csv" if f is legacy.read_india else "USAin.csv"))]
    assert "IND-1" in ids and "USA-1" in ids and len(ids) == len(set(ids))


def test_australia_month_13_rejected_and_null_dob_kept():
    staged, rejects = stage_records(legacy.read_australia(LEGACY / "AUS.xlsx"), ts("20221231000000"), AS_OF)
    assert [r.member_name for r in staged] == ["Mike", "Cristina"]
    assert staged[0].dob is None
    (j,) = rejects
    assert j.reasons == ["INVALID_DATE:enrollment_date=2021-13-13"]


def test_india_dates_and_extras():
    (vikas, *_) = legacy.read_india(LEGACY / "INDin.csv")
    assert vikas.values["dob"] == "12011998"
    assert vikas.values["extras"] == {"member_type": "Individual"}


def test_end_to_end_run(tmp_path):
    report = run(SAMPLE, LEGACY, tmp_path, AS_OF)
    assert all(report.invariants.values())
    assert report.table_counts == {
        "TABLE_AUSTRALIA": 3, "TABLE_CANADA": 1, "TABLE_INDIA": 4,
        "TABLE_PHILIPPINES": 1, "TABLE_USA": 4,
    }
    assert report.rejected == 1
    assert report.redemptions == {"transactions": 2, "rejected": 0, "orphans": 0}

    with (tmp_path / "TABLE_INDIA.csv").open() as fh:
        india = {r["member_id"]: r for r in csv.DictReader(fh)}
    assert india["223458"]["member_name"] == "Ravi"
    assert india["223458"]["age"] == "38"
    assert india["223458"]["stale_member"] == "True"

    with (tmp_path / "REDEMPTION_ENRICHED.csv").open() as fh:
        enriched = list(csv.DictReader(fh))
    assert {r["member_name"] for r in enriched} == {"Elena"}
    assert json.loads((tmp_path / "dq_report.json").read_text())["staged"] == 13


def test_spec_key_problem_is_detected(tmp_path):
    report = run(SAMPLE, LEGACY, tmp_path, AS_OF)
    names = {c["name"]: c for c in report.batch_checks}
    assert "MIKE" in names["SPEC_KEY_MEMBER_NAME_NOT_UNIQUE"]["details"]
    assert names["SUSPICIOUS_CONSTANT_COLUMN"]["failed"] >= 3


def test_day_two_move_reject_and_orphan(tmp_path):
    report = run([SAMPLE, SAMPLE.parent / "demo_day2"], LEGACY, tmp_path, date(2024, 1, 16))
    assert [(m["member_id"], m["from_country"], m["to_country"]) for m in report.moves] == [("223458", "IND", "CAN")]
    with (tmp_path / "TABLE_INDIA.csv").open() as fh:
        assert "223458" not in {r["member_id"] for r in csv.DictReader(fh)}
    assert report.reject_reasons == {"INVALID_DATE": 2, "MANDATORY_MISSING": 1, "UNKNOWN_COUNTRY": 1}
    assert report.redemptions == {"transactions": 3, "rejected": 1, "orphans": 1}
    with (tmp_path / "REDEMPTION_ENRICHED.csv").open() as fh:
        status = {r["txn_id"]: r["status"] for r in csv.DictReader(fh)}
    assert status["RX10092"] == "COMPLETED"
    assert all(report.invariants.values())
