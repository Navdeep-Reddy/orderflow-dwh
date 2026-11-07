"""Deterministic identifiers. Same business key in -> same ID out, on every machine/run."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def _digest(*parts: object, length: int = 12) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:length]


def customer_key(customer_id: str, valid_from: str) -> str:
    """Surrogate key of one SCD2 version of a customer."""
    return "ck_" + _digest(customer_id, valid_from)


def product_key(product_code: str) -> str:
    return "pk_" + _digest(product_code)


def order_line_key(order_id: str, line_no: int) -> str:
    return "ol_" + _digest(order_id, line_no, length=16)


def date_key(iso_ts: str) -> int:
    """'2026-03-05T10:00:00Z' -> 20260305"""
    return int(iso_ts[0:4] + iso_ts[5:7] + iso_ts[8:10])


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_run_id(now: datetime | None = None, fingerprint: str = "") -> str:
    """run_<UTC timestamp>_<6 hex of input fingerprint>; sortable and traceable to inputs."""
    now = now or datetime.now(timezone.utc)
    return f"run_{now.strftime('%Y%m%dT%H%M%S%fZ')}_{_digest(fingerprint, length=6)}"
