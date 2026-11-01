"""End-to-end local run: feeds in a folder -> per-country CSVs + DQ report.

    python -m skypoints.pipeline --input data/sample --legacy data/legacy \
        --out out --as-of 2024-01-15
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import legacy
from .checks import run_batch_checks
from .filename import FileNameError, parse_file_name
from .parser import read_member_file
from .redemptions import Redemption, RedemptionReject, dedupe_latest, flatten, load_documents
from .router import CountryTables
from .staging import Reject, StagedMember, stage_records

MEMBER_COLUMNS = [
    "member_id", "member_name", "enrollment_date", "last_flight_date", "tier_code", "agent_name",
    "state", "country_code", "post_code", "dob", "is_active", "age", "stale_member",
    "days_since_flight", "as_of_date", "dq_warnings", "src_file_name", "src_file_ts",
]


@dataclass
class RunReport:
    as_of: str
    files: list[dict] = field(default_factory=list)
    staged: int = 0
    rejected: int = 0
    warnings: dict[str, int] = field(default_factory=dict)
    reject_reasons: dict[str, int] = field(default_factory=dict)
    apply: list[dict] = field(default_factory=list)
    table_counts: dict[str, int] = field(default_factory=dict)
    moves: list[dict] = field(default_factory=list)
    redemptions: dict = field(default_factory=dict)
    invariants: dict[str, bool] = field(default_factory=dict)
    batch_checks: list[dict] = field(default_factory=list)


def _reason_key(reason: str) -> str:
    return reason.split(":", 1)[0]


def run(input_dir: Path | list[Path] | None, legacy_dir: Path | None, out_dir: Path, as_of: date,
        legacy_ts: datetime | None = None) -> RunReport:
    report = RunReport(as_of=as_of.isoformat())
    tables = CountryTables()
    all_staged: list[StagedMember] = []
    all_rejects: list[Reject] = []
    redemption_rows: list[Redemption] = []
    redemption_rejects: list[RedemptionReject] = []

    # ---- legacy extracts first: they are older than any unified feed -------
    if legacy_dir:
        ts = legacy_ts or datetime(2022, 12, 31)
        for name, adapter in legacy.ADAPTERS.items():
            p = legacy_dir / name
            if not p.exists():
                continue
            staged, rejects = stage_records(adapter(p), ts, as_of)
            res = tables.apply(staged)
            all_staged += staged
            all_rejects += rejects
            report.files.append({"file": name, "kind": "LEGACY", "rows": len(staged) + len(rejects),
                                 "staged": len(staged), "rejected": len(rejects)})
            report.apply.append({"file": name, **asdict(res)})

    # ---- unified feeds, oldest first (order does not change the outcome,
    #      the router is order-independent, but it keeps logs readable) ----
    feeds = []
    input_dirs = [input_dir] if isinstance(input_dir, Path) else list(input_dir or [])
    for d in input_dirs:
        for p in sorted(d.iterdir()):
            if p.is_file():
                try:
                    feeds.append(parse_file_name(p))
                except FileNameError as exc:
                    report.files.append({"file": p.name, "kind": "UNRECOGNISED", "error": str(exc)})
    for feed in sorted(feeds, key=lambda f: f.produced_at):
        if feed.kind == "MBR":
            raws, summary = read_member_file(feed.path)
            staged, rejects = stage_records(raws, feed.produced_at, as_of)
            res = tables.apply(staged)
            all_staged += staged
            all_rejects += rejects
            entry = {"file": feed.path.name, "kind": "MBR", "rows": summary.detail_rows,
                     "staged": len(staged), "rejected": len(rejects),
                     "header_issues": summary.header_issues,
                     "trailer_count": summary.trailer_count}
            if summary.trailer_count is not None and summary.trailer_count != summary.detail_rows:
                entry["header_issues"].append(
                    f"TRAILER_COUNT_MISMATCH:trailer={summary.trailer_count},detail={summary.detail_rows}")
            report.files.append(entry)
            report.apply.append({"file": feed.path.name, **asdict(res)})
        else:
            rows, rejects = flatten(load_documents(feed.path), feed.path.name)
            redemption_rows += rows
            redemption_rejects += rejects
            report.files.append({"file": feed.path.name, "kind": "RDM",
                                 "transactions": len(rows), "rejected": len(rejects)})

    # ---- report -----------------------------------------------------------
    report.staged, report.rejected = len(all_staged), len(all_rejects)
    report.warnings = dict(Counter(_reason_key(w) for m in all_staged for w in m.dq_warnings))
    report.reject_reasons = dict(Counter(_reason_key(r) for j in all_rejects for r in j.reasons))
    report.table_counts = {t: len(rows) for t, rows in sorted(tables.tables.items())}
    report.moves = [{**asdict(m), "moved_on_file_ts": m.moved_on_file_ts.isoformat()} for m in tables.moves]

    facts = dedupe_latest(redemption_rows)
    members = {mid: rec for rows in tables.tables.values() for mid, rec in rows.items()}
    orphans = [t for t in facts.values() if t.member_id not in members]
    report.redemptions = {"transactions": len(facts), "rejected": len(redemption_rejects),
                          "orphans": len(orphans)}

    # invariants (= table-level checks C4/C5/C6 in 07_validations.sql)
    ids = [mid for rows in tables.tables.values() for mid in rows]
    report.invariants = {
        "member_in_exactly_one_table": len(ids) == len(set(ids)),
        "rows_match_table_country": all(
            r.target_table == t for t, rows in tables.tables.items() for r in rows.values()),
        "xref_in_sync": set(tables.xref) == set(ids) and all(
            tables.xref[m].target_table == members[m].target_table for m in ids),
    }

    report.batch_checks = [asdict(c) for c in run_batch_checks(all_staged) if not c.passed]

    _write_outputs(out_dir, tables, all_rejects, facts, members, redemption_rejects, report)
    return report


def _cell(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, list):
        return ";".join(v)
    return "" if v is None else v


def _write_outputs(out_dir, tables, rejects, facts, members, redemption_rejects, report):
    out_dir.mkdir(parents=True, exist_ok=True)
    for t, rows in tables.tables.items():
        with (out_dir / f"{t}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(MEMBER_COLUMNS)
            for r in sorted(rows.values(), key=lambda r: r.member_id):
                w.writerow([_cell(getattr(r, c)) for c in MEMBER_COLUMNS])

    with (out_dir / "MEMBER_REJECTS.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["src_file_name", "src_row_number", "member_id", "reject_reasons", "raw_record"])
        for j in rejects:
            w.writerow([j.src_file_name, j.src_row_number, j.member_id or "", ";".join(j.reasons),
                        json.dumps(j.raw_record, default=str)])

    with (out_dir / "REDEMPTION_ENRICHED.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["txn_id", "txn_date", "partner", "miles_redeemed", "status", "feed_date", "member_id",
                    "member_name", "tier_code", "country_code", "stale_member", "is_orphan_redemption"])
        for t in sorted(facts.values(), key=lambda t: t.txn_id):
            m = members.get(t.member_id)
            w.writerow([t.txn_id, _cell(t.txn_date), t.partner or "", t.miles_redeemed, t.status or "",
                        _cell(t.feed_date), t.member_id,
                        m.member_name if m else "", (m.tier_code or "") if m else "",
                        m.country_code if m else "", _cell(m.stale_member) if m else "", m is None])

    with (out_dir / "REDEMPTION_REJECTS.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["src_file_name", "member_id", "reasons", "raw"])
        for j in redemption_rejects:
            w.writerow([j.src_file_name, j.member_id or "", ";".join(j.reasons), json.dumps(j.raw)])

    (out_dir / "dq_report.json").write_text(json.dumps(asdict(report), indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, nargs="*", default=[],
                    help="folder(s) with SKYPOINTS_MBR_*/SKYPOINTS_RDM_* files")
    ap.add_argument("--legacy", type=Path, help="folder with INDin.csv / USAin.csv / AUS.xlsx")
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today(),
                    help="date AGE / STALE_MEMBER are computed for (default: today)")
    args = ap.parse_args(argv)
    report = run(args.input, args.legacy, args.out, args.as_of)
    print(json.dumps(asdict(report), indent=2, default=str))
    return 0 if all(report.invariants.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
