"""ISO-8601 handling. All stored timestamps are UTC, 'YYYY-MM-DDTHH:MM:SSZ' (sortable as text)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$"
)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime(FMT)


def parse_ts(value: str) -> str:
    """Validate and normalise to UTC. A timezone designator is REQUIRED: a naive timestamp
    would force us to guess its zone, and silent guesses corrupt data."""
    s = value.strip()
    if not _TS_RE.match(s):
        raise ValueError(f"not an ISO-8601 timestamp with timezone: {value!r}")
    dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)  # may raise ValueError
    return dt.astimezone(timezone.utc).strftime(FMT)


def add_hours(iso: str, hours: int) -> str:
    dt = datetime.strptime(iso, FMT).replace(tzinfo=timezone.utc)
    return (dt + timedelta(hours=hours)).strftime(FMT)
