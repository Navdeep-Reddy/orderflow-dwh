"""Run bookkeeping: per-source stats and persistence of run / source / reject records."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from .timeutil import utc_now_iso

log = logging.getLogger("orderflow")


@dataclass
class SourceStats:
    source_name: str
    source_file: str
    file_sha256: Optional[str] = None
    status: str = "FAILED"
    rows_read: int = 0
    rows_rejected: int = 0
    rows_accepted: int = 0
    rows_inserted_new: int = 0
    rows_already_present: int = 0
    expected_amount_minor: Optional[int] = None
    target_amount_minor: Optional[int] = None
    target_rows_found: Optional[int] = None
    warnings: list = field(default_factory=list)
    reject_reasons: dict = field(default_factory=dict)
    failure_reason: Optional[str] = None
    started_at: str = field(default_factory=utc_now_iso)
    ended_at: Optional[str] = None
    duration_seconds: float = 0.0
    validate_seconds: float = 0.0
    load_seconds: float = 0.0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def start_run(conn, run_id: str, as_of: str) -> None:
    conn.execute("INSERT INTO pipeline_run (run_id, started_at, status, as_of) VALUES (?,?,?,?)",
                 (run_id, utc_now_iso(), "RUNNING", as_of))


def finish_run(conn, run_id: str, status: str, failure_reason: Optional[str],
               duration: float, recon_passed: Optional[bool]) -> None:
    conn.execute(
        "UPDATE pipeline_run SET ended_at=?, status=?, failure_reason=?, duration_seconds=?, "
        "reconciliation_passed=? WHERE run_id=?",
        (utc_now_iso(), status, failure_reason, round(duration, 3),
         None if recon_passed is None else int(recon_passed), run_id))


def save_source(conn, run_id: str, s: SourceStats) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO run_source
           (run_id, source_name, source_file, file_sha256, status, rows_read, rows_rejected,
            rows_accepted, rows_inserted_new, rows_already_present, expected_amount_minor,
            target_amount_minor, target_rows_found, warnings, failure_reason, started_at,
            ended_at, duration_seconds)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, s.source_name, s.source_file, s.file_sha256, s.status, s.rows_read,
         s.rows_rejected, s.rows_accepted, s.rows_inserted_new, s.rows_already_present,
         s.expected_amount_minor, s.target_amount_minor, s.target_rows_found,
         json.dumps(s.warnings), s.failure_reason, s.started_at, s.ended_at,
         round(s.duration_seconds, 3)))


def save_rejects(conn, run_id: str, source_name: str, source_file: str, rejects: list) -> None:
    """rejects: list of (line_number, reason_code, reason_detail, raw_record)"""
    now = utc_now_iso()
    conn.executemany(
        """INSERT INTO reject_record (run_id, source_name, source_file, line_number, reason_code,
                                      reason_detail, raw_record, rejected_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(run_id, source_name, source_file, ln, code, detail, raw, now)
         for ln, code, detail, raw in rejects])


@dataclass
class Extracted:
    """Result of reading + validating one source file (nothing written to the DB yet)."""
    rows_read: int = 0
    accepted: list = field(default_factory=list)   # clean dicts, each carries "_line"
    rejects: list = field(default_factory=list)    # (line_number, code, detail, raw_record)
    warnings: list = field(default_factory=list)
