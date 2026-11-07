"""Reconciliation: proves source-to-target consistency.

run_checks()          - run-scoped: source counts/amounts vs what landed in the target
check_db_invariants() - state-scoped: referential integrity, SCD2 integrity, fact == latest event
Every check returns {name, expected, actual, passed, detail}. Results are persisted in
reconciliation_result so they can be audited later.
"""
from __future__ import annotations

from typing import Iterable


def _chk(name, expected, actual, detail=""):
    return {"name": name, "expected": expected, "actual": actual,
            "passed": expected == actual, "detail": detail}


def _scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def run_checks(conn, run_id: str, sources: Iterable) -> list[dict]:
    checks: list[dict] = []
    for s in sources:
        n = s.source_name
        if s.status == "FAILED":
            # nothing may have leaked into the target from a failed source
            leaked = _scalar(conn, "SELECT COUNT(*) FROM %s WHERE run_id=?" %
                             ("stg_customer_event" if n == "customers" else "order_line_event"), (run_id,))
            checks.append(_chk(f"{n}.failed_source_loaded_nothing", 0, leaked,
                               "a failed source must be rolled back completely"))
            continue
        if s.status == "SKIPPED_ALREADY_LOADED":
            continue
        checks.append(_chk(f"{n}.rows_read = rejected + accepted", s.rows_read,
                           s.rows_rejected + s.rows_accepted))
        checks.append(_chk(f"{n}.accepted = inserted_new + already_present", s.rows_accepted,
                           s.rows_inserted_new + s.rows_already_present))
        checks.append(_chk(f"{n}.accepted_keys_present_in_target_with_same_payload", s.rows_accepted,
                           s.target_rows_found, "detects silent drops and conflicting re-sends"))
        persisted = _scalar(conn, "SELECT COUNT(*) FROM reject_record WHERE run_id=? AND source_name=?", (run_id, n))
        checks.append(_chk(f"{n}.rejects_persisted_and_traceable", s.rows_rejected, persisted))
        untraceable = _scalar(conn, "SELECT COUNT(*) FROM reject_record WHERE run_id=? AND source_name=? AND "
                                    "(raw_record='' OR source_file='' OR line_number<1)", (run_id, n))
        checks.append(_chk(f"{n}.rejects_have_file_line_raw", 0, untraceable))
        if n == "orders":
            checks.append(_chk("orders.sum(line_amount) source = target", s.expected_amount_minor,
                               s.target_amount_minor, "key aggregate in minor units"))
    checks += check_db_invariants(conn)
    conn.execute("DELETE FROM reconciliation_result WHERE run_id=?", (run_id,))
    conn.executemany(
        "INSERT INTO reconciliation_result (run_id, check_name, expected, actual, passed, detail) VALUES (?,?,?,?,?,?)",
        [(run_id, c["name"], str(c["expected"]), str(c["actual"]), int(c["passed"]), c["detail"]) for c in checks])
    return checks


def check_db_invariants(conn) -> list[dict]:
    q = lambda sql: _scalar(conn, sql)  # noqa: E731
    out = [
        _chk("fact.rows = distinct order lines in event history", q("SELECT COUNT(*) FROM (SELECT 1 FROM order_line_event GROUP BY order_id, line_no)"),
             q("SELECT COUNT(*) FROM fact_order_line")),
        _chk("fact.no_orphan_customer_key", 0, q("SELECT COUNT(*) FROM fact_order_line f LEFT JOIN dim_customer d USING (customer_key) WHERE d.customer_key IS NULL")),
        _chk("fact.no_orphan_product_key", 0, q("SELECT COUNT(*) FROM fact_order_line f LEFT JOIN dim_product d USING (product_key) WHERE d.product_key IS NULL")),
        _chk("fact.no_orphan_date_key", 0, q("SELECT COUNT(*) FROM fact_order_line f LEFT JOIN dim_date d USING (date_key) WHERE d.date_key IS NULL")),
        _chk("fact.state = latest event (status mismatches)", 0, q(
            """SELECT COUNT(*) FROM fact_order_line f JOIN order_line_event e
                 ON e.order_id=f.order_id AND e.line_no=f.line_no
                WHERE e.status_ts = (SELECT MAX(x.status_ts) FROM order_line_event x WHERE x.order_id=e.order_id AND x.line_no=e.line_no)
                  AND (e.status<>f.status OR e.line_amount_minor<>f.line_amount_minor OR e.status_ts<>f.status_ts)""")),
        _chk("fact.sum(line_amount) = sum(quantity*unit_price)", 0, q(
            "SELECT COUNT(*) FROM fact_order_line WHERE line_amount_minor <> quantity*unit_price_minor")),
        _chk("fact.customer_key is the version valid at order_ts", 0, q(
            """SELECT COUNT(*) FROM fact_order_line f JOIN dim_customer d ON d.customer_key=f.customer_key
                WHERE d.customer_id<>f.customer_id
                   OR (f.order_ts >= (SELECT MIN(valid_from) FROM dim_customer m WHERE m.customer_id=f.customer_id)
                       AND NOT (d.valid_from <= f.order_ts AND f.order_ts < d.valid_to))""")),
        _chk("dim_customer.exactly_one_current_per_customer", 0, q(
            """SELECT COUNT(*) FROM (SELECT customer_id FROM dim_customer GROUP BY customer_id HAVING SUM(is_current) <> 1)""")),
        _chk("dim_customer.no_gaps_or_overlaps", 0, q(
            """SELECT COUNT(*) FROM dim_customer a JOIN dim_customer b
                 ON a.customer_id=b.customer_id AND a.customer_key<>b.customer_key
                WHERE a.valid_from < b.valid_from AND a.valid_to > b.valid_from""")),
        _chk("dim_customer.chain_contiguous", 0, q(
            """SELECT COUNT(*) FROM dim_customer a WHERE a.is_current=0 AND NOT EXISTS
                 (SELECT 1 FROM dim_customer b WHERE b.customer_id=a.customer_id AND b.valid_from=a.valid_to)""")),
        _chk("dim_customer.pk_unique (customer_id, valid_from)", 0, q(
            "SELECT COUNT(*) FROM (SELECT 1 FROM dim_customer GROUP BY customer_id, valid_from HAVING COUNT(*)>1)")),
        _chk("dim_product.unique_product_code", 0, q(
            "SELECT COUNT(*) FROM (SELECT 1 FROM dim_product GROUP BY product_code HAVING COUNT(*)>1)")),
    ]
    return out
