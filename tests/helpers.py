"""Shared test helpers: tiny file builders + a base TestCase that runs the real pipeline."""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from orderflow.config import Config
from orderflow.generate_data import ORDER_COLUMNS
from orderflow.pipeline import run_pipeline

AS_OF = "2026-11-06T00:00:00Z"
HEADER = ",".join(ORDER_COLUMNS)
logging.getLogger("orderflow").setLevel(logging.CRITICAL)


def order_row(order_id="ORD-00000001", line_no=1, customer_id="C000001", product_code="P0001",
              product_name="Widget", category="Tools", quantity=2, unit_price="10.50", currency="INR",
              order_ts="2026-02-01T10:00:00Z", status="PLACED", status_ts=None) -> str:
    status_ts = status_ts or order_ts
    vals = [order_id, line_no, customer_id, product_code, product_name, category, quantity,
            unit_price, currency, order_ts, status, status_ts]
    return ",".join(str(v) for v in vals)


def customer(customer_id="C000001", name="Asha", email="asha@example.com", country="IN",
             segment="SMB", updated_at="2026-01-01T00:00:00Z") -> dict:
    return {"customer_id": customer_id, "name": name, "email": email, "country": country,
            "segment": segment, "updated_at": updated_at}


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orderflow_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = self.tmp / "wh.db"
        self._n = 0

    # ---- file builders
    def orders_file(self, rows, header=HEADER, name=None) -> Path:
        self._n += 1
        p = self.tmp / (name or f"orders_{self._n}.csv")
        p.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
        return p

    def customers_file(self, records, name=None) -> Path:
        self._n += 1
        p = self.tmp / (name or f"customers_{self._n}.jsonl")
        lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
        p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return p

    # ---- run + inspect
    def run_pipe(self, customers=None, orders=None, **overrides) -> dict:
        kw = dict(db_path=self.db, output_dir=self.tmp / "out", customers_file=customers,
                  orders_file=orders, as_of=AS_OF)
        kw.update(overrides)
        return run_pipeline(Config(**kw))

    def q(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def scalar(self, sql, params=()):
        return self.q(sql, params)[0][0]

    @staticmethod
    def src(summary, name) -> dict:
        return next(s for s in summary["sources"] if s["source_name"] == name)

    def assertReconciled(self, summary):
        bad = [c for c in summary["reconciliation"] if not c["passed"]]
        self.assertEqual(bad, [], f"failed reconciliation checks: {bad}")

    def load_base_customers(self, *extra):
        """C000001 (SMB since Jan 1) loaded and ready for order tests."""
        s = self.run_pipe(customers=self.customers_file([customer(), *extra], name="base_customers.jsonl"))
        self.assertEqual(s["status"], "SUCCESS")
        return s
