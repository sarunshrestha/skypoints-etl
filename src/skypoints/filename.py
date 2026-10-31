"""File-name specification.

    SKYPOINTS_MBR_<YYYYMMDD>_<HHMMSSTT>.dat    member profile flat file
    SKYPOINTS_RDM_<YYYYMMDD>_<HHMMSSTT>.json   partner redemption feed

TT = hundredths of a second. The timestamp in the name is the moment the
source produced the extract, and is the primary ordering key for
"latest record wins" (it is stable across re-deliveries, unlike arrival time).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_PATTERN = re.compile(
    r"^SKYPOINTS_(?P<kind>MBR|RDM)_(?P<date>\d{8})_(?P<time>\d{6})(?P<hund>\d{2})\.(?P<ext>dat|json)$"
)
_EXT_FOR_KIND = {"MBR": "dat", "RDM": "json"}


@dataclass(frozen=True)
class FeedFile:
    path: Path
    kind: str            # "MBR" | "RDM"
    produced_at: datetime


class FileNameError(ValueError):
    pass


def parse_file_name(path: str | Path) -> FeedFile:
    path = Path(path)
    m = _PATTERN.match(path.name)
    if not m:
        raise FileNameError(f"{path.name!r} does not match SKYPOINTS_<MBR|RDM>_YYYYMMDD_HHMMSSTT.<dat|json>")
    if _EXT_FOR_KIND[m["kind"]] != m["ext"]:
        raise FileNameError(f"{path.name!r}: kind {m['kind']} must use .{_EXT_FOR_KIND[m['kind']]}")
    try:
        ts = datetime.strptime(m["date"] + m["time"], "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise FileNameError(f"{path.name!r}: invalid timestamp ({exc})") from exc
    ts = ts.replace(microsecond=int(m["hund"]) * 10_000)
    return FeedFile(path=path, kind=m["kind"], produced_at=ts)
