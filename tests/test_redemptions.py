import json
from datetime import date

from skypoints.redemptions import dedupe_latest, flatten, load_documents

from conftest import SAMPLE


def test_sample_feed_flattens_to_one_row_per_txn():
    docs = load_documents(SAMPLE / "SKYPOINTS_RDM_20240115_02300000.json")
    rows, rejects = flatten(docs, "f.json")
    assert rejects == []
    assert [(r.txn_id, r.member_id, r.miles_redeemed, r.status) for r in rows] == [
        ("RX10091", "223457", 12000, "COMPLETED"),
        ("RX10092", "223457", 5000, "PENDING"),
    ]
    assert rows[0].feed_date == date(2024, 1, 15) and rows[0].txn_date == date(2024, 1, 10)


def test_accepts_array_and_json_lines(tmp_path):
    doc = {"member_id": "1", "feed_date": "20240101", "redemptions": [
        {"txn_id": "T1", "txn_date": "20240101", "partner": "P", "miles_redeemed": 1, "status": "COMPLETED"}]}
    a = tmp_path / "a.json"; a.write_text(json.dumps([doc, doc]))
    b = tmp_path / "b.json"; b.write_text(json.dumps(doc) + "\n" + json.dumps(doc) + "\n")
    assert len(load_documents(a)) == len(load_documents(b)) == 2


def test_bad_transactions_rejected_individually_good_ones_kept():
    doc = {"member_id": "1", "feed_date": "20240115", "redemptions": [
        {"txn_id": "OK", "txn_date": "20240110", "partner": "P", "miles_redeemed": 10, "status": "COMPLETED"},
        {"txn_id": "", "txn_date": "20240110", "miles_redeemed": 10},
        {"txn_id": "NEG", "txn_date": "20240110", "miles_redeemed": -5},
        {"txn_id": "TXT", "txn_date": "2024-01-10", "miles_redeemed": "lots"},
    ]}
    rows, rejects = flatten([doc], "f")
    assert [r.txn_id for r in rows] == ["OK"]
    reasons = [r for j in rejects for r in j.reasons]
    assert "MANDATORY_MISSING:txn_id" in reasons
    assert "NON_POSITIVE_MILES" in reasons
    assert "INVALID_NUMBER:miles_redeemed=lots" in reasons
    assert "INVALID_DATE:txn_date=2024-01-10" in reasons


def test_document_without_member_or_transactions_rejected():
    rows, rejects = flatten([{"feed_date": "20240101", "redemptions": []}], "f")
    assert rows == []
    assert set(rejects[0].reasons) == {"MANDATORY_MISSING:member_id", "EMPTY_REDEMPTIONS_ARRAY"}


def test_status_progression_latest_feed_wins():
    def doc(feed, status):
        return {"member_id": "1", "feed_date": feed, "redemptions": [
            {"txn_id": "T", "txn_date": "20240110", "miles_redeemed": 5, "status": status}]}
    rows, _ = flatten([doc("20240116", "COMPLETED"), doc("20240115", "PENDING")], "f")
    assert dedupe_latest(rows)["T"].status == "COMPLETED"


def test_unknown_status_and_future_txn_are_warnings():
    rows, _ = flatten([{"member_id": "1", "feed_date": "20240101", "redemptions": [
        {"txn_id": "T", "txn_date": "20240105", "miles_redeemed": 5, "status": "weird"}]}], "f")
    assert set(rows[0].dq_warnings) == {"UNKNOWN_STATUS:WEIRD", "TXN_AFTER_FEED_DATE"}
