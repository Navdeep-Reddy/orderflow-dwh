"""Performance measurement: generate N rows, run the pipeline, report throughput per stage, and
quantify one optimisation (the composite index behind the SCD2 point-in-time lookup)."""
from __future__ import annotations

import json
import resource
import shutil
import time
from pathlib import Path

from .config import Config
from .db import connect
from .generate_data import generate
from .load_customers import CUSTOMER_KEY_RESOLUTION
from .pipeline import run_pipeline


def _time_lookup(conn, sample: int) -> float:
    sql = ("SELECT SUM(LENGTH(k)) FROM (SELECT " + CUSTOMER_KEY_RESOLUTION.format(t="t") +
           f" AS k FROM (SELECT customer_id, order_ts FROM fact_order_line LIMIT {sample}) t)")
    t0 = time.perf_counter()
    conn.execute(sql).fetchone()
    return time.perf_counter() - t0


def run_benchmark(n_customers: int, n_order_lines: int, seed: int, out_dir: Path, batch_size: int = 5000) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    data = out_dir / "data"

    t0 = time.perf_counter()
    labels = generate(data, n_customers, n_order_lines, seed, invalid_rate=0.02, dup_rate=0.005,
                      late_fraction=0.0, n_products=200)
    gen_s = time.perf_counter() - t0
    rows = labels["customers"]["total_rows"] + labels["orders"]["total_rows"]

    cfg = Config(db_path=out_dir / "bench.db", output_dir=out_dir, batch_size=batch_size,
                 customers_file=data / "customers.jsonl", orders_file=data / "orders.csv",
                 as_of="2026-11-06T00:00:00Z")
    first = run_pipeline(cfg)
    skip = run_pipeline(cfg)                                              # same input: must be ~free
    forced = run_pipeline(Config(**{**cfg.__dict__, "force": True}))     # idempotent re-load of everything

    by = {s["source_name"]: s for s in first["sources"]}
    conn = connect(cfg.db_path)
    sample = 5000
    with_idx = _time_lookup(conn, sample)
    conn.execute("DROP INDEX ix_dim_customer_lookup")
    without_idx = _time_lookup(conn, sample)
    conn.execute("CREATE INDEX ix_dim_customer_lookup ON dim_customer(customer_id, valid_from)")
    result = {
        "input_rows": {"customers": by["customers"]["rows_read"], "orders": by["orders"]["rows_read"], "total": rows},
        "generation_seconds": round(gen_s, 2),
        "first_run": {"status": first["status"], "seconds": first["duration_seconds"],
                      "rows_per_second": round(rows / first["duration_seconds"]),
                      "reconciliation_passed": first["reconciliation_passed"],
                      "stages_seconds": {n: {"validate": round(s["validate_seconds"], 2), "load": round(s["load_seconds"], 2)}
                                         for n, s in by.items()}},
        "rerun_same_input_seconds": skip["duration_seconds"],
        "forced_reload_seconds": forced["duration_seconds"],
        "forced_reload_new_rows": sum(s["rows_inserted_new"] for s in forced["sources"]),
        "fact_rows": conn.execute("SELECT COUNT(*) FROM fact_order_line").fetchone()[0],
        "db_size_mb": round(cfg.db_path.stat().st_size / 1e6, 1),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "optimisation_scd2_lookup_index": {
            "what": f"point-in-time customer_key lookup for {sample} fact rows",
            "with_index_seconds": round(with_idx, 4), "without_index_seconds": round(without_idx, 4),
            "speedup_x": round(without_idx / with_idx, 1) if with_idx else None},
    }
    conn.close()
    (out_dir / "benchmark_results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
