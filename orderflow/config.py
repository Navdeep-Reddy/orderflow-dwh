"""Pipeline configuration. Everything tunable lives here; nothing is hard-coded in modules."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ---- Domain constants (documented in docs/DATA_DICTIONARY.md) ---------------------------
ALLOWED_STATUSES = ("PLACED", "SHIPPED", "DELIVERED", "CANCELLED", "RETURNED")
NON_REVENUE_STATUSES = ("CANCELLED", "RETURNED")
ALLOWED_CURRENCIES = ("INR", "USD", "EUR")
ALLOWED_SEGMENTS = ("RETAIL", "SMB", "ENTERPRISE")
ALLOWED_COUNTRIES = ("IN", "US", "GB", "DE", "SG", "AE", "FR", "AU")
MAX_QUANTITY = 10_000
MAX_LINE_NO = 999
MAX_UNIT_PRICE_MINOR = 100_000_000  # 1,000,000.00 in minor units (boundary value)
HIGH_DATE = "9999-12-31T23:59:59Z"  # valid_to of the current SCD2 version


@dataclass(frozen=True)
class Config:
    db_path: Path = Path("output/warehouse.db")
    output_dir: Path = Path("output")
    customers_file: Optional[Path] = None
    orders_file: Optional[Path] = None
    # Data-quality gate: if more than this share of a source's rows is rejected, the
    # source is NOT loaded (rejects are still persisted for diagnosis).
    max_reject_ratio: float = 0.30
    # Timestamps later than as_of + skew are rejected as FUTURE_TIMESTAMP.
    as_of: Optional[str] = None  # ISO-8601; default = now (UTC). Pin it for reproducible runs.
    max_future_skew_hours: int = 24
    batch_size: int = 5_000
    # Re-load a file even if the same content (sha256) was already loaded.
    force: bool = False
    run_id: Optional[str] = None
    # Test hook: raise after this many event rows were written to simulate a crash mid-load.
    fail_after_rows: Optional[int] = None
