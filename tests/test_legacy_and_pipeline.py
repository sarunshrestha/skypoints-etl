import csv
import json
from datetime import date

from skypoints import legacy
from skypoints.pipeline import run
from skypoints.staging import stage_records

from conftest import LEGACY, SAMPLE, ts

AS_OF = date(2024, 1, 15)


def test_usa_dates():
    john = next(r for r in legacy.read_usa(LEGACY / "USAin.csv") if r.values["member_name"] == "John")
    assert john.values["enrollment_date"] == "20220105"


def test_legacy_ids_dont_collide():
    assert legacy.read_india(LEGACY / "INDin.csv")[0].values["member_id"] == "IND-1"
    assert legacy.read_usa(LEGACY / "USAin.csv")[0].values["member_id"] == "USA-1"


def test_aus_bad_enrollment_rejected():
    staged, rejects = stage_records(legacy.read_australia(LEGACY / "AUS.xlsx"), ts("20221231000000"), AS_OF)
    assert any("INVALID_DATE:enrollment_date=2021-13-13" in r.reasons for r in rejects)
    assert any(m.member_name == "Mike" and m.dob is None for m in staged)


def test_pipeline_sample_and_legacy(tmp_path):
    report = run(SAMPLE, LEGACY, tmp_path, AS_OF)
    assert all(report.invariants.values())
    assert report.rejected == 1
    assert report.table_counts["TABLE_INDIA"] == 4

    with (tmp_path / "TABLE_INDIA.csv").open() as fh:
        india = {r["member_id"]: r for r in csv.DictReader(fh)}
    assert india["223458"]["member_name"] == "Ravi"
    assert india["223458"]["stale_member"] == "True"

    checks = {c["name"]: c for c in report.batch_checks}
    assert "MIKE" in checks["SPEC_KEY_MEMBER_NAME_NOT_UNIQUE"]["details"]


def test_day2_ravi_moves_and_redemption_updates(tmp_path):
    report = run([SAMPLE, SAMPLE.parent / "day2"], LEGACY, tmp_path, date(2024, 1, 16))
    assert [(m["member_id"], m["from_country"], m["to_country"]) for m in report.moves] == [
        ("223458", "IND", "CAN")
    ]
    with (tmp_path / "TABLE_INDIA.csv").open() as fh:
        assert "223458" not in {r["member_id"] for r in csv.DictReader(fh)}
    with (tmp_path / "REDEMPTION_ENRICHED.csv").open() as fh:
        status = {r["txn_id"]: r["status"] for r in csv.DictReader(fh)}
    assert status["RX10092"] == "COMPLETED"
