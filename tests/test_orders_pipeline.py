"""End-to-end tests of the orders source through the real pipeline + SQLite."""
import unittest

from tests.helpers import HEADER, PipelineTestCase, customer, order_row


class OrdersHappyPath(PipelineTestCase):
    def test_happy_path_loads_events_fact_and_dimensions(self):
        self.load_base_customers()
        f = self.orders_file([
            order_row("ORD-00000001", 1, quantity=2, unit_price="10.50", status="PLACED", order_ts="2026-02-01T10:00:00Z"),
            order_row("ORD-00000001", 1, quantity=2, unit_price="10.50", status="SHIPPED",
                      order_ts="2026-02-01T10:00:00Z", status_ts="2026-02-02T10:00:00Z"),
            order_row("ORD-00000001", 2, product_code="P0002", product_name="Gadget", quantity=1, unit_price="99.99"),
        ])
        s = self.run_pipe(orders=f)
        self.assertEqual(s["status"], "SUCCESS")
        self.assertReconciled(s)
        o = self.src(s, "orders")
        self.assertEqual((o["rows_read"], o["rows_accepted"], o["rows_rejected"], o["rows_inserted_new"]), (3, 3, 0, 3))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_line_event"), 3)       # history kept
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), 2)        # grain = order line
        self.assertEqual(self.scalar("SELECT status FROM fact_order_line WHERE line_no=1"), "SHIPPED")
        self.assertEqual(self.scalar("SELECT line_amount_minor FROM fact_order_line WHERE line_no=1"), 2100)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM dim_product"), 2)
        self.assertEqual(self.scalar("SELECT full_date FROM dim_date"), "2026-02-01")

    def test_run_metrics_are_recorded(self):
        self.load_base_customers()
        s = self.run_pipe(orders=self.orders_file([order_row(), order_row("ORD-00000003"),
                                                   order_row("ORD-00000004"), order_row(quantity="0", order_id="ORD-00000002")]))
        row = self.q("SELECT status, started_at, ended_at, duration_seconds, reconciliation_passed FROM pipeline_run WHERE run_id=?", (s["run_id"],))[0]
        self.assertEqual(row[0], "SUCCESS")
        self.assertTrue(row[1] and row[2])
        self.assertEqual(row[4], 1)
        rs = self.q("SELECT rows_read, rows_accepted, rows_rejected, source_file, file_sha256 FROM run_source "
                    "WHERE run_id=? AND source_name='orders'", (s["run_id"],))[0]
        self.assertEqual(rs[:3], (4, 3, 1))
        self.assertTrue(rs[3].endswith(".csv") and len(rs[4]) == 64)


class OrdersEmptyAndMalformed(PipelineTestCase):
    def test_empty_batch_header_only(self):
        self.load_base_customers()
        s = self.run_pipe(orders=self.orders_file([]))
        o = self.src(s, "orders")
        self.assertEqual((s["status"], o["status"], o["rows_read"]), ("SUCCESS", "SUCCESS", 0))
        self.assertTrue(any("EMPTY_INPUT" in w for w in o["warnings"]))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), 0)
        self.assertReconciled(s)

    def test_empty_batch_zero_byte_file(self):
        self.load_base_customers()
        p = self.tmp / "zero.csv"
        p.write_bytes(b"")
        s = self.run_pipe(orders=p)
        self.assertEqual(s["status"], "SUCCESS")
        self.assertEqual(self.src(s, "orders")["rows_read"], 0)

    def test_malformed_rows_are_quarantined_with_line_numbers(self):
        self.load_base_customers()
        f = self.orders_file([
            order_row("ORD-00000001"),
            "ORD-00000002,1,C000001,P0001",                       # too few columns
            order_row("ORD-00000003") + ",unexpected,extra",      # too many columns
            order_row("ORD-00000004"),
        ])
        s = self.run_pipe(orders=f, max_reject_ratio=0.9)
        self.assertEqual(self.q("SELECT line_number, reason_code FROM reject_record WHERE run_id=? ORDER BY line_number", (s["run_id"],)),
                         [(3, "MALFORMED_RECORD"), (4, "MALFORMED_RECORD")])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), 2)
        raw = self.scalar("SELECT raw_record FROM reject_record WHERE line_number=3")
        self.assertIn("ORD-00000002", raw)                      # raw content preserved for replay
        self.assertReconciled(s)

    def test_null_required_fields_rejected_null_category_accepted(self):
        self.load_base_customers()
        f = self.orders_file([
            order_row("ORD-00000001", category=""),
            order_row("ORD-00000002", customer_id=""),
            order_row("ORD-00000003", unit_price=""),
            order_row("ORD-00000004", product_name=""),
        ])
        s = self.run_pipe(orders=f, max_reject_ratio=0.9)
        self.assertEqual(self.src(s, "orders")["reject_reasons"], {"MISSING_REQUIRED_FIELD": 3})
        self.assertEqual(self.scalar("SELECT category FROM dim_product"), "UNCATEGORISED")


