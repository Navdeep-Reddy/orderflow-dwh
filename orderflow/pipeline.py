"""Orchestration: for each source -> checksum/skip -> extract+validate -> DQ gate -> quarantine
rejects -> ONE atomic load transaction -> run bookkeeping -> reconciliation -> run report.

Restart / retry semantics
  * Each source is loaded in a single transaction together with its `ingested_file` registration.
    A crash therefore leaves either everything or nothing of that source.
  * Retry = run the same command again. Sources already loaded are SKIPPED (same sha256);
    failed ones are retried. A run never needs manual cleanup.
  * Backfill = point the pipeline at the historical file. Late/out-of-order events are merged by
    event timestamp; dim_customer is rebuilt for the affected customers only.
"""
from __future__ import annotations

import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import load_customers, load_orders, reconcile
from .config import Config
from .db import connect, init_schema, transaction
from .ids import file_sha256, make_run_id
from .runlog import (SourceStats, finish_run, save_rejects, save_source, start_run)
from .sources import SourceSchemaError
from .timeutil import add_hours, parse_ts, utc_now_iso

log = logging.getLogger("orderflow")

# customers must load before orders: orders are checked against dim_customer
SOURCES = [(load_customers, "customers_file"), (load_orders, "orders_file")]


def _run_source(conn, cfg: Config, run_id: str, module, path: Path, latest_allowed_ts: str) -> SourceStats:
    stats = SourceStats(source_name=module.NAME, source_file=str(path))
    t0 = time.perf_counter()
    try:
        stats.file_sha256 = file_sha256(path)                       # FileNotFoundError -> FAILED
        already = conn.execute("SELECT run_id FROM ingested_file WHERE source_name=? AND file_sha256=?",
                               (module.NAME, stats.file_sha256)).fetchone()
        if already and not cfg.force:
            stats.status = "SKIPPED_ALREADY_LOADED"
            stats.warnings.append(f"identical content already loaded by {already['run_id']}")
            log.info("source skipped run_id=%s source=%s reason=already_loaded", run_id, module.NAME)
            return stats

        t1 = time.perf_counter()
        ex = module.extract(conn, path, cfg, latest_allowed_ts)     # SourceSchemaError on contract break
        stats.validate_seconds = time.perf_counter() - t1
        stats.rows_read, stats.rows_accepted = ex.rows_read, len(ex.accepted)
        stats.rows_rejected = len(ex.rejects)
        stats.warnings += ex.warnings
        for _, code, _, _ in ex.rejects:
            stats.reject_reasons[code] = stats.reject_reasons.get(code, 0) + 1

        with transaction(conn):                                      # quarantine is always kept
            save_rejects(conn, run_id, module.NAME, stats.source_file, ex.rejects)

        ratio = stats.rows_rejected / stats.rows_read if stats.rows_read else 0.0
        if ratio > cfg.max_reject_ratio:
            raise ValueError(f"REJECT_RATIO_EXCEEDED: {ratio:.1%} of rows rejected "
                             f"(limit {cfg.max_reject_ratio:.1%}); nothing loaded")

        t2 = time.perf_counter()
        with transaction(conn):
            module.load(conn, ex, run_id, cfg, stats)
            conn.execute("INSERT OR REPLACE INTO ingested_file VALUES (?,?,?,?,?,?)",
                         (module.NAME, stats.file_sha256, stats.source_file, run_id,
                          utc_now_iso(), stats.rows_read))
            stats.status = "SUCCESS"
            stats.load_seconds = time.perf_counter() - t2
            stats.ended_at = utc_now_iso()
            stats.duration_seconds = time.perf_counter() - t0
            save_source(conn, run_id, stats)                          # committed atomically with the load
        return stats
    except SourceSchemaError as exc:
        stats.status, stats.failure_reason = "FAILED", f"SCHEMA_ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded, never swallowed silently
        stats.status, stats.failure_reason = "FAILED", f"{type(exc).__name__}: {exc}"
    # failure path: the load transaction (if any) has been rolled back
    stats.rows_inserted_new = stats.rows_already_present = 0
    stats.target_rows_found = stats.target_amount_minor = None
    stats.ended_at, stats.duration_seconds = utc_now_iso(), time.perf_counter() - t0
    log.error("source failed run_id=%s source=%s reason=%s", run_id, module.NAME, stats.failure_reason)
    return stats


