"""Row-level validation + normalisation. Pure functions, no I/O: easy to unit-test.

Each validator returns a clean dict or raises Reject(code, detail). Only the FIRST failing
rule is reported (rules run in a fixed order, so results are deterministic).

Reason codes
  MALFORMED_RECORD | MISSING_REQUIRED_FIELD | INVALID_FORMAT | INVALID_ENUM | INVALID_TIMESTAMP |
  OUT_OF_RANGE | FUTURE_TIMESTAMP | STATUS_BEFORE_ORDER |
  DUPLICATE_EVENT | CONFLICTING_DUPLICATE | INVALID_CUSTOMER_REF   (the last three are set by the loaders)
"""
from __future__ import annotations

import re
from decimal import Decimal

from . import config as C
from .timeutil import parse_ts

CUSTOMER_FIELDS = ["customer_id", "name", "email", "country", "segment", "updated_at"]
ORDER_REQUIRED = ["order_id", "line_no", "customer_id", "product_code", "product_name",
                  "quantity", "unit_price", "currency", "order_ts", "status", "status_ts"]
ORDER_OPTIONAL = ["category"]

_CUSTOMER_ID = re.compile(r"^C\d{6}$")
_ORDER_ID = re.compile(r"^ORD-\d{8}$")
_PRODUCT_CODE = re.compile(r"^P\d{4}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INT = re.compile(r"^-?\d+$")
_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")

UNCATEGORISED = "UNCATEGORISED"


class Reject(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _required(rec: dict, field: str) -> str:
    v = rec.get(field)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise Reject("MISSING_REQUIRED_FIELD", f"{field} is missing or empty")
    if not isinstance(v, str):
        raise Reject("INVALID_FORMAT", f"{field} must be a string, got {type(v).__name__}")
    return v.strip()


def _enum(field: str, value: str, allowed) -> str:
    v = value.upper()
    if v not in allowed:
        raise Reject("INVALID_ENUM", f"{field}={value!r} not in {list(allowed)}")
    return v


def _ts(field: str, value: str) -> str:
    try:
        return parse_ts(value)
    except ValueError as exc:
        raise Reject("INVALID_TIMESTAMP", f"{field}: {exc}")


def validate_customer(rec: dict) -> dict:
    vals = {f: _required(rec, f) for f in CUSTOMER_FIELDS}
    if not _CUSTOMER_ID.match(vals["customer_id"]):
        raise Reject("INVALID_FORMAT", f"customer_id={vals['customer_id']!r} must match C######")
    if not _EMAIL.match(vals["email"]):
        raise Reject("INVALID_FORMAT", f"email={vals['email']!r} is not a valid address")
    return {
        "customer_id": vals["customer_id"],
        "name": vals["name"],
        "email": vals["email"].lower(),
        "country": _enum("country", vals["country"], C.ALLOWED_COUNTRIES),
        "segment": _enum("segment", vals["segment"], C.ALLOWED_SEGMENTS),
        "updated_at": _ts("updated_at", vals["updated_at"]),
    }


def validate_order_row(rec: dict, latest_allowed_ts: str) -> dict:
    """`latest_allowed_ts` = as_of + skew, normalised ISO string; later timestamps are rejected."""
    v = {f: _required(rec, f) for f in ORDER_REQUIRED}
    category = rec.get("category")
    category = category.strip() if isinstance(category, str) and category.strip() else UNCATEGORISED

    if not _ORDER_ID.match(v["order_id"]):
        raise Reject("INVALID_FORMAT", f"order_id={v['order_id']!r} must match ORD-########")
    if not _CUSTOMER_ID.match(v["customer_id"]):
        raise Reject("INVALID_FORMAT", f"customer_id={v['customer_id']!r} must match C######")
    if not _PRODUCT_CODE.match(v["product_code"]):
        raise Reject("INVALID_FORMAT", f"product_code={v['product_code']!r} must match P####")

    if not _INT.match(v["line_no"]):
        raise Reject("INVALID_FORMAT", f"line_no={v['line_no']!r} is not an integer")
    line_no = int(v["line_no"])
    if not 1 <= line_no <= C.MAX_LINE_NO:
        raise Reject("OUT_OF_RANGE", f"line_no={line_no} outside 1..{C.MAX_LINE_NO}")

    if not _INT.match(v["quantity"]):
        raise Reject("INVALID_FORMAT", f"quantity={v['quantity']!r} is not an integer")
    quantity = int(v["quantity"])
    if not 1 <= quantity <= C.MAX_QUANTITY:
        raise Reject("OUT_OF_RANGE", f"quantity={quantity} outside 1..{C.MAX_QUANTITY}")

    if not _DECIMAL.match(v["unit_price"]):
        raise Reject("INVALID_FORMAT", f"unit_price={v['unit_price']!r} is not a decimal number")
    price = Decimal(v["unit_price"])
    if price != price.quantize(Decimal("0.01")):
        raise Reject("INVALID_FORMAT", f"unit_price={v['unit_price']!r} has more than 2 decimals")
    price_minor = int(price * 100)
    if price_minor < 0:
        raise Reject("OUT_OF_RANGE", f"unit_price={v['unit_price']} is negative")
    if price_minor > C.MAX_UNIT_PRICE_MINOR:
        raise Reject("OUT_OF_RANGE", f"unit_price={v['unit_price']} exceeds 1000000.00")

    currency = _enum("currency", v["currency"], C.ALLOWED_CURRENCIES)
    status = _enum("status", v["status"], C.ALLOWED_STATUSES)

    order_ts = _ts("order_ts", v["order_ts"])
    status_ts = _ts("status_ts", v["status_ts"])
    if order_ts > latest_allowed_ts or status_ts > latest_allowed_ts:
        raise Reject("FUTURE_TIMESTAMP", f"order_ts={order_ts} / status_ts={status_ts} is after "
                                         f"{latest_allowed_ts}")
    if status_ts < order_ts:
        raise Reject("STATUS_BEFORE_ORDER", f"status_ts={status_ts} precedes order_ts={order_ts}")

    return {
        "order_id": v["order_id"], "line_no": line_no, "status_ts": status_ts,
        "customer_id": v["customer_id"], "product_code": v["product_code"],
        "product_name": v["product_name"], "category": category,
        "quantity": quantity, "unit_price_minor": price_minor, "currency": currency,
        "line_amount_minor": quantity * price_minor,  # derived field
        "order_ts": order_ts, "status": status,
    }
