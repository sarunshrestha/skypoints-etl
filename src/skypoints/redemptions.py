"""Flatten partner redemption JSON."""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .staging import parse_yyyymmdd

KNOWN_STATUSES = {"COMPLETED", "PENDING", "CANCELLED", "REVERSED"}


@dataclass
class Redemption:
    txn_id: str
    member_id: str
    feed_date: date | None
    txn_date: date | None
    partner: str | None
    miles_redeemed: int
    status: str | None
    src_file_name: str
    dq_warnings: list[str] = field(default_factory=list)


@dataclass
class RedemptionReject:
    src_file_name: str
    member_id: str | None
    reasons: list[str]
    raw: Any


def load_documents(path: str | Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        doc = json.loads(text)
        return doc if isinstance(doc, list) else [doc]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _date(value, field_name, reasons):
    try:
        return parse_yyyymmdd(None if value is None else str(value))
    except ValueError:
        reasons.append(f"INVALID_DATE:{field_name}={value}")
        return None


def flatten(docs: Iterable[dict], src_file_name: str):
    rows, rejects = [], []
    for doc in docs:
        member_id = doc.get("member_id")
        member_id = str(member_id).strip() if member_id is not None else None
        doc_reasons = []
        feed_date = _date(doc.get("feed_date"), "feed_date", doc_reasons)
        txns = doc.get("redemptions")
        if not member_id:
            doc_reasons.append("MANDATORY_MISSING:member_id")
        if not isinstance(txns, list) or not txns:
            doc_reasons.append("EMPTY_REDEMPTIONS_ARRAY")
        if doc_reasons:
            rejects.append(RedemptionReject(src_file_name, member_id, doc_reasons, doc))
            continue

        for txn in txns:
            reasons, warnings = [], []
            txn_id = txn.get("txn_id")
            if not txn_id:
                reasons.append("MANDATORY_MISSING:txn_id")
            txn_date = _date(txn.get("txn_date"), "txn_date", reasons)
            miles = txn.get("miles_redeemed")
            try:
                miles = int(miles)
                if isinstance(txn.get("miles_redeemed"), float) and txn["miles_redeemed"] != miles:
                    raise ValueError
                if miles <= 0:
                    reasons.append("NON_POSITIVE_MILES")
            except (TypeError, ValueError):
                reasons.append(f"INVALID_NUMBER:miles_redeemed={txn.get('miles_redeemed')}")
            status = str(txn["status"]).upper() if txn.get("status") else None
            if status not in KNOWN_STATUSES:
                warnings.append(f"UNKNOWN_STATUS:{status}")
            if txn_date and feed_date and txn_date > feed_date:
                warnings.append("TXN_AFTER_FEED_DATE")
            if reasons:
                rejects.append(RedemptionReject(src_file_name, member_id, reasons, txn))
                continue
            rows.append(Redemption(
                txn_id, member_id, feed_date, txn_date, txn.get("partner"),
                miles, status, src_file_name, warnings,
            ))
    return rows, rejects


def dedupe_latest(rows: Iterable[Redemption]) -> dict[str, Redemption]:
    out = {}
    for r in rows:
        cur = out.get(r.txn_id)
        if cur is None or (r.feed_date or date.min) >= (cur.feed_date or date.min):
            out[r.txn_id] = r
    return out
