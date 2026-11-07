"""Reproducible synthetic data generator (fixed seed => byte-identical output).

Writes, into `out_dir`:
    customers.jsonl            source 1: customer change events (JSON Lines)
    orders.csv                 source 2: order-line status events (CSV)
    orders_late.csv            a second batch of late-arriving, older events (optional)
    expected/expected_labels.json   ground truth. Kept OUT of the input files on purpose.

The inputs contain normal data, boundary values (qty 1 / 10000, price 0.00 / 1000000.00), mixed
timezone offsets, out-of-order lines, exact + conflicting duplicates, no-op customer updates,
and deliberately invalid records. Every injected defect is labelled with the reason code the
pipeline is expected to give it and the physical line it ended up on.
"""
from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ALLOWED_COUNTRIES, ALLOWED_SEGMENTS, NON_REVENUE_STATUSES
from .validate import ORDER_OPTIONAL, ORDER_REQUIRED

ORDER_COLUMNS = ["order_id", "line_no", "customer_id", "product_code", "product_name", "category",
                 "quantity", "unit_price", "currency", "order_ts", "status", "status_ts"]
assert set(ORDER_COLUMNS) == set(ORDER_REQUIRED + ORDER_OPTIONAL)

CATEGORIES = ["Electronics", "Home", "Grocery", "Fashion", "Books", "Toys"]
CURRENCIES = ["INR", "USD", "EUR"]
FMT = "%Y-%m-%dT%H:%M:%SZ"


def _z(dt: datetime) -> str:
    return dt.strftime(FMT)


def _with_offset(dt: datetime, minutes: int) -> str:
    """Same instant written in a local timezone, e.g. 2026-03-01T15:30:00+05:30."""
    local = dt + timedelta(minutes=minutes)
    sign = "+" if minutes >= 0 else "-"
    m = abs(minutes)
    return local.strftime("%Y-%m-%dT%H:%M:%S") + f"{sign}{m // 60:02d}:{m % 60:02d}"


# --------------------------------------------------------------------------- customers
def _customer_events(rng: random.Random, n: int):
    base = datetime(2025, 12, 1)
    out = []  # (customer_id, datetime, attrs dict)
    for i in range(1, n + 1):
        cid = f"C{i:06d}"
        attrs = {"name": f"Customer {i}", "email": f"customer{i}@example.com",
                 "country": rng.choice(ALLOWED_COUNTRIES), "segment": rng.choice(ALLOWED_SEGMENTS)}
        t0 = base + timedelta(minutes=rng.randint(0, 40 * 24 * 60))
        out.append((cid, t0, dict(attrs)))
        k = rng.choices([0, 1, 2], weights=[0.60, 0.28, 0.12])[0]
        times = sorted(datetime(2026, 2, 1) + timedelta(minutes=rng.randint(0, 120 * 24 * 60)) for _ in range(k))
        for t in times:
            if rng.random() < 0.15:                       # no-op update: nothing tracked changed
                pass
            else:
                field = rng.choice(["segment", "country", "email"])
                if field == "segment":
                    attrs["segment"] = rng.choice([s for s in ALLOWED_SEGMENTS if s != attrs["segment"]])
                elif field == "country":
                    attrs["country"] = rng.choice([c for c in ALLOWED_COUNTRIES if c != attrs["country"]])
                else:
                    attrs["email"] = f"customer{i}.v{rng.randint(2, 99)}@example.org"
            out.append((cid, t, dict(attrs)))
    return out


def _cust_json(cid, t, attrs, minutes_offset=None) -> dict:
    ts = _z(t) if minutes_offset is None else _with_offset(t, minutes_offset)
    return {"customer_id": cid, "name": attrs["name"], "email": attrs["email"],
            "country": attrs["country"], "segment": attrs["segment"], "updated_at": ts}


CUSTOMER_INJECTORS = {
    "missing_name": (lambda r: {k: v for k, v in r.items() if k != "name"}, "MISSING_REQUIRED_FIELD"),
    "empty_email": (lambda r: {**r, "email": ""}, "MISSING_REQUIRED_FIELD"),
    "bad_email": (lambda r: {**r, "email": "not-an-email"}, "INVALID_FORMAT"),
    "bad_customer_id": (lambda r: {**r, "customer_id": "cust-17"}, "INVALID_FORMAT"),
    "bad_segment": (lambda r: {**r, "segment": "GOLD"}, "INVALID_ENUM"),
    "bad_country": (lambda r: {**r, "country": "Narnia"}, "INVALID_ENUM"),
    "bad_timestamp": (lambda r: {**r, "updated_at": "yesterday"}, "INVALID_TIMESTAMP"),
}


