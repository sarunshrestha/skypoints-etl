"""Streaming parser for the pipe-delimited member flat file.

Layout:  |H|Member_Name|Member_Id|...      header (exactly one)
         |D|Elena|223457|...              detail
         |T|<count>                       trailer (optional; recommended)

Parsing is header-driven: values are mapped by header name, not position,
so an added column (e.g. Post_Code, which the spec lists but the sample
header omits) does not shift every field after it. The file is read line by
line, so memory use is constant regardless of file size.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, TextIO

# Header name -> canonical field name
HEADER_TO_FIELD = {
    "Member_Name": "member_name",
    "Member_Id": "member_id",
    "Enrollment_Date": "enrollment_date",
    "Last_Flight_Date": "last_flight_date",
    "Tier_Code": "tier_code",
    "Agent_Name": "agent_name",
    "State": "state",
    "Country": "country",
    "Post_Code": "post_code",
    "DOB": "dob",
    "Is_Active": "is_active",
}
EXPECTED_HEADER = [
    "Member_Name", "Member_Id", "Enrollment_Date", "Last_Flight_Date", "Tier_Code",
    "Agent_Name", "State", "Country", "DOB", "Is_Active",
]


@dataclass
class RawRecord:
    """One detail line, all values still strings (mirrors RAW_MEMBER_PROFILE)."""
    values: dict[str, str | None]
    src_file_name: str
    src_row_number: int
    raw_line: str
    issues: list[str] = field(default_factory=list)   # structural problems found while parsing


@dataclass
class FileSummary:
    header: list[str] | None = None
    header_issues: list[str] = field(default_factory=list)
    detail_rows: int = 0
    trailer_count: int | None = None
    unknown_record_types: int = 0


class FlatFileError(ValueError):
    pass


def _split(line: str) -> list[str]:
    parts = line.rstrip("\r\n").split("|")
    if parts and parts[0] == "":          # leading delimiter
        parts = parts[1:]
    return [p.strip() for p in parts]


def iter_member_file(
    stream: TextIO, src_file_name: str, summary: FileSummary | None = None
) -> Iterator[RawRecord]:
    summary = summary if summary is not None else FileSummary()
    columns: list[str] | None = None

    for line_no, line in enumerate(stream, start=1):
        if not line.strip():
            continue
        parts = _split(line)
        rtype, payload = parts[0].upper(), parts[1:]

        if rtype == "H":
            if columns is not None:
                raise FlatFileError(f"{src_file_name}:{line_no}: second header record")
            columns = payload
            summary.header = payload
            unknown = [c for c in payload if c not in HEADER_TO_FIELD]
            missing = [c for c in EXPECTED_HEADER if c not in payload]
            if unknown:
                summary.header_issues.append(f"UNKNOWN_HEADER_COLUMNS:{','.join(unknown)}")
            if missing:
                summary.header_issues.append(f"MISSING_HEADER_COLUMNS:{','.join(missing)}")
            continue

        if rtype == "T":
            try:
                summary.trailer_count = int(payload[0])
            except (IndexError, ValueError):
                summary.header_issues.append("INVALID_TRAILER")
            continue

        if rtype != "D":
            summary.unknown_record_types += 1
            continue

        if columns is None:
            raise FlatFileError(f"{src_file_name}:{line_no}: detail record before header")

        summary.detail_rows += 1
        issues = []
        if len(payload) != len(columns):
            issues.append(f"COLUMN_COUNT_MISMATCH:expected={len(columns)},got={len(payload)}")
        values: dict[str, str | None] = {f: None for f in HEADER_TO_FIELD.values()}
        for col, val in zip(columns, payload):
            key = HEADER_TO_FIELD.get(col)
            if key:
                values[key] = val or None
        yield RawRecord(values, src_file_name, line_no, line.rstrip("\r\n"), issues)


def read_member_file(path: str | Path) -> tuple[list[RawRecord], FileSummary]:
    path = Path(path)
    summary = FileSummary()
    with path.open(encoding="utf-8") as fh:
        records = list(iter_member_file(fh, path.name, summary))
    if summary.header is None:
        raise FlatFileError(f"{path.name}: no header record")
    return records, summary
