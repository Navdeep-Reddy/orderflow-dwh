"""Command line interface:  python -m orderflow <generate|run|validate|report|benchmark> ..."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config

def _cmd_benchmark(a) -> int:
    from .benchmark import run_benchmark
    print(json.dumps(run_benchmark(a.customers, a.order_lines, a.seed, Path(a.output_dir), a.batch_size), indent=2))
    return 0
def _cmd_verify(a) -> int:
    from .verify import run_verification, summarise

    results = run_verification(run_tests=not a.skip_tests)
    counts = summarise(results)

    print("\nORDERFLOW REQUIREMENT VERIFICATION")
    print("=" * 60)

    for result in results:
        print(
            f"[{result['status']:<6}] "
            f"{result['requirement']}\n"
            f"         Evidence: {result['evidence']}"
        )

    print("\nSUMMARY")
    print("-" * 60)

    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")

    failures = counts.get("FAIL", 0)

    if failures:
        print("\nVerification completed with failures.")
        return 1

    print("\nVerification completed successfully.")
    return 0


def _setup_logging(level: str, output_dir: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(output_dir / "pipeline.log", encoding="utf-8"))
    logging.basicConfig(level=getattr(logging, level.upper()), handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s %(message)s")


def _cmd_generate(a) -> int:
    from .generate_data import generate, write_edge_case_files
    labels = generate(a.out, a.customers, a.order_lines, a.seed, a.invalid_rate, a.dup_rate,
                      a.late_fraction, a.products)
    if a.edge_cases:
        write_edge_case_files(Path(a.out) / "edge_cases")
    print(json.dumps({"out": a.out, "customers_rows": labels["customers"]["total_rows"],
                      "orders_rows": labels["orders"]["total_rows"],
                      "late_rows": (labels["orders_late"] or {}).get("total_rows", 0)}))
    return 0
def _cmd_dashboard(a) -> int:
    from .db import connect
    from .dashboard import build_dashboard
    from .verify import run_verification

    db_path = Path(a.db)
    output_dir = Path(a.output_dir)

    if not db_path.exists():
        print(f"ERROR: Warehouse database not found: {db_path}")
        print("Run the pipeline first using: python -m orderflow run")
        return 1

    # Run live requirement verification unless explicitly skipped.
    checks = None
    if not a.skip_verify:
        print("Running live requirement verification...")
        checks = run_verification()

    # Build the dashboard.
    conn = connect(db_path)

    try:
        dashboard_path = build_dashboard(
            conn=conn,
            out_path=output_dir / "dashboard.html",
            checks=checks,
            bench_path=output_dir / "benchmark" / "benchmark_results.json",
        )
    finally:
        conn.close()

    print(f"\nDashboard generated successfully:")
    print(dashboard_path.resolve())

    if checks is not None:
        failures = sum(1 for c in checks if c["status"] == "FAIL")
        print(f"Requirement checks: {len(checks)}")
        print(f"Failed checks: {failures}")

        if failures:
            return 1

    return 0


def _cmd_run(a) -> int:
    from .pipeline import run_pipeline
    out = Path(a.output_dir)
    _setup_logging(a.log_level, out)
    cfg = Config(db_path=Path(a.db or out / "warehouse.db"), output_dir=out,
                 customers_file=Path(a.customers) if a.customers else None,
                 orders_file=Path(a.orders) if a.orders else None,
                 max_reject_ratio=a.max_reject_ratio, as_of=a.as_of, force=a.force,
                 batch_size=a.batch_size, run_id=a.run_id)
    s = run_pipeline(cfg)
    print(json.dumps({k: s[k] for k in ("run_id", "status", "failure_reason", "duration_seconds",
                                       "reconciliation_passed")}, indent=2))
    for src in s["sources"]:
        print(f"  {src['source_name']:<10} {src['status']:<24} read={src['rows_read']:<7} "
              f"accepted={src['rows_accepted']:<7} rejected={src['rows_rejected']:<6} "
              f"new={src['rows_inserted_new']:<7} already_present={src['rows_already_present']:<6} "
              f"reasons={src['reject_reasons']}")
    failed = [c for c in s["reconciliation"] if not c["passed"]]
    for c in failed:
        print(f"  RECONCILIATION FAILED: {c['name']} expected={c['expected']} actual={c['actual']}")
    if s["status"] != "SUCCESS":
        return 1
    return 2 if failed else 0


def _cmd_validate(a) -> int:
    from .db import connect
    from .reconcile import check_db_invariants
    conn = connect(a.db)
    checks = check_db_invariants(conn)
    for c in checks:
        print(f"[{'PASS' if c['passed'] else 'FAIL'}] {c['name']}: expected={c['expected']} actual={c['actual']}")
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation_report.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    return 0 if all(c["passed"] for c in checks) else 2


def _cmd_report(a) -> int:
    from .db import connect
    from .reports import export_reports
    p = export_reports(connect(a.db), a.output_dir)
    print(f"wrote {p} and CSVs next to it")
    return 0


def _cmd_benchmark(a) -> int:
    from .benchmark import run_benchmark
    print(json.dumps(run_benchmark(a.customers, a.order_lines, a.seed, Path(a.output_dir), a.batch_size), indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="orderflow", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate reproducible synthetic input data + expected labels")
    g.add_argument("--out", default="data/sample")
    g.add_argument("--customers", type=int, default=25)
    g.add_argument("--order-lines", type=int, default=40)
    g.add_argument("--products", type=int, default=12)
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--invalid-rate", type=float, default=0.15)
    g.add_argument("--dup-rate", type=float, default=0.04)
    g.add_argument("--late-fraction", type=float, default=0.30)
    g.add_argument("--edge-cases", action="store_true", help="also write small negative-scenario files")
    g.set_defaults(fn=_cmd_generate)

    r = sub.add_parser("run", help="run the pipeline")
    r.add_argument("--customers"), r.add_argument("--orders")
    r.add_argument("--db"), r.add_argument("--output-dir", default="output")
    r.add_argument("--as-of", help="ISO-8601 reference time for the future-timestamp rule (default: now)")
    r.add_argument("--max-reject-ratio", type=float, default=0.30)
    r.add_argument("--batch-size", type=int, default=5000)
    r.add_argument("--run-id")
    r.add_argument("--force", action="store_true", help="re-load files even if identical content was loaded before")
    r.add_argument("--log-level", default="INFO")
    r.set_defaults(fn=_cmd_run)

    v = sub.add_parser("validate", help="check warehouse invariants (RI, SCD2, fact = latest event)")
    v.add_argument("--db", default="output/warehouse.db"), v.add_argument("--output-dir", default="output")
    v.set_defaults(fn=_cmd_validate)

    rp = sub.add_parser("report", help="run analytical queries; write CSV + Markdown")
    rp.add_argument("--db", default="output/warehouse.db"), rp.add_argument("--output-dir", default="output")
    rp.set_defaults(fn=_cmd_report)

    b = sub.add_parser("benchmark", help="generate a large dataset, run the pipeline, time it")
    b.add_argument("--customers", type=int, default=5000)
    b.add_argument("--order-lines", type=int, default=50000)
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--batch-size", type=int, default=5000)
    b.add_argument("--output-dir", default="output/benchmark")
    b.set_defaults(fn=_cmd_benchmark)

    # Verify project requirements
    vf = sub.add_parser(
        "verify",
        help="run live verification of project requirements"
    )
    vf.add_argument("--skip-tests", action="store_true")
    vf.set_defaults(fn=_cmd_verify)

    # Generate dashboard
    ds = sub.add_parser(
        "dashboard",
        help="generate HTML dashboard"
    )
    ds.add_argument("--db", default="output/warehouse.db")
    ds.add_argument("--output-dir", default="output")
    ds.add_argument("--skip-verify", action="store_true")
    ds.set_defaults(fn=_cmd_dashboard)

    a = p.parse_args(argv)
    return a.fn(a)

   

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
