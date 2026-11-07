"""Reruns, retries after partial failure, restart behaviour, backfill."""
import unittest

from tests.helpers import PipelineTestCase, customer, order_row

COUNTS = {"events": "SELECT COUNT(*) FROM order_line_event", "fact": "SELECT COUNT(*) FROM fact_order_line",
          "cust_events": "SELECT COUNT(*) FROM stg_customer_event", "dim_c": "SELECT COUNT(*) FROM dim_customer",
          "prod": "SELECT COUNT(*) FROM dim_product", "date": "SELECT COUNT(*) FROM dim_date"}


class Idempotency(PipelineTestCase):
    def snapshot(self):
        return {k: self.scalar(v) for k, v in COUNTS.items()}

    def test_rerun_of_same_input_is_skipped_and_changes_nothing(self):
        c = self.customers_file([customer(), customer("C000002", name="Bob")])
        o = self.orders_file([order_row("ORD-00000001"), order_row("ORD-00000002", customer_id="C000002")])
        self.run_pipe(customers=c, orders=o)
        before = self.snapshot()
        s2 = self.run_pipe(customers=c, orders=o)
        self.assertEqual(s2["status"], "SUCCESS")
        self.assertEqual({x["source_name"]: x["status"] for x in s2["sources"]},
                         {"customers": "SKIPPED_ALREADY_LOADED", "orders": "SKIPPED_ALREADY_LOADED"})
        self.assertEqual(self.snapshot(), before)

    def test_same_content_under_a_different_filename_is_still_skipped(self):
        c = self.customers_file([customer()], name="a.jsonl")
        self.run_pipe(customers=c)
        copy = self.tmp / "b.jsonl"
        copy.write_bytes(c.read_bytes())
        self.assertEqual(self.run_pipe(customers=copy)["sources"][0]["status"], "SKIPPED_ALREADY_LOADED")

    def test_forced_reload_inserts_no_duplicates(self):
        c = self.customers_file([customer()])
        o = self.orders_file([order_row("ORD-00000001"), order_row("ORD-00000001", status="SHIPPED", status_ts="2026-02-02T00:00:00Z")])
        self.run_pipe(customers=c, orders=o)
        before = self.snapshot()
        s2 = self.run_pipe(customers=c, orders=o, force=True)
        self.assertEqual(s2["status"], "SUCCESS")
        self.assertReconciled(s2)
        so = self.src(s2, "orders")
        self.assertEqual((so["rows_inserted_new"], so["rows_already_present"]), (0, 2))
        self.assertEqual(self.snapshot(), before)

    def test_overlapping_files_only_add_the_new_rows(self):
        self.load_base_customers()
        self.run_pipe(orders=self.orders_file([order_row("ORD-00000001"), order_row("ORD-00000002")]))
        s = self.run_pipe(orders=self.orders_file([order_row("ORD-00000002"), order_row("ORD-00000003")]))
        o = self.src(s, "orders")
        self.assertEqual((o["rows_inserted_new"], o["rows_already_present"]), (1, 1))
        self.assertEqual(self.scalar(COUNTS["fact"]), 3)
        self.assertReconciled(s)

    def test_conflicting_resend_across_batches_is_detected_by_reconciliation(self):
        """Same event key, different payload in a LATER file: first write wins, and the
        source->target payload check flags it instead of hiding it."""
        self.load_base_customers()
        self.run_pipe(orders=self.orders_file([order_row(quantity=2)]))
        s = self.run_pipe(orders=self.orders_file([order_row(quantity=5)]))
        self.assertFalse(s["reconciliation_passed"])
        failed = [c["name"] for c in s["reconciliation"] if not c["passed"]]
        self.assertIn("orders.accepted_keys_present_in_target_with_same_payload", failed)
        self.assertEqual(self.scalar("SELECT quantity FROM fact_order_line"), 2)   # state untouched


class PartialFailureAndRetry(PipelineTestCase):
    def test_crash_mid_load_rolls_back_everything_and_retry_succeeds(self):
        self.load_base_customers()
        rows = [order_row(f"ORD-0000000{i}") for i in range(1, 6)]
        o = self.orders_file(rows)
        bad = self.run_pipe(orders=o, batch_size=2, fail_after_rows=2)        # crashes after the first chunk
        so = self.src(bad, "orders")
        self.assertEqual((bad["status"], so["status"]), ("FAILED", "FAILED"))
        self.assertIn("InjectedFailure", so["failure_reason"])
        for table in ("order_line_event", "fact_order_line", "dim_product", "dim_date"):
            self.assertEqual(self.scalar(f"SELECT COUNT(*) FROM {table}"), 0, f"{table} must be rolled back")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ingested_file WHERE source_name='orders'"), 0)
        self.assertEqual(self.scalar("SELECT status FROM pipeline_run WHERE run_id=?", (bad["run_id"],)), "FAILED")
        self.assertReconciled(bad)                                             # "failed source loaded nothing"

        good = self.run_pipe(orders=o)                                         # plain retry, no cleanup needed
        self.assertEqual(good["status"], "SUCCESS")
        self.assertEqual(self.src(good, "orders")["rows_inserted_new"], 5)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), 5)
        self.assertReconciled(good)

    def test_one_source_failing_does_not_undo_the_other(self):
        c = self.customers_file([customer()])
        o = self.orders_file([order_row()], header="order_id,line_no")        # contract broken
        s = self.run_pipe(customers=c, orders=o)
        self.assertEqual(s["status"], "PARTIAL_FAILURE")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM dim_customer"), 1)
        # restart with a fixed orders file: customers are skipped, orders load
        s2 = self.run_pipe(customers=c, orders=self.orders_file([order_row()]))
        self.assertEqual({x["source_name"]: x["status"] for x in s2["sources"]},
                         {"customers": "SKIPPED_ALREADY_LOADED", "orders": "SUCCESS"})

    def test_backfill_of_historical_file_after_newer_data(self):
        self.load_base_customers()
        self.run_pipe(orders=self.orders_file([order_row("ORD-00000009", order_ts="2026-06-01T00:00:00Z")]))
        s = self.run_pipe(orders=self.orders_file([order_row("ORD-00000001", order_ts="2026-01-05T00:00:00Z")]))
        self.assertEqual(self.scalar(COUNTS["fact"]), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM dim_date"), 2)
        self.assertReconciled(s)


if __name__ == "__main__":
    unittest.main()