class OrdersDuplicates(PipelineTestCase):
    def test_exact_and_conflicting_duplicates_first_wins(self):
        self.load_base_customers()
        f = self.orders_file([
            order_row("ORD-00000001", quantity=2),
            order_row("ORD-00000001", quantity=2),                # exact duplicate
            order_row("ORD-00000001", quantity=9),                # same key, different payload
            order_row("ORD-00000002"),
        ])
        s = self.run_pipe(orders=f, max_reject_ratio=0.9)
        self.assertEqual(self.src(s, "orders")["reject_reasons"], {"DUPLICATE_EVENT": 1, "CONFLICTING_DUPLICATE": 1})
        self.assertEqual(self.scalar("SELECT quantity FROM fact_order_line WHERE order_id='ORD-00000001'"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), 2)
        self.assertReconciled(s)


class OrdersSchemaAndReferences(PipelineTestCase):
    def test_missing_required_column_fails_source_and_loads_nothing(self):
        self.load_base_customers()
        f = self.orders_file([order_row()], header=HEADER.replace("unit_price", "price"))
        s = self.run_pipe(orders=f)
        o = self.src(s, "orders")
        self.assertEqual((s["status"], o["status"]), ("FAILED", "FAILED"))
        self.assertIn("SCHEMA_ERROR", o["failure_reason"])
        self.assertIn("unit_price", o["failure_reason"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_line_event"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ingested_file WHERE source_name='orders'"), 0)
        self.assertEqual(self.scalar("SELECT status FROM pipeline_run WHERE run_id=?", (s["run_id"],)), "FAILED")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM run_source WHERE run_id=? AND status='FAILED'", (s["run_id"],)), 1)

    def test_extra_column_is_tolerated_with_a_warning(self):
        self.load_base_customers()
        f = self.orders_file([order_row() + ",SAVE10"], header=HEADER + ",coupon_code")
        s = self.run_pipe(orders=f)
        o = self.src(s, "orders")
        self.assertEqual(o["status"], "SUCCESS")
        self.assertTrue(any("UNEXPECTED_COLUMNS_IGNORED" in w and "coupon_code" in w for w in o["warnings"]))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), 1)

    def test_reordered_columns_still_load(self):
        self.load_base_customers()
        cols = HEADER.split(",")
        vals = order_row().split(",")
        idx = list(reversed(range(len(cols))))
        f = self.orders_file([",".join(vals[i] for i in idx)], header=",".join(cols[i] for i in idx))
        self.assertEqual(self.src(self.run_pipe(orders=f), "orders")["rows_accepted"], 1)

    def test_invalid_reference_key_is_rejected_not_loaded(self):
        self.load_base_customers()
        s = self.run_pipe(orders=self.orders_file([order_row("ORD-00000001"), order_row("ORD-00000003"),
                                                   order_row("ORD-00000002", customer_id="C999999")]),
                       max_reject_ratio=0.5)
        self.assertEqual(self.q("SELECT reason_code, reason_detail FROM reject_record")[0][0], "INVALID_CUSTOMER_REF")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line f LEFT JOIN dim_customer d USING(customer_key) WHERE d.customer_key IS NULL"), 0)

    def test_missing_input_file_fails_cleanly(self):
        s = self.run_pipe(orders=self.tmp / "does_not_exist.csv")
        self.assertEqual(s["status"], "FAILED")
        self.assertIn("FileNotFoundError", self.src(s, "orders")["failure_reason"])

    def test_no_inputs_configured(self):
        s = self.run_pipe()
        self.assertEqual(s["status"], "FAILED")
        self.assertIn("no input files", s["failure_reason"])

    def test_reject_ratio_gate_blocks_load_but_keeps_quarantine(self):
        self.load_base_customers()
        f = self.orders_file([order_row("ORD-00000001"),
                              order_row("ORD-00000002", quantity="0"),
                              order_row("ORD-00000003", quantity="0"),
                              order_row("ORD-00000004", quantity="0")])
        s = self.run_pipe(orders=f, max_reject_ratio=0.5)
        self.assertEqual(s["status"], "FAILED")
        self.assertIn("REJECT_RATIO_EXCEEDED", self.src(s, "orders")["failure_reason"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_line_event"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM reject_record WHERE source_name='orders'"), 3)


