"""Source 2: order-line status events (CSV) -> order_line_event (append-only) -> dim_product /
dim_date / fact_order_line (current state of each line = its latest event).

Why events? Order status changes over time and events may arrive late or out of order. We never
overwrite history: every (order_id, line_no, status_ts) is inserted once (INSERT OR IGNORE makes
reruns idempotent) and the fact row is *derived* from the event with the greatest status_ts.
"""
from __future__ import annotations

from datetime import date

from . import ids
from .config import Config, NON_REVENUE_STATUSES
from .load_customers import CUSTOMER_KEY_RESOLUTION
from .runlog import Extracted, SourceStats
from .sources import CsvSource
from .timeutil import utc_now_iso
from .validate import ORDER_OPTIONAL, ORDER_REQUIRED, Reject, validate_order_row

NAME = "orders"
COMPARED = ("customer_id", "product_code", "quantity", "unit_price_minor", "currency",
            "order_ts", "status", "line_amount_minor")


class InjectedFailure(RuntimeError):
    """Raised by the cfg.fail_after_rows test hook to simulate a crash mid-load."""


def extract(conn, path, cfg: Config, latest_allowed_ts: str) -> Extracted:
    src = CsvSource(path, ORDER_REQUIRED, ORDER_OPTIONAL)   # raises SourceSchemaError on contract break
    known_customers = {r[0] for r in conn.execute("SELECT DISTINCT customer_id FROM dim_customer")}
    ex = Extracted()
    seen: dict = {}
    for line, rec, raw, err in src:
        ex.rows_read += 1
        if err:
            ex.rejects.append((line, err[0], err[1], raw))
            continue
        try:
            clean = validate_order_row(rec, latest_allowed_ts)
        except Reject as r:
            ex.rejects.append((line, r.code, r.detail, raw))
            continue
        if clean["customer_id"] not in known_customers:  # referential integrity
            ex.rejects.append((line, "INVALID_CUSTOMER_REF",
                               f"customer_id={clean['customer_id']} not found in dim_customer", raw))
            continue
        key = (clean["order_id"], clean["line_no"], clean["status_ts"])
        payload = tuple(clean[c] for c in COMPARED)
        if key in seen:
            code = "DUPLICATE_EVENT" if seen[key] == payload else "CONFLICTING_DUPLICATE"
            ex.rejects.append((line, code, f"event {key} already seen earlier in file", raw))
            continue
        seen[key] = payload
        clean["_line"] = line
        ex.accepted.append(clean)
    ex.warnings = src.warnings
    return ex


def _dim_date_row(iso_day: str):
    d = date.fromisoformat(iso_day)
    return (int(iso_day.replace("-", "")), iso_day, d.year, d.month, d.day,
            d.isocalendar()[1], d.isoweekday(), 1 if d.isoweekday() >= 6 else 0)