def run_pipeline(cfg: Config) -> dict:
    t_start = time.perf_counter()
    as_of = parse_ts(cfg.as_of) if cfg.as_of else utc_now_iso()
    latest_allowed = add_hours(as_of, cfg.max_future_skew_hours)
    inputs = [str(getattr(cfg, attr)) for _, attr in SOURCES if getattr(cfg, attr)]
    run_id = cfg.run_id or make_run_id(datetime.now(timezone.utc), "|".join(inputs))

    conn = connect(cfg.db_path)
    init_schema(conn)
    start_run(conn, run_id, as_of)
    log.info("run started run_id=%s as_of=%s inputs=%s", run_id, as_of, inputs)

    results: list[SourceStats] = []
    run_failure, checks = None, []
    try:
        for module, attr in SOURCES:
            path = getattr(cfg, attr)
            if path is None:
                continue
            s = _run_source(conn, cfg, run_id, module, Path(path), latest_allowed)
            if s.status == "FAILED":
                save_source(conn, run_id, s)
            elif s.status == "SKIPPED_ALREADY_LOADED":
                s.ended_at = utc_now_iso()
                save_source(conn, run_id, s)
            results.append(s)
            log.info("source done run_id=%s source=%s status=%s read=%d rejected=%d new=%d "
                     "already_present=%d secs=%.2f", run_id, s.source_name, s.status, s.rows_read,
                     s.rows_rejected, s.rows_inserted_new, s.rows_already_present, s.duration_seconds)

        if not results:
            run_failure = "no input files configured"
        statuses = {s.status for s in results}
        if not results or statuses == {"FAILED"}:
            status = "FAILED"
        elif "FAILED" in statuses:
            status = "PARTIAL_FAILURE"
        else:
            status = "SUCCESS"
        if status != "SUCCESS" and not run_failure:
            run_failure = "; ".join(f"{s.source_name}: {s.failure_reason}" for s in results
                                    if s.status == "FAILED")

        checks = reconcile.run_checks(conn, run_id, results)
        recon_ok = all(c["passed"] for c in checks)
    except Exception as exc:  # noqa: BLE001
        status, run_failure, recon_ok = "FAILED", f"{type(exc).__name__}: {exc}", None
        log.exception("run crashed run_id=%s", run_id)

    duration = time.perf_counter() - t_start
    finish_run(conn, run_id, status, run_failure, duration, recon_ok)
    summary = {"run_id": run_id, "status": status, "failure_reason": run_failure, "as_of": as_of,
               "duration_seconds": round(duration, 3), "reconciliation_passed": recon_ok,
               "sources": [s.as_dict() for s in results], "reconciliation": checks}
    _write_artifacts(conn, cfg, run_id, summary)
    log.info("run finished run_id=%s status=%s reconciliation_passed=%s secs=%.2f",
             run_id, status, recon_ok, duration)
    conn.close()
    return summary


def _write_artifacts(conn, cfg: Config, run_id: str, summary: dict) -> None:
    out = Path(cfg.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"run_report_{run_id}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    rows = conn.execute("SELECT source_name, source_file, line_number, reason_code, reason_detail, raw_record "
                        "FROM reject_record WHERE run_id=? ORDER BY source_name, line_number", (run_id,)).fetchall()
    if rows:
        with open(out / f"rejects_{run_id}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["source_name", "source_file", "line_number", "reason_code", "reason_detail", "raw_record"])
            w.writerows([tuple(r) for r in rows])
