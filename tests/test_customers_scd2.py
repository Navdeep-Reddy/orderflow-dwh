"""Customer source + SCD Type 2 behaviour (history, late-arriving changes, point-in-time joins)."""
import unittest

from tests.helpers import PipelineTestCase, customer, order_row

V = "SELECT segment, valid_from, valid_to, is_current FROM dim_customer WHERE customer_id='C000001' ORDER BY valid_from"
SEG_AT_ORDER = ("SELECT d.segment FROM fact_order_line f JOIN dim_customer d USING (customer_key) "
                "WHERE f.order_id=?")


class CustomerSource(PipelineTestCase):
    def test_happy_path_and_scd2_versions(self):
        f = self.customers_file([
            customer(segment="RETAIL", updated_at="2026-01-01T00:00:00Z"),
            customer(segment="SMB", updated_at="2026-03-01T00:00:00Z"),
            customer("C000002", name="Bob"),
        ])
        s = self.run_pipe(customers=f)
        self.assertEqual(s["status"], "SUCCESS")
        self.assertReconciled(s)
        self.assertEqual(self.q(V), [("RETAIL", "2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z", 0),
                                     ("SMB", "2026-03-01T00:00:00Z", "9999-12-31T23:59:59Z", 1)])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM stg_customer_event"), 3)

    def test_out_of_order_events_give_same_result_as_in_order(self):
        a = [customer(segment="RETAIL", updated_at="2026-01-01T00:00:00Z"), customer(segment="SMB", updated_at="2026-03-01T00:00:00Z")]
        self.run_pipe(customers=self.customers_file(list(reversed(a))))
        self.assertEqual([r[0] for r in self.q(V)], ["RETAIL", "SMB"])

    def test_noop_update_does_not_create_a_version(self):
        f = self.customers_file([customer(updated_at="2026-01-01T00:00:00Z"), customer(updated_at="2026-02-01T00:00:00Z")])
        self.run_pipe(customers=f)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM dim_customer"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM stg_customer_event"), 2)   # but the event is kept

    def test_duplicates_malformed_and_invalid_records(self):
        f = self.customers_file([
            customer(),
            customer(),                                             # exact duplicate
            customer(segment="ENTERPRISE"),                         # same key, different payload
            '{"customer_id": "C000009", "name": "trunc',           # malformed JSON
            "[1, 2, 3]",                                            # JSON but not an object
            customer("C000003", segment="GOLD"),                    # invalid enum
            customer("C000004", email=""),                          # missing field
            customer("C000005"),
            customer("C000006"),
            customer("C000007"),
            customer("C000008"),
            customer("C000010"),
        ])
        s = self.run_pipe(customers=f, max_reject_ratio=0.6)
        c = self.src(s, "customers")
        self.assertEqual(c["reject_reasons"], {"DUPLICATE_EVENT": 1, "CONFLICTING_DUPLICATE": 1, "MALFORMED_RECORD": 2,
                                               "INVALID_ENUM": 1, "MISSING_REQUIRED_FIELD": 1})
        self.assertEqual(self.scalar("SELECT COUNT(DISTINCT customer_id) FROM dim_customer"), 6)
        self.assertEqual(self.q("SELECT line_number FROM reject_record WHERE reason_code='MALFORMED_RECORD' ORDER BY 1"), [(4,), (5,)])
        self.assertReconciled(s)

    def test_empty_batch(self):
        s = self.run_pipe(customers=self.customers_file([]))
        self.assertEqual(s["status"], "SUCCESS")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM dim_customer"), 0)
        self.assertTrue(any("EMPTY_INPUT" in w for w in self.src(s, "customers")["warnings"]))

    def test_unexpected_extra_field_is_tolerated_and_reported(self):
        s = self.run_pipe(customers=self.customers_file([{**customer(), "loyalty_tier": "gold"}]))
        c = self.src(s, "customers")
        self.assertEqual(c["status"], "SUCCESS")
        self.assertTrue(any("UNEXPECTED_FIELDS_IGNORED" in w and "loyalty_tier" in w for w in c["warnings"]))

    def test_schema_drift_in_every_record_trips_the_quality_gate(self):
        recs = [{k: v for k, v in customer(f"C00000{i}").items() if k != "segment"} for i in range(1, 6)]
        s = self.run_pipe(customers=self.customers_file(recs))
        self.assertEqual(s["status"], "FAILED")
        self.assertIn("REJECT_RATIO_EXCEEDED", self.src(s, "customers")["failure_reason"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM dim_customer"), 0)


class PointInTimeAndLateHistory(PipelineTestCase):
    def test_order_resolves_to_the_customer_version_valid_at_order_time(self):
        self.run_pipe(customers=self.customers_file([
            customer(segment="RETAIL", updated_at="2026-01-01T00:00:00Z"),
            customer(segment="SMB", updated_at="2026-03-01T00:00:00Z")]))
        s = self.run_pipe(orders=self.orders_file([
            order_row("ORD-00000001", order_ts="2026-02-15T00:00:00Z"),        # before the change
            order_row("ORD-00000002", order_ts="2026-03-01T00:00:00Z"),        # exactly at valid_from (inclusive)
            order_row("ORD-00000003", order_ts="2026-04-01T00:00:00Z")]))      # after
        self.assertEqual(self.scalar(SEG_AT_ORDER, ("ORD-00000001",)), "RETAIL")
        self.assertEqual(self.scalar(SEG_AT_ORDER, ("ORD-00000002",)), "SMB")
        self.assertEqual(self.scalar(SEG_AT_ORDER, ("ORD-00000003",)), "SMB")
        self.assertReconciled(s)

    def test_order_older_than_first_known_version_maps_to_earliest_version(self):
        self.run_pipe(customers=self.customers_file([customer(updated_at="2026-03-01T00:00:00Z")]))
        s = self.run_pipe(orders=self.orders_file([order_row(order_ts="2026-02-01T00:00:00Z")]))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), 1)
        self.assertReconciled(s)

    def test_late_arriving_customer_history_repoints_existing_facts(self):
        self.run_pipe(customers=self.customers_file([customer(segment="SMB", updated_at="2026-03-01T00:00:00Z")]))
        self.run_pipe(orders=self.orders_file([order_row(order_ts="2026-02-01T00:00:00Z")]))
        self.assertEqual(self.scalar(SEG_AT_ORDER, ("ORD-00000001",)), "SMB")      # best known at the time
        late = self.customers_file([customer(segment="RETAIL", updated_at="2026-01-01T00:00:00Z")])
        s = self.run_pipe(customers=late)
        self.assertEqual(self.scalar(SEG_AT_ORDER, ("ORD-00000001",)), "RETAIL")   # corrected retroactively
        self.assertEqual(len(self.q(V)), 2)
        self.assertReconciled(s)

    def test_late_event_that_merges_versions_removes_orphan_without_breaking_references(self):
        self.run_pipe(customers=self.customers_file([customer(segment="SMB", updated_at="2026-03-01T00:00:00Z")]))
        self.run_pipe(orders=self.orders_file([order_row(order_ts="2026-04-01T00:00:00Z")]))
        old_key = self.scalar("SELECT customer_key FROM dim_customer")
        s = self.run_pipe(customers=self.customers_file([customer(segment="SMB", updated_at="2026-01-01T00:00:00Z")]))
        self.assertEqual(self.q(V), [("SMB", "2026-01-01T00:00:00Z", "9999-12-31T23:59:59Z", 1)])   # one merged version
        self.assertNotEqual(self.scalar("SELECT customer_key FROM dim_customer"), old_key)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line f JOIN dim_customer d USING (customer_key)"), 1)
        self.assertReconciled(s)

    def test_surrogate_keys_are_deterministic(self):
        self.run_pipe(customers=self.customers_file([customer()]))
        k1 = self.scalar("SELECT customer_key FROM dim_customer")
        from orderflow.ids import customer_key
        self.assertEqual(k1, customer_key("C000001", "2026-01-01T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