def load(conn, ex: Extracted, run_id: str, cfg: Config, stats: SourceStats) -> None:
    now = utc_now_iso()
    # deterministic processing order -> deterministic SCD1 results regardless of file order
    events = sorted(ex.accepted, key=lambda e: (e["status_ts"], e["order_id"], e["line_no"]))
    stats.expected_amount_minor = sum(e["line_amount_minor"] for e in events)

    # dimensions first (facts reference them)
    conn.executemany("INSERT OR IGNORE INTO dim_date VALUES (?,?,?,?,?,?,?,?)",
                     [_dim_date_row(d) for d in sorted({e["order_ts"][:10] for e in events})])
    conn.executemany(
        """INSERT INTO dim_product (product_key, product_code, product_name, category, last_event_ts, run_id)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(product_code) DO UPDATE SET product_name=excluded.product_name,
             category=excluded.category, last_event_ts=excluded.last_event_ts, run_id=excluded.run_id
           WHERE excluded.last_event_ts >= dim_product.last_event_ts""",
        [(ids.product_key(e["product_code"]), e["product_code"], e["product_name"], e["category"],
          e["status_ts"], run_id) for e in events])

    conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_event_batch (order_id TEXT, line_no INTEGER, "
                 "status_ts TEXT, customer_id TEXT, product_code TEXT, quantity INTEGER, "
                 "unit_price_minor INTEGER, currency TEXT, order_ts TEXT, status TEXT, "
                 "line_amount_minor INTEGER, PRIMARY KEY (order_id, line_no, status_ts))")
    conn.execute("DELETE FROM tmp_event_batch")

    written = 0
    for i in range(0, len(events), cfg.batch_size):
        chunk = events[i:i + cfg.batch_size]
        conn.executemany(
            "INSERT OR IGNORE INTO order_line_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(e["order_id"], e["line_no"], e["status_ts"], e["customer_id"], e["product_code"],
              e["product_name"], e["category"], e["quantity"], e["unit_price_minor"], e["currency"],
              e["line_amount_minor"], e["order_ts"], e["status"], run_id, stats.source_file,
              e["_line"], now) for e in chunk])
        conn.executemany(
            "INSERT INTO tmp_event_batch VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(e["order_id"], e["line_no"], e["status_ts"], e["customer_id"], e["product_code"],
              e["quantity"], e["unit_price_minor"], e["currency"], e["order_ts"], e["status"],
              e["line_amount_minor"]) for e in chunk])
        written += len(chunk)
        if cfg.fail_after_rows is not None and written >= cfg.fail_after_rows:
            raise InjectedFailure(f"injected failure after {written} rows")

    stats.rows_inserted_new = conn.execute(
        "SELECT COUNT(*) FROM order_line_event WHERE run_id=?", (run_id,)).fetchone()[0]
    stats.rows_already_present = len(events) - stats.rows_inserted_new
    # source-to-target: every accepted event must be in the target with an identical payload
    found, amount = conn.execute(
        """SELECT COUNT(*), COALESCE(SUM(e.line_amount_minor), 0) FROM tmp_event_batch b
             JOIN order_line_event e ON e.order_id=b.order_id AND e.line_no=b.line_no AND e.status_ts=b.status_ts
              AND e.customer_id=b.customer_id AND e.product_code=b.product_code AND e.quantity=b.quantity
              AND e.unit_price_minor=b.unit_price_minor AND e.currency=b.currency AND e.order_ts=b.order_ts
              AND e.status=b.status AND e.line_amount_minor=b.line_amount_minor""").fetchone()
    stats.target_rows_found, stats.target_amount_minor = found, amount

    rebuild_fact(conn, run_id)


def rebuild_fact(conn, run_id: str) -> None:
    """Derive current state for every line that received a NEW event in this run."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_affected_lines "
                 "(order_id TEXT, line_no INTEGER, PRIMARY KEY (order_id, line_no))")
    conn.execute("DELETE FROM tmp_affected_lines")
    conn.execute("INSERT INTO tmp_affected_lines SELECT DISTINCT order_id, line_no "
                 "FROM order_line_event WHERE run_id=?", (run_id,))
    non_rev = ",".join(f"'{s}'" for s in NON_REVENUE_STATUSES)
    conn.execute(
        f"""INSERT INTO fact_order_line
              (order_line_key, order_id, line_no, customer_key, customer_id, product_key, date_key,
               order_ts, status, status_ts, quantity, unit_price_minor, currency, line_amount_minor,
               is_net_revenue, run_id)
            SELECT order_line_key(e.order_id, e.line_no), e.order_id, e.line_no,
                   {CUSTOMER_KEY_RESOLUTION.format(t="e")},
                   e.customer_id, p.product_key, date_key(e.order_ts), e.order_ts, e.status, e.status_ts,
                   e.quantity, e.unit_price_minor, e.currency, e.line_amount_minor,
                   CASE WHEN e.status IN ({non_rev}) THEN 0 ELSE 1 END, :run_id
              FROM tmp_affected_lines a
              JOIN order_line_event e ON e.order_id = a.order_id AND e.line_no = a.line_no
              JOIN dim_product p ON p.product_code = e.product_code
             WHERE e.status_ts = (SELECT MAX(x.status_ts) FROM order_line_event x
                                   WHERE x.order_id = a.order_id AND x.line_no = a.line_no)
            ON CONFLICT(order_line_key) DO UPDATE SET
              customer_key=excluded.customer_key, customer_id=excluded.customer_id,
              product_key=excluded.product_key, date_key=excluded.date_key, order_ts=excluded.order_ts,
              status=excluded.status, status_ts=excluded.status_ts, quantity=excluded.quantity,
              unit_price_minor=excluded.unit_price_minor, currency=excluded.currency,
              line_amount_minor=excluded.line_amount_minor, is_net_revenue=excluded.is_net_revenue,
              run_id=excluded.run_id""",
        {"run_id": run_id})
