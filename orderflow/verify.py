"""Live verification of the project brief's requirements.

Every check actually RUNS something (the pipeline on temp copies of the sample data, the test
suite, the generator twice, ...) and reports PASS / FAIL with the evidence it observed.
Statuses: PASS | FAIL | WARN (honest to-do the candidate owns) | MANUAL (cannot be checked by code).
"""
from __future__ import annotations

import hashlib
import io
import logging
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable, Optional

from .config import Config
from .generate_data import generate
from .pipeline import run_pipeline
from .reconcile import check_db_invariants
from .db import connect

ROOT = Path(__file__).resolve().parent.parent
AS_OF = "2026-11-06T00:00:00Z"


class _Skip(Exception):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def _q(db: Path, sql: str, params=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _run(db: Path, out: Path, customers=None, orders=None, **kw) -> dict:
    return run_pipeline(Config(db_path=db, output_dir=out, customers_file=customers,
                               orders_file=orders, as_of=AS_OF, **kw))


def run_verification(root: Path = ROOT, run_tests: bool = True) -> list[dict]:
    log = logging.getLogger("orderflow")
    old = log.level
    log.setLevel(logging.CRITICAL)        # the edge-case checks fail sources on purpose; keep the console quiet
    try:
        return _run_verification(Path(root), run_tests)
    finally:
        log.setLevel(old)


def _run_verification(root: Path, run_tests: bool) -> list[dict]:
    sample = root / "data" / "sample"
    labels = json.loads((sample / "expected" / "expected_labels.json").read_text(encoding="utf-8"))
    results: list[dict] = []

    def check(cid: str, requirement: str, group: str, fn: Callable[[], tuple]):
        try:
            status, evidence = fn()
        except _Skip as exc:
            status, evidence = "FAIL", str(exc)
        except Exception as exc:  # noqa: BLE001
            status, evidence = "FAIL", f"{type(exc).__name__}: {exc}"
        results.append({"id": cid, "group": group, "requirement": requirement,
                        "status": status, "evidence": evidence})

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        main_db, out = tmp / "main.db", tmp / "out"
        state: dict = {}

        # ---------------------------------------------------------------- data & rules
        def c_docs():
            need = ["README.md", "docs/DATA_DICTIONARY.md", "docs/DESIGN.md", "requirements.txt",
                    "orderflow/sql/schema.sql"]
            missing = [n for n in need if not (root / n).exists()]
            return ("FAIL", f"missing: {missing}") if missing else ("PASS", f"{len(need)} files present")
        check("docs", "README, data dictionary, design note, setup files (Step 1, 10)", "Documentation", c_docs)

        def c_gen():
            a, b = tmp / "ga", tmp / "gb"
            generate(a), generate(b)
            same = all(_sha(a / f) == _sha(b / f) for f in ("customers.jsonl", "orders.csv", "orders_late.csv"))
            leaked = any("expected" in (sample / f).read_text(encoding="utf-8").lower()
                         for f in ("customers.jsonl", "orders.csv"))
            if not same:
                return "FAIL", "same seed gave different bytes"
            if leaked:
                return "FAIL", "answer key words found inside input files"
            return "PASS", "same seed -> byte-identical files; labels live in data/sample/expected/ only"
        check("data", "Reproducible sample + generator, fixed seed, labels separate from inputs (Step 7)", "Data", c_gen)

        def c_load():
            s = _run(main_db, out, sample / "customers.jsonl", sample / "orders.csv")
            state["first"] = s
            c, o = (next(x for x in s["sources"] if x["source_name"] == n) for n in ("customers", "orders"))
            if s["status"] != "SUCCESS" or not s["reconciliation_passed"]:
                return "FAIL", f"status={s['status']} reconciliation={s['reconciliation_passed']}"
            return "PASS", (f"customers {c['rows_read']} read / {c['rows_rejected']} rejected; "
                            f"orders {o['rows_read']} read / {o['rows_rejected']} rejected; reconciliation passed")
        check("load", "Ingest 2 sources, validate, transform, load dimensional model (Steps 2-3)", "Pipeline", c_load)

        def c_rejects():
            run_id = state["first"]["run_id"]
            bad = []
            for name in ("customers", "orders"):
                got = {tuple(r) for r in _q(main_db, "SELECT line_number, reason_code FROM reject_record "
                                                     "WHERE run_id=? AND source_name=?", (run_id, name))}
                exp = {(r["line"], r["reason"]) for r in labels[name]["expected_rejects"]}
                if got != exp:
                    bad.append(f"{name}: {len(got ^ exp)} differ")
            if bad:
                return "FAIL", "; ".join(bad)
            n = labels["customers"]["expected_rejects"].__len__() + len(labels["orders"]["expected_rejects"])
            return "PASS", f"all {n} injected defects rejected with the expected (line, reason)"
        check("rejects", "Reject/quarantine path; invalid records traceable (Step 3, acceptance)", "Data quality", c_rejects)

        def c_answers():
            exp = labels["expected_aggregates"]
            got = {
                "lines": _q(main_db, "SELECT COUNT(*) FROM fact_order_line")[0][0],
                "versions": _q(main_db, "SELECT COUNT(*) FROM dim_customer")[0][0],
                "net": dict(_q(main_db, "SELECT currency, SUM(line_amount_minor) FROM fact_order_line "
                                        "WHERE is_net_revenue=1 GROUP BY currency")),
            }
            ok = (got["lines"] == exp["order_lines"] and got["versions"] == exp["dim_customer_versions"]
                  and got["net"] == exp["net_revenue_minor_by_currency"])
            return ("PASS", "line count, SCD2 versions and net revenue per currency equal the independent answer key") \
                if ok else ("FAIL", f"got {got}")
        check("answers", "Key aggregates reconcile with independently derived expected values (Step 8)", "Correctness", c_answers)

        def c_invariants():
            conn = connect(main_db)
            try:
                checks = check_db_invariants(conn)
            finally:
                conn.close()
            bad = [c["name"] for c in checks if not c["passed"]]
            return ("FAIL", f"failed: {bad}") if bad else ("PASS", f"{len(checks)} warehouse invariants hold "
                                                                    "(keys, RI, SCD2 chain, fact = latest event)")
        check("invariants", "Primary keys and referential integrity checked (acceptance)", "Correctness", c_invariants)

        # ---------------------------------------------------------------- idempotency / recovery
        def c_rerun():
            before = _q(main_db, "SELECT (SELECT COUNT(*) FROM order_line_event), (SELECT COUNT(*) FROM fact_order_line), "
                                 "(SELECT COUNT(*) FROM dim_customer), (SELECT COUNT(*) FROM reject_record)")
            s2 = _run(main_db, out, sample / "customers.jsonl", sample / "orders.csv")
            skipped = all(x["status"] == "SKIPPED_ALREADY_LOADED" for x in s2["sources"])
            s3 = _run(main_db, out, sample / "customers.jsonl", sample / "orders.csv", force=True)
            new = sum(x["rows_inserted_new"] for x in s3["sources"])
            after = _q(main_db, "SELECT (SELECT COUNT(*) FROM order_line_event), (SELECT COUNT(*) FROM fact_order_line), "
                                "(SELECT COUNT(*) FROM dim_customer), (SELECT COUNT(*) FROM reject_record)")
            tables_same = before[0][:3] == after[0][:3]
            if skipped and new == 0 and tables_same:
                return "PASS", "identical rerun skipped; forced reload inserted 0 new rows; table counts unchanged"
            return "FAIL", f"skipped={skipped} new_rows={new} tables_same={tables_same}"
        check("rerun", "Rerun of the same input creates no duplicates (Step 6, edge case)", "Idempotency", c_rerun)

        def c_late():
            late = labels["orders_late"]
            before = _q(main_db, "SELECT COUNT(*) FROM order_line_event")[0][0]
            fact_before = _q(main_db, "SELECT status, COUNT(*) FROM fact_order_line GROUP BY status")
            s = _run(main_db, out, orders=sample / late["file"])
            after = _q(main_db, "SELECT COUNT(*) FROM order_line_event")[0][0]
            fact_after = _q(main_db, "SELECT status, COUNT(*) FROM fact_order_line GROUP BY status")
            ok = (after - before == late["expected_new_events"] and fact_before == fact_after
                  and s["reconciliation_passed"])
            return ("PASS", f"late batch added {after - before} history events; current state unchanged") if ok \
                else ("FAIL", f"added={after - before} expected={late['expected_new_events']}")
        check("late", "Late-arriving / out-of-order data; history preserved (edge case)", "Edge cases", c_late)

        def c_partial():
            db = tmp / "partial.db"
            _run(db, out, sample / "customers.jsonl")
            bad = _run(db, out, orders=sample / "orders.csv", batch_size=10, fail_after_rows=10)
            leaked = _q(db, "SELECT (SELECT COUNT(*) FROM order_line_event), (SELECT COUNT(*) FROM fact_order_line), "
                            "(SELECT COUNT(*) FROM ingested_file WHERE source_name='orders')")[0]
            good = _run(db, out, orders=sample / "orders.csv")
            ok = (bad["status"] == "FAILED" and leaked == (0, 0, 0) and good["status"] == "SUCCESS")
            return ("PASS", "crash mid-load rolled back completely (0 rows left); plain retry succeeded") if ok \
                else ("FAIL", f"bad={bad['status']} leaked={tuple(leaked)} retry={good['status']}")
        check("partial", "Partial failure and retry (edge case, Step 6)", "Idempotency", c_partial)

        def c_edges():
            db = tmp / "edges.db"
            _run(db, out, sample / "customers.jsonl")
            ec = sample / "edge_cases"
            empty = _run(db, out, orders=ec / "orders_empty_batch.csv")
            schema = _run(db, out, orders=ec / "orders_schema_change_missing_column.csv")
            extra = _run(db, out, orders=ec / "orders_schema_change_extra_column.csv")
            e_src = next(x for x in extra["sources"] if x["source_name"] == "orders")
            ok = (empty["status"] == "SUCCESS" and schema["status"] == "FAILED"
                  and "SCHEMA_ERROR" in (schema["failure_reason"] or "")
                  and any("UNEXPECTED_COLUMNS_IGNORED" in w for w in e_src["warnings"]))
            return ("PASS", "empty batch -> SUCCESS(0 rows); missing column -> FAILED(SCHEMA_ERROR); extra column -> warning") \
                if ok else ("FAIL", f"empty={empty['status']} schema={schema['status']} extra={extra['status']}")
        check("edges", "Empty batch and unexpected schema change (edge cases)", "Edge cases", c_edges)

        # ---------------------------------------------------------------- tests
        def c_tests():
            if not run_tests:
                raise _Skip("skipped (run_tests=False)")
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            suite = unittest.defaultTestLoader.discover(str(root / "tests"), top_level_dir=str(root))
            res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
            bad = len(res.failures) + len(res.errors)
            return ("PASS", f"{res.testsRun} tests, 0 failures") if bad == 0 \
                else ("FAIL", f"{res.testsRun} tests, {bad} failing")
        if run_tests:
            check("tests", "Automated tests pass: happy path, empty, malformed, duplicates, nulls, domain cases (Step 5)",
                  "Testing", c_tests)

    # ---------------------------------------------------------------- repo-level checks (no temp data)
    def c_bench():
        p = root / "output" / "benchmark" / "benchmark_results.json"
        if not p.exists():
            return "FAIL", "run: python -m orderflow benchmark --customers 100000 --order-lines 100000"
        r = json.loads(p.read_text(encoding="utf-8"))
        total, opt = r["input_rows"]["total"], r["optimisation_scd2_lookup_index"]
        ok = total >= 100_000 and r["first_run"]["status"] == "SUCCESS" and r["first_run"]["reconciliation_passed"]
        msg = (f"{total:,} rows in {r['first_run']['seconds']}s; index optimisation {opt['speedup_x']}x "
               f"({opt['without_index_seconds']}s -> {opt['with_index_seconds']}s)")
        return ("PASS", msg) if ok else ("FAIL", msg)
    check("bench", "100,000+ records run with performance measurement and one optimisation (Step 9)", "Performance", c_bench)

    def c_secrets():
        pats = [re.compile(p) for p in (r"AKIA[0-9A-Z]{16}", r"BEGIN [A-Z ]*PRIVATE KEY", r"(?i)\b(api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{6,}")]
        hits = []
        for f in root.rglob("*"):
            if f.is_file() and ".git" not in f.parts and "output" not in f.parts and f.suffix in {".py", ".md", ".txt", ".sql", ".json", ".csv", ".jsonl", ".yml", ".toml"}:
                text = f.read_text(encoding="utf-8", errors="ignore")
                if any(p.search(text) for p in pats):
                    hits.append(str(f.relative_to(root)))
        return ("FAIL", f"possible secrets in {hits}") if hits else ("PASS", "no credentials or key patterns found; data is synthetic")
    check("secrets", "No secrets or unauthorised data in the repo (Scope guidance, FAQ 12)", "Submission", c_secrets)

    def c_ai():
        need = [root / "docs/ai/PROMPT_HISTORY.md", root / "docs/ai/AI_USAGE.md"]
        if not all(p.exists() for p in need):
            return "FAIL", "AI disclosure files missing"
        unticked = (root / "docs/ai/AI_USAGE.md").read_text(encoding="utf-8").count("- [ ]")
        if unticked:
            return "WARN", (f"files present, but {unticked} checklist box(es) in AI_USAGE.md are still unticked - "
                            "tick only what you have really done before submitting")
        return "PASS", "prompt history and AI usage disclosure present and completed"
    check("ai", "Prompt history + transparent AI-use disclosure (YottaFlex guideline 03)", "Submission", c_ai)

    results.append({"id": "github", "group": "Submission", "status": "MANUAL",
                    "requirement": "Public GitHub repo with meaningful commits (YottaFlex guideline 02)",
                    "evidence": "cannot be checked by code: push to a public repo and open it in a private window"})
    results.append({"id": "explain", "group": "Submission", "status": "MANUAL",
                    "requirement": "You can explain every design choice (guideline 04, FAQ 7)",
                    "evidence": "use docs/WALKTHROUGH.md; verified only by you"})
    return results


def summarise(results: list[dict]) -> dict:
    counts: dict = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts