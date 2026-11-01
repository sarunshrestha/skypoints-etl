"""Apply staged members into per-country tables (latest file/line wins)."""

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


@dataclass
class CountryTables:
    tables: dict[str, dict[str, StagedMember]] = field(default_factory=dict)
    xref: dict[str, XrefEntry] = field(default_factory=dict)
    moves: list[Move] = field(default_factory=list)

    def apply(self, batch: Iterable[StagedMember]) -> ApplyResult:
        result = ApplyResult()
        latest = {}
        total = 0
        for rec in batch:
            total += 1
            cur = latest.get(rec.member_id)
            if cur is None or rec.order_key > cur.order_key:
                latest[rec.member_id] = rec
        result.superseded_in_batch = total - len(latest)

        for member_id, rec in latest.items():
            prev = self.xref.get(member_id)
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
        return [t for t, rows in self.tables.items() if member_id in rows]
