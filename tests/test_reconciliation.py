"""The reconciliation layer must actually catch problems (we corrupt the warehouse on purpose)."""
import sqlite3
import unittest

from orderflow.db import connect
from orderflow.reconcile import check_db_invariants
from tests.helpers import PipelineTestCase, customer, order_row


class Reconciliation(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.run_pipe(customers=self.customers_file([customer(segment="RETAIL"), customer(segment="SMB", updated_at="2026-03-01T00:00:00Z")]))
        self.run_pipe(orders=self.orders_file([order_row("ORD-00000001"), order_row("ORD-00000002", order_ts="2026-04-01T00:00:00Z")]))

    def failed(self):
        conn = connect(self.db)
        try:
            return {c["name"] for c in check_db_invariants(conn) if not c["passed"]}
        finally:
            conn.close()

    def exec_(self, sql):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(sql)
        conn.commit()
        conn.close()

    def test_clean_warehouse_passes_every_invariant(self):
        self.assertEqual(self.failed(), set())

    def test_detects_missing_fact_row(self):
        self.exec_("DELETE FROM fact_order_line WHERE order_id='ORD-00000001'")
        self.assertIn("fact.rows = distinct order lines in event history", self.failed())

    def test_detects_wrong_scd2_version_on_fact(self):
        self.exec_("UPDATE fact_order_line SET customer_key=(SELECT customer_key FROM dim_customer WHERE segment='SMB') WHERE order_id='ORD-00000001'")
        self.assertIn("fact.customer_key is the version valid at order_ts", self.failed())

    def test_detects_orphan_keys(self):
        self.exec_("UPDATE fact_order_line SET product_key='pk_missing' WHERE order_id='ORD-00000001'")
        self.assertIn("fact.no_orphan_product_key", self.failed())

    def test_detects_state_drift_from_latest_event(self):
        self.exec_("UPDATE fact_order_line SET status='DELIVERED' WHERE order_id='ORD-00000001'")
        self.assertIn("fact.state = latest event (status mismatches)", self.failed())

    def test_detects_broken_derived_amount(self):
        self.exec_("UPDATE fact_order_line SET line_amount_minor=1 WHERE order_id='ORD-00000001'")
        self.assertIn("fact.sum(line_amount) = sum(quantity*unit_price)", self.failed())

    def test_detects_scd2_chain_problems(self):
        self.exec_("UPDATE dim_customer SET valid_to='2026-02-01T00:00:00Z' WHERE is_current=0")   # creates a gap
        self.assertIn("dim_customer.chain_contiguous", self.failed())

    def test_results_are_persisted_for_audit(self):
        n = self.scalar("SELECT COUNT(*) FROM reconciliation_result WHERE passed=1")
        self.assertGreater(n, 10)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM reconciliation_result WHERE passed=0"), 0)


if __name__ == "__main__":
    unittest.main()
