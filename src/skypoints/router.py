"""STAGING -> TABLE_<COUNTRY> with "latest record wins".

Mirrors CORE.SP_SPLIT_MEMBERS_BY_COUNTRY. ``CountryTables`` holds the
target tables plus the member->country cross-reference; ``apply`` is
idempotent and order-independent: feeding the same files again, or an
older file after a newer one, never regresses a member.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .staging import StagedMember


@dataclass
class XrefEntry:
    country_code: str
    target_table: str
    order_key: tuple[datetime, int]


@dataclass
class Move:
    member_id: str
    from_country: str
    to_country: str
    moved_on_file_ts: datetime


@dataclass
class ApplyResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    moved: int = 0
    ignored_older: int = 0
    superseded_in_batch: int = 0


def latest_per_member(batch: Iterable[StagedMember]) -> tuple[dict[str, StagedMember], int]:
    """Collapse a batch to one record per member (= SQL QUALIFY ROW_NUMBER() = 1)."""
    latest: dict[str, StagedMember] = {}
    total = 0
    for rec in batch:
        total += 1
        cur = latest.get(rec.member_id)
        if cur is None or rec.order_key > cur.order_key:
            latest[rec.member_id] = rec
    return latest, total - len(latest)


@dataclass
class CountryTables:
    tables: dict[str, dict[str, StagedMember]] = field(default_factory=dict)
    xref: dict[str, XrefEntry] = field(default_factory=dict)
    moves: list[Move] = field(default_factory=list)

    def apply(self, batch: Iterable[StagedMember]) -> ApplyResult:
        result = ApplyResult()
        latest, result.superseded_in_batch = latest_per_member(batch)

        for member_id, rec in latest.items():
            prev = self.xref.get(member_id)

            # Late or replayed data must never overwrite newer data.
            if prev is not None and rec.order_key <= prev.order_key:
                result.ignored_older += 1
                continue

            if prev is not None and prev.target_table != rec.target_table:
                self.tables.get(prev.target_table, {}).pop(member_id, None)
                self.moves.append(Move(member_id, prev.country_code, rec.country_code, rec.src_file_ts))
                result.moved += 1

            table = self.tables.setdefault(rec.target_table, {})
            existing = table.get(member_id)
            if existing is None:
                result.inserted += 1
            elif existing.record_hash != rec.record_hash:
                result.updated += 1
            else:
                result.unchanged += 1
            table[member_id] = rec
            self.xref[member_id] = XrefEntry(rec.country_code, rec.target_table, rec.order_key)

        return result

    def where_is(self, member_id: str) -> list[str]:
        """All tables containing the member (should always be 0 or 1)."""
        return [t for t, rows in self.tables.items() if member_id in rows]
