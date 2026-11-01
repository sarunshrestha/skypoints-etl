from datetime import date

from skypoints.redemptions import dedupe_latest, flatten, load_documents

from conftest import SAMPLE


def test_sample_redemptions():
    rows, rejects = flatten(load_documents(SAMPLE / "SKYPOINTS_RDM_20240115_02300000.json"), "f.json")
    assert rejects == []
    assert len(rows) == 2
    assert rows[0].txn_id == "RX10091"
    assert rows[1].status == "PENDING"


def test_latest_status_wins():
    docs = [
        {"member_id": "1", "feed_date": "20240115", "redemptions": [
            {"txn_id": "T", "txn_date": "20240110", "miles_redeemed": 5, "status": "PENDING"}]},
        {"member_id": "1", "feed_date": "20240116", "redemptions": [
            {"txn_id": "T", "txn_date": "20240110", "miles_redeemed": 5, "status": "COMPLETED"}]},
    ]
    rows, _ = flatten(docs, "f")
    assert dedupe_latest(rows)["T"].status == "COMPLETED"


def test_bad_miles_rejected():
    doc = {"member_id": "1", "feed_date": "20240115", "redemptions": [
        {"txn_id": "OK", "txn_date": "20240110", "miles_redeemed": 10, "status": "COMPLETED"},
        {"txn_id": "NEG", "txn_date": "20240110", "miles_redeemed": -5},
    ]}
    rows, rejects = flatten([doc], "f")
    assert [r.txn_id for r in rows] == ["OK"]
    assert any("NON_POSITIVE_MILES" in j.reasons for j in rejects)
