"""Source 1: customer change events (JSON Lines) -> stg_customer_event (history) -> dim_customer (SCD2).

SCD2 strategy ("rebuild affected customers"): the append-only event table is the source of truth.
For each customer that received NEW events in this run we recompute all versions from the full
event history. This makes out-of-order / late-arriving events and reruns trivially correct and
idempotent, and surrogate keys stay stable because they are derived from (customer_id, valid_from).
"""
from __future__ import annotations

from . import ids
from .config import HIGH_DATE, Config
from .runlog import Extracted, SourceStats
from .sources import JsonlSource
from .timeutil import utc_now_iso
from .validate import CUSTOMER_FIELDS, Reject, validate_customer

NAME = "customers"
TRACKED = ("name", "email", "country", "segment")


def extract(conn, path, cfg: Config, latest_allowed_ts: str) -> Extracted:
    src = JsonlSource(path, CUSTOMER_FIELDS)
    ex = Extracted()
    seen: dict = {}
    for line, rec, raw, err in src:
        ex.rows_read += 1
        if err:
            ex.rejects.append((line, err[0], err[1], raw))
            continue
        try:
            clean = validate_customer(rec)
        except Reject as r:
            ex.rejects.append((line, r.code, r.detail, raw))
            continue
        key = (clean["customer_id"], clean["updated_at"])
        payload = tuple(clean[c] for c in TRACKED)
        if key in seen:
            code = "DUPLICATE_EVENT" if seen[key] == payload else "CONFLICTING_DUPLICATE"
            ex.rejects.append((line, code, f"same (customer_id, updated_at) already seen in file: {key}", raw))
            continue
        seen[key] = payload
        clean["_line"] = line
        ex.accepted.append(clean)
    ex.warnings = src.warnings
    return ex


def build_versions(events: list[dict]) -> list[dict]:
    """events sorted by updated_at -> SCD2 versions. Consecutive events with identical tracked
    attributes ("no-op updates") are collapsed so they do not create a spurious version."""
    versions: list[dict] = []
    for e in events:
        attrs = tuple(e[c] for c in TRACKED)
        if versions and versions[-1]["attrs"] == attrs:
            continue
        versions.append({"attrs": attrs, "customer_id": e["customer_id"], "valid_from": e["updated_at"]})
    for i, v in enumerate(versions):
        last = i == len(versions) - 1
        v["valid_to"] = HIGH_DATE if last else versions[i + 1]["valid_from"]
        v["is_current"] = 1 if last else 0
        v["customer_key"] = ids.customer_key(v["customer_id"], v["valid_from"])
    return versions


CUSTOMER_KEY_RESOLUTION = """
    COALESCE(
      (SELECT d.customer_key FROM dim_customer d
        WHERE d.customer_id = {t}.customer_id AND d.valid_from <= {t}.order_ts AND {t}.order_ts < d.valid_to),
      (SELECT d.customer_key FROM dim_customer d
        WHERE d.customer_id = {t}.customer_id ORDER BY d.valid_from LIMIT 1))
"""  # point-in-time lookup; orders older than the first known version map to the earliest version


def load(conn, ex: Extracted, run_id: str, cfg: Config, stats: SourceStats) -> None:
    now = utc_now_iso()
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_customer_batch ("
                 "customer_id TEXT, updated_at TEXT, name TEXT, email TEXT, country TEXT, segment TEXT, "
                 "PRIMARY KEY (customer_id, updated_at))")
    conn.execute("DELETE FROM tmp_customer_batch")
    rows = [(c["customer_id"], c["updated_at"], c["name"], c["email"], c["country"], c["segment"],
             run_id, stats.source_file, c["_line"], now) for c in ex.accepted]
    for i in range(0, len(rows), cfg.batch_size):
        chunk = rows[i:i + cfg.batch_size]
        conn.executemany("INSERT OR IGNORE INTO stg_customer_event VALUES (?,?,?,?,?,?,?,?,?,?)", chunk)
        conn.executemany("INSERT INTO tmp_customer_batch VALUES (?,?,?,?,?,?)", [r[:6] for r in chunk])

    stats.rows_inserted_new = conn.execute(
        "SELECT COUNT(*) FROM stg_customer_event WHERE run_id=?", (run_id,)).fetchone()[0]
    stats.rows_already_present = len(rows) - stats.rows_inserted_new
    # every accepted event must exist in the target with an identical payload
    stats.target_rows_found = conn.execute(
        """SELECT COUNT(*) FROM tmp_customer_batch b JOIN stg_customer_event s
             ON s.customer_id=b.customer_id AND s.updated_at=b.updated_at AND s.name=b.name
            AND s.email=b.email AND s.country=b.country AND s.segment=b.segment""").fetchone()[0]

    affected = [r[0] for r in conn.execute(
        "SELECT DISTINCT customer_id FROM stg_customer_event WHERE run_id=?", (run_id,))]
    rebuild_dim_customer(conn, affected, run_id)


def rebuild_dim_customer(conn, customer_ids: list[str], run_id: str) -> None:
    if not customer_ids:
        return
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_affected_customers (customer_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_customer_keep (customer_key TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM tmp_affected_customers")
    conn.execute("DELETE FROM tmp_customer_keep")
    conn.executemany("INSERT INTO tmp_affected_customers VALUES (?)", [(c,) for c in customer_ids])

    # 1) demote current flags first so the partial-unique "one current row" index never trips
    conn.execute("UPDATE dim_customer SET is_current=0 WHERE is_current=1 AND customer_id IN "
                 "(SELECT customer_id FROM tmp_affected_customers)")

    # 2) recompute versions from full history and upsert them
    cur = conn.execute(
        """SELECT s.customer_id, s.updated_at, s.name, s.email, s.country, s.segment
             FROM stg_customer_event s JOIN tmp_affected_customers a ON a.customer_id = s.customer_id
            ORDER BY s.customer_id, s.updated_at""")
    upserts, keep, current_id, bucket = [], [], None, []

    def flush():
        for v in build_versions(bucket):
            n, e, c, s = v["attrs"]
            upserts.append((v["customer_key"], v["customer_id"], n, e, c, s, v["valid_from"],
                            v["valid_to"], v["is_current"], run_id))
            keep.append((v["customer_key"],))

    for row in cur:
        if row["customer_id"] != current_id:
            if bucket:
                flush()
            current_id, bucket = row["customer_id"], []
        bucket.append(dict(row))
    if bucket:
        flush()

    conn.executemany(
        """INSERT INTO dim_customer (customer_key, customer_id, name, email, country, segment,
                                     valid_from, valid_to, is_current, run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(customer_key) DO UPDATE SET name=excluded.name, email=excluded.email,
             country=excluded.country, segment=excluded.segment, valid_to=excluded.valid_to,
             is_current=excluded.is_current, run_id=excluded.run_id""", upserts)
    conn.executemany("INSERT INTO tmp_customer_keep VALUES (?)", keep)

    # 3) re-point already-loaded facts to the version valid at order time (late-arriving history)
    conn.execute(
        "UPDATE fact_order_line SET customer_key = " + CUSTOMER_KEY_RESOLUTION.format(t="fact_order_line") +
        " WHERE customer_id IN (SELECT customer_id FROM tmp_affected_customers)")

    # 4) drop versions that no longer exist (e.g. merged by a late event); nothing references them now
    conn.execute(
        "DELETE FROM dim_customer WHERE customer_id IN (SELECT customer_id FROM tmp_affected_customers) "
        "AND customer_key NOT IN (SELECT customer_key FROM tmp_customer_keep)")
