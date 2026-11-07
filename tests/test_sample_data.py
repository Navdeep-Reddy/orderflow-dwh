"""Ground-truth tests: the pipeline's answers must match labels that were produced by the
generator independently of pipeline code and stored OUTSIDE the input files."""
import filecmp
import json
import unittest
from pathlib import Path

from orderflow.generate_data import generate
from tests.helpers import PipelineTestCase

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


class LabelChecks(PipelineTestCase):
    def check_dataset(self, d: Path):
        labels = json.loads((d / "expected" / "expected_labels.json").read_text())
        s = self.run_pipe(customers=d / labels["customers"]["file"], orders=d / labels["orders"]["file"], max_reject_ratio=0.5)
        self.assertEqual(s["status"], "SUCCESS")
        self.assertReconciled(s)

        for source, key in (("customers", "customers"), ("orders", "orders")):
            lab = labels[key]
            got = {(ln, rc) for ln, rc in self.q("SELECT line_number, reason_code FROM reject_record WHERE run_id=? AND source_name=?", (s["run_id"], source))}
            exp = {(r["line"], r["reason"]) for r in lab["expected_rejects"]}
            self.assertEqual(got, exp, f"{source}: rejected (line, reason) pairs differ from labels")
            src = self.src(s, source)
            self.assertEqual((src["rows_read"], src["rows_accepted"], src["rows_rejected"]),
                             (lab["total_rows"], lab["expected_accepted"], len(lab["expected_rejects"])))

        exp = labels["expected_aggregates"]
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), exp["order_lines"])
        self.assertEqual(self.scalar("SELECT COUNT(DISTINCT customer_id) FROM dim_customer"), exp["customers"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM dim_customer"), exp["dim_customer_versions"])
        self.assertEqual(dict(self.q("SELECT status, COUNT(*) FROM fact_order_line GROUP BY status")), exp["final_status_counts"])
        self.assertEqual(dict(self.q("SELECT currency, SUM(line_amount_minor) FROM fact_order_line WHERE is_net_revenue=1 GROUP BY currency")),
                         exp["net_revenue_minor_by_currency"])
        self.assertEqual({f"{seg}|{cur}": v for seg, cur, v in self.q(
            "SELECT d.segment, f.currency, SUM(f.line_amount_minor) FROM fact_order_line f "
            "JOIN dim_customer d USING (customer_key) WHERE f.is_net_revenue=1 GROUP BY 1,2")},
            exp["net_revenue_minor_by_segment_at_order_time"])
        return labels, exp

    def test_checked_in_sample_dataset(self):
        labels, exp = self.check_dataset(SAMPLE)
        late = labels["orders_late"]
        events_before = self.scalar("SELECT COUNT(*) FROM order_line_event")
        s = self.run_pipe(orders=SAMPLE / late["file"])                        # second, late-arriving batch
        self.assertEqual(self.src(s, "orders")["rows_rejected"], 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_line_event") - events_before, late["expected_new_events"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_order_line"), exp["order_lines"])          # state unchanged
        self.assertEqual(dict(self.q("SELECT status, COUNT(*) FROM fact_order_line GROUP BY status")), exp["final_status_counts"])
        self.assertReconciled(s)

    def test_larger_generated_datasets_with_different_seeds(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                d = self.tmp / f"gen{seed}"
                generate(d, n_customers=150, n_order_lines=600, seed=seed, invalid_rate=0.04, dup_rate=0.01, late_fraction=0.1, n_products=25)
                self.db = self.tmp / f"wh{seed}.db"
                self.check_dataset(d)

    def test_labels_are_not_embedded_in_inputs(self):
        for name in ("customers.jsonl", "orders.csv"):
            text = (SAMPLE / name).read_text().lower()
            for banned in ("expected", "duplicate_event", "label", "invalid_enum"):
                self.assertNotIn(banned, text, f"{name} leaks the answer key ({banned})")


class GeneratorReproducibility(PipelineTestCase):
    def test_same_seed_gives_byte_identical_files(self):
        a, b = self.tmp / "a", self.tmp / "b"
        generate(a, 40, 120, seed=5), generate(b, 40, 120, seed=5)
        for rel in ("customers.jsonl", "orders.csv", "orders_late.csv", "expected/expected_labels.json"):
            self.assertTrue(filecmp.cmp(a / rel, b / rel, shallow=False), rel)

    def test_different_seed_gives_different_data(self):
        a, b = self.tmp / "a", self.tmp / "b"
        generate(a, 40, 120, seed=5), generate(b, 40, 120, seed=6)
        self.assertFalse(filecmp.cmp(a / "orders.csv", b / "orders.csv", shallow=False))

    def test_checked_in_sample_matches_generator(self):
        """The committed sample must be exactly what `python -m orderflow generate` produces."""
        d = self.tmp / "regen"
        generate(d)   # default arguments = the sample
        for rel in ("customers.jsonl", "orders.csv", "orders_late.csv"):
            self.assertTrue(filecmp.cmp(d / rel, SAMPLE / rel, shallow=False), rel)


if __name__ == "__main__":
    unittest.main()
