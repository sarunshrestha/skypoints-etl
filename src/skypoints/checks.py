"""Batch-level data-quality checks (mirror of section C in sql/07_validations.sql).

Row-level rules live in ``staging.py``. These look *across* rows of a
batch, catching problems no single row reveals.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .staging import StagedMember


@dataclass
class CheckResult:
    name: str
    severity: str            # BLOCKING | WARNING
    failed: int
    details: object = None

    @property
    def passed(self) -> bool:
        return self.failed == 0


def duplicate_member_id_in_file(rows: list[StagedMember]) -> CheckResult:
    seen: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        seen[(r.src_file_name, r.member_id)] += 1
    dups = {f"{f}:{m}": n for (f, m), n in seen.items() if n > 1}
    return CheckResult("DUPLICATE_MEMBER_ID_IN_FILE", "WARNING", len(dups), dups)


def member_id_with_multiple_names(rows: list[StagedMember]) -> CheckResult:
    names: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        names[r.member_id].add(r.member_name.upper())
    bad = {m: sorted(n) for m, n in names.items() if len(n) > 1}
    return CheckResult("MEMBER_ID_WITH_MULTIPLE_NAMES", "WARNING", len(bad), bad)


def spec_key_member_name_not_unique(rows: list[StagedMember]) -> CheckResult:
    """The spec marks Member_Name as the key column; show why that fails."""
    ids: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        ids[r.member_name.upper()].add(r.member_id)
    bad = {n: sorted(i) for n, i in ids.items() if len(i) > 1}
    return CheckResult("SPEC_KEY_MEMBER_NAME_NOT_UNIQUE", "WARNING", len(bad), bad)


def suspicious_constant_columns(rows: list[StagedMember], min_rows: int = 3) -> CheckResult:
    """Every row identical in a column usually means an injected default."""
    by_file: dict[str, list[StagedMember]] = defaultdict(list)
    for r in rows:
        by_file[r.src_file_name].append(r)
    flagged = {}
    for f, rs in by_file.items():
        if len(rs) < min_rows:
            continue
        for col in ("dob", "enrollment_date", "last_flight_date", "agent_name"):
            vals = {getattr(r, col) for r in rs}
            if len(vals) == 1 and None not in vals:
                flagged.setdefault(f, []).append(f"{col}={next(iter(vals))}")
    return CheckResult("SUSPICIOUS_CONSTANT_COLUMN", "WARNING",
                       sum(len(v) for v in flagged.values()), flagged)


def run_batch_checks(rows: Iterable[StagedMember]) -> list[CheckResult]:
    rows = list(rows)
    return [
        duplicate_member_id_in_file(rows),
        member_id_with_multiple_names(rows),
        spec_key_member_name_not_unique(rows),
        suspicious_constant_columns(rows),
    ]