def _generate_customers(rng, n_customers, invalid_rate, dup_rate, out_dir: Path):
    events = _customer_events(rng, n_customers)
    valid = [{"obj": _cust_json(c, t, a, rng.choice([None, None, None, 330, -300])), "dt": t, "cid": c}
             for c, t, a in events]
    rng.shuffle(valid)                                         # out-of-order delivery
    n = len(valid)
    items = [{"key": float(i), "text": json.dumps(v["obj"]), "label": None} for i, v in enumerate(valid)]

    n_invalid = max(len(CUSTOMER_INJECTORS) + 1, int(n * invalid_rate))
    names = list(CUSTOMER_INJECTORS)
    for j in range(n_invalid):
        name = names[j] if j < len(names) else rng.choice(names)   # every injector at least once
        fn, reason = CUSTOMER_INJECTORS[name]
        src = rng.randrange(n)
        items.append({"key": rng.random() * n, "text": json.dumps(fn(valid[src]["obj"])), "label": reason})
    for _ in range(max(1, int(n * invalid_rate / 3))):         # truncated JSON
        src = rng.randrange(n)
        text = json.dumps(valid[src]["obj"])
        items.append({"key": rng.random() * n, "text": text[: len(text) // 2], "label": "MALFORMED_RECORD"})
    for _ in range(max(1, int(n * dup_rate))):                 # exact duplicates (after the original)
        src = rng.randrange(n)
        items.append({"key": src + 0.5 + rng.random() * (n - src - 1), "text": items[src]["text"], "label": "DUPLICATE_EVENT"})
    for _ in range(max(1, int(n * dup_rate / 2))):             # same key, different payload
        src = rng.randrange(n)
        obj = dict(valid[src]["obj"])
        obj["segment"] = "ENTERPRISE" if obj["segment"] != "ENTERPRISE" else "SMB"
        items.append({"key": src + 0.5 + rng.random() * (n - src - 1), "text": json.dumps(obj), "label": "CONFLICTING_DUPLICATE"})

    items.sort(key=lambda it: it["key"])
    path = out_dir / "customers.jsonl"
    with open(path, "w", encoding="utf-8", newline="\n") as f:   # always LF, also on Windows
        f.write("\n".join(it["text"] for it in items) + "\n")
    rejects = [{"line": i + 1, "reason": it["label"]} for i, it in enumerate(items) if it["label"]]
    by_cust: dict = {}
    for v in valid:
        by_cust.setdefault(v["cid"], []).append(v)
    for lst in by_cust.values():
        lst.sort(key=lambda v: v["dt"])
    return path, {"total_rows": len(items), "expected_accepted": n, "expected_rejects": rejects}, by_cust


# --------------------------------------------------------------------------- orders
def _scenario(rng, t0):
    """list of (status, status_ts) for one order line, ordered in time."""
    r = rng.random()
    ev = [("PLACED", t0)]
    if r < 0.10:
        ev.append(("CANCELLED", t0 + timedelta(hours=rng.randint(1, 24))))
    elif r < 0.75:
        t1 = t0 + timedelta(hours=rng.randint(24, 72))
        t2 = t1 + timedelta(hours=rng.randint(48, 168))
        ev += [("SHIPPED", t1), ("DELIVERED", t2)]
    elif r < 0.85:
        t1 = t0 + timedelta(hours=rng.randint(24, 72))
        t2 = t1 + timedelta(hours=rng.randint(48, 168))
        ev += [("SHIPPED", t1), ("DELIVERED", t2), ("RETURNED", t2 + timedelta(hours=rng.randint(72, 240)))]
    elif r < 0.93:
        ev.append(("SHIPPED", t0 + timedelta(hours=rng.randint(24, 72))))
    return ev


def _order_rows(rng, n_lines, n_customers, n_products):
    products = []
    for p in range(1, n_products + 1):
        products.append((f"P{p:04d}", f"Product {p}", "" if p % 17 == 0 else CATEGORIES[p % len(CATEGORIES)],
                         round(rng.uniform(1, 500), 2)))
    rows, truth = [], {}      # truth[(order_id, line_no)] = list of event dicts
    order_no, produced = 0, 0
    start = datetime(2026, 1, 15)
    while produced < n_lines:
        order_no += 1
        oid = f"ORD-{order_no:08d}"
        cid = f"C{rng.randint(1, n_customers):06d}"
        cur = rng.choices(CURRENCIES, weights=[0.5, 0.3, 0.2])[0]
        t0 = start + timedelta(seconds=rng.randint(0, 150 * 24 * 3600))
        for line_no in range(1, rng.choices([1, 2, 3, 4], weights=[0.4, 0.3, 0.2, 0.1])[0] + 1):
            code, pname, cat, price = rng.choice(products)
            qty = rng.choices([1, 2, 3, 4, 5], weights=[0.4, 0.25, 0.15, 0.1, 0.1])[0]
            roll = rng.random()
            if roll < 0.003:
                qty = 10_000                                   # boundary: max quantity
            elif roll < 0.006:
                price = 0.0                                    # boundary: free item
            elif roll < 0.008:
                qty, price = 1, 1_000_000.00                   # boundary: max price
            minor = round(price * 100)
            evs = []
            for status, ts in _scenario(rng, t0):
                use_offset = rng.random() < 0.05
                off = rng.choice([330, -300, 60]) if use_offset else None
                f = (lambda d: _z(d)) if off is None else (lambda d, o=off: _with_offset(d, o))
                row = {"order_id": oid, "line_no": str(line_no), "customer_id": cid, "product_code": code,
                       "product_name": pname, "category": cat, "quantity": str(qty),
                       "unit_price": f"{minor // 100}.{minor % 100:02d}", "currency": cur,
                       "order_ts": f(t0), "status": status, "status_ts": f(ts)}
                evs.append({"row": row, "status": status, "status_dt": ts})
            truth[(oid, line_no)] = {"customer_id": cid, "order_dt": t0, "currency": cur,
                                     "amount_minor": qty * minor, "events": evs}
            produced += 1
    return truth


ORDER_INJECTORS = {
    "missing_customer": (lambda r: {**r, "customer_id": ""}, "MISSING_REQUIRED_FIELD"),
    "bad_order_id": (lambda r: {**r, "order_id": "ORD-12"}, "INVALID_FORMAT"),
    "qty_zero": (lambda r: {**r, "quantity": "0"}, "OUT_OF_RANGE"),
    "qty_negative": (lambda r: {**r, "quantity": "-3"}, "OUT_OF_RANGE"),
    "qty_over_max": (lambda r: {**r, "quantity": "10001"}, "OUT_OF_RANGE"),
    "qty_text": (lambda r: {**r, "quantity": "two"}, "INVALID_FORMAT"),
    "price_negative": (lambda r: {**r, "unit_price": "-5.00"}, "OUT_OF_RANGE"),
    "price_3_decimals": (lambda r: {**r, "unit_price": "12.345"}, "INVALID_FORMAT"),
    "price_text": (lambda r: {**r, "unit_price": "N/A"}, "INVALID_FORMAT"),
    "bad_currency": (lambda r: {**r, "currency": "GBP"}, "INVALID_ENUM"),
    "bad_status": (lambda r: {**r, "status": "SHIPPING"}, "INVALID_ENUM"),
    "bad_order_ts": (lambda r: {**r, "order_ts": "2026-13-45T00:00:00Z"}, "INVALID_TIMESTAMP"),
    "naive_ts": (lambda r: {**r, "status_ts": "2026-03-01 10:00:00"}, "INVALID_TIMESTAMP"),
    "future_status_ts": (lambda r: {**r, "status_ts": "2099-01-01T00:00:00Z"}, "FUTURE_TIMESTAMP"),
    "status_before_order": (lambda r: {**r, "status_ts": "2000-01-01T00:00:00Z"}, "STATUS_BEFORE_ORDER"),
    "unknown_customer": (lambda r: {**r, "customer_id": "C999999"}, "INVALID_CUSTOMER_REF"),
}


def _generate_orders(rng, truth, invalid_rate, dup_rate, late_fraction, out_dir: Path):
    main, late = [], []
    for (_, _), t in truth.items():
        evs = t["events"]
        for idx, e in enumerate(evs):
            # withhold only NON-final events, so the final state of every line is unchanged
            is_late = late_fraction > 0 and idx < len(evs) - 1 and rng.random() < late_fraction
            (late if is_late else main).append(e["row"])
    rng.shuffle(main)
    rng.shuffle(late)
    n = len(main)
    items = [{"key": float(i), "row": r, "label": None} for i, r in enumerate(main)]

    names = list(ORDER_INJECTORS)
    n_invalid = max(len(names) + 1, int(n * invalid_rate))
    for j in range(n_invalid):
        name = names[j] if j < len(names) else rng.choice(names)
        fn, reason = ORDER_INJECTORS[name]
        items.append({"key": rng.random() * n, "row": fn(main[rng.randrange(n)]), "label": reason})
    for _ in range(max(1, int(n * invalid_rate / 4))):         # short row => MALFORMED_RECORD
        row = dict(main[rng.randrange(n)])
        items.append({"key": rng.random() * n, "row": row, "label": "MALFORMED_RECORD", "short": True})
    for _ in range(max(1, int(n * dup_rate))):
        src = rng.randrange(n)
        items.append({"key": src + 0.5 + rng.random() * (n - src - 1), "row": dict(main[src]), "label": "DUPLICATE_EVENT"})
    for _ in range(max(1, int(n * dup_rate / 2))):
        src = rng.randrange(n)
        row = dict(main[src])
        row["quantity"] = "7" if row["quantity"] != "7" else "8"          # same key, different payload
        items.append({"key": src + 0.5 + rng.random() * (n - src - 1), "row": row, "label": "CONFLICTING_DUPLICATE"})
    items.sort(key=lambda it: it["key"])

    def write(path, its):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(ORDER_COLUMNS)
            for it in its:
                vals = [it["row"].get(c, "") for c in ORDER_COLUMNS]
                w.writerow(vals[:-3] if it.get("short") else vals)

    path = out_dir / "orders.csv"
    write(path, items)
    rejects = [{"line": i + 2, "reason": it["label"]} for i, it in enumerate(items) if it["label"]]  # header = line 1
    meta = {"total_rows": len(items), "expected_accepted": n, "expected_rejects": rejects}
    late_meta = None
    if late:
        lp = out_dir / "orders_late.csv"
        write(lp, [{"row": r, "label": None} for r in late])
        late_meta = {"file": lp.name, "total_rows": len(late), "expected_new_events": len(late)}
    return path, meta, late_meta


# --------------------------------------------------------------------------- expected truth
def _expected_aggregates(truth, customers_by_id):
    """Independent re-derivation of the answers (no code shared with the pipeline)."""
    status_counts, net_by_cur, by_seg = {}, {}, {}
    for t in truth.values():
        final = max(t["events"], key=lambda e: e["status_dt"])
        status_counts[final["status"]] = status_counts.get(final["status"], 0) + 1
        if final["status"] in NON_REVENUE_STATUSES:
            continue
        net_by_cur[t["currency"]] = net_by_cur.get(t["currency"], 0) + t["amount_minor"]
        evs = customers_by_id[t["customer_id"]]
        seg = evs[0]["obj"]["segment"]                       # orders before first version -> earliest
        for e in evs:
            if e["dt"] <= t["order_dt"]:
                seg = e["obj"]["segment"]
        k = f"{seg}|{t['currency']}"
        by_seg[k] = by_seg.get(k, 0) + t["amount_minor"]
    versions = 0
    for evs in customers_by_id.values():
        prev = None
        for e in evs:
            attrs = tuple(e["obj"][k] for k in ("name", "email", "country", "segment"))
            if attrs != prev:
                versions += 1
            prev = attrs
    return {"order_lines": len(truth), "final_status_counts": dict(sorted(status_counts.items())),
            "net_revenue_minor_by_currency": dict(sorted(net_by_cur.items())),
            "net_revenue_minor_by_segment_at_order_time": dict(sorted(by_seg.items())),
            "customers": len(customers_by_id), "dim_customer_versions": versions}


def generate(out_dir, n_customers=25, n_order_lines=40, seed=42, invalid_rate=0.15,
             dup_rate=0.04, late_fraction=0.30, n_products=12) -> dict:
    out_dir = Path(out_dir)
    (out_dir / "expected").mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    _, cust_meta, by_cust = _generate_customers(rng, n_customers, invalid_rate, dup_rate, out_dir)
    truth = _order_rows(rng, n_order_lines, n_customers, n_products)
    _, ord_meta, late_meta = _generate_orders(rng, truth, invalid_rate, dup_rate, late_fraction, out_dir)
    labels = {
        "seed": seed,
        "params": {"customers": n_customers, "order_lines": n_order_lines, "invalid_rate": invalid_rate,
                   "dup_rate": dup_rate, "late_fraction": late_fraction},
        "customers": {"file": "customers.jsonl", **cust_meta},
        "orders": {"file": "orders.csv", **ord_meta},
        "orders_late": late_meta,
        "expected_aggregates": _expected_aggregates(truth, by_cust),
    }
    with open(out_dir / "expected" / "expected_labels.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(labels, indent=2))
    return labels


def write_edge_case_files(out_dir) -> None:
    """Small hand-made files for the negative scenarios (schema change, empty batch, ...)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    hdr = ",".join(ORDER_COLUMNS)
    (d / "orders_empty_batch.csv").write_text(hdr + "\n", encoding="utf-8")
    (d / "customers_empty_batch.jsonl").write_text("", encoding="utf-8")
    (d / "orders_schema_change_missing_column.csv").write_text(
        hdr.replace("unit_price", "price") + "\n"
        "ORD-00000001,1,C000001,P0001,Widget,Tools,2,10.50,INR,2026-02-01T10:00:00Z,PLACED,2026-02-01T10:00:00Z\n",
        encoding="utf-8")
    (d / "orders_schema_change_extra_column.csv").write_text(
        hdr + ",coupon_code\n"
        "ORD-00000001,1,C000001,P0001,Widget,Tools,2,10.50,INR,2026-02-01T10:00:00Z,PLACED,2026-02-01T10:00:00Z,SAVE10\n",
        encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    import sys
    print(json.dumps(generate(sys.argv[1] if len(sys.argv) > 1 else "data/sample")["expected_aggregates"], indent=2))