class OrdersDomainEdgeCases(PipelineTestCase):
    def test_domain_edge_cases(self):
        self.load_base_customers()
        f = self.orders_file([
            order_row("ORD-00000001", unit_price="0.00"),                                   # free item: valid
            order_row("ORD-00000002", quantity=10000),                                      # max qty: valid
            order_row("ORD-00000003", quantity=10001),                                      # over max
            order_row("ORD-00000004", status="SHIPPED", status_ts="2026-01-31T00:00:00Z"),  # status before order
            order_row("ORD-00000005", status_ts="2099-01-01T00:00:00Z"),                    # future
            order_row("ORD-00000006", order_ts="2026-03-01T05:30:00+05:30"),                # offset -> UTC
            order_row("ORD-00000007", status="CANCELLED", order_ts="2026-02-01T10:00:00Z", status_ts="2026-02-01T12:00:00Z"),
        ])
        s = self.run_pipe(orders=f, max_reject_ratio=0.9)
        rej = dict(self.q("SELECT substr(raw_record, 3, 12), reason_code FROM reject_record"))
        self.assertEqual(rej, {"ORD-00000003": "OUT_OF_RANGE", "ORD-00000004": "STATUS_BEFORE_ORDER",
                               "ORD-00000005": "FUTURE_TIMESTAMP"})
        self.assertEqual(self.scalar("SELECT line_amount_minor FROM fact_order_line WHERE order_id='ORD-00000001'"), 0)
        self.assertEqual(self.scalar("SELECT line_amount_minor FROM fact_order_line WHERE order_id='ORD-00000002'"), 10000 * 1050)
        self.assertEqual(self.scalar("SELECT order_ts FROM fact_order_line WHERE order_id='ORD-00000006'"), "2026-03-01T00:00:00Z")
        # cancelled lines stay in the fact (with history) but are excluded from net revenue
        self.assertEqual(self.scalar("SELECT is_net_revenue FROM fact_order_line WHERE order_id='ORD-00000007'"), 0)
        self.assertEqual(self.scalar("SELECT SUM(is_net_revenue) FROM fact_order_line"), 3)
        self.assertReconciled(s)

    def test_out_of_order_events_within_one_file(self):
        self.load_base_customers()
        f = self.orders_file([
            order_row(status="DELIVERED", status_ts="2026-02-05T00:00:00Z"),   # newest first
            order_row(status="PLACED"),
            order_row(status="SHIPPED", status_ts="2026-02-02T00:00:00Z"),
        ])
        s = self.run_pipe(orders=f)
        self.assertEqual(self.scalar("SELECT status FROM fact_order_line"), "DELIVERED")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_line_event"), 3)
        self.assertReconciled(s)

    def test_late_arriving_older_event_in_a_later_batch_keeps_history_not_state(self):
        self.load_base_customers()
        self.run_pipe(orders=self.orders_file([order_row(status="DELIVERED", status_ts="2026-02-05T00:00:00Z")]))
        late = self.orders_file([order_row(status="SHIPPED", status_ts="2026-02-02T00:00:00Z")])
        s = self.run_pipe(orders=late)
        self.assertEqual(self.src(s, "orders")["rows_inserted_new"], 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_line_event"), 2)           # history gained a row
        self.assertEqual(self.scalar("SELECT status FROM fact_order_line"), "DELIVERED")    # state did not regress
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), 1)
        self.assertReconciled(s)

    def test_product_dimension_latest_event_wins_even_when_older_one_arrives_later(self):
        self.load_base_customers()
        self.run_pipe(orders=self.orders_file([order_row(product_name="Widget v2", status="SHIPPED", status_ts="2026-02-05T00:00:00Z")]))
        self.run_pipe(orders=self.orders_file([order_row(product_name="Widget v1", status="PLACED")]))   # older
        self.assertEqual(self.scalar("SELECT product_name FROM dim_product"), "Widget v2")


if __name__ == "__main__":
    unittest.main()
