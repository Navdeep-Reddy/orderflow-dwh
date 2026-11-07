import contextlib
import io
import json
import unittest

from orderflow.cli import main
from tests.helpers import HEADER, PipelineTestCase, customer, order_row


class Cli(PipelineTestCase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(list(args))
        return code, buf.getvalue()

    def test_run_validate_report_flow(self):
        c = self.customers_file([customer()])
        o = self.orders_file([order_row()])
        out = str(self.tmp / "cli_out")
        code, text = self.run_cli("run", "--customers", str(c), "--orders", str(o), "--output-dir", out, "--as-of", "2026-11-06T00:00:00Z", "--log-level", "ERROR")
        self.assertEqual(code, 0, text)
        self.assertIn('"status": "SUCCESS"', text)
        self.assertEqual(len(list((self.tmp / "cli_out").glob("run_report_*.json"))), 1)
        report = json.loads(next((self.tmp / "cli_out").glob("run_report_*.json")).read_text())
        self.assertEqual(report["sources"][0]["rows_read"], 1)
        code, text = self.run_cli("validate", "--db", f"{out}/warehouse.db", "--output-dir", out)
        self.assertEqual(code, 0, text)
        self.assertIn("[PASS]", text)
        code, _ = self.run_cli("report", "--db", f"{out}/warehouse.db", "--output-dir", out)
        self.assertEqual(code, 0)
        self.assertTrue((self.tmp / "cli_out" / "reports" / "analytical_report.md").exists())
        self.assertTrue((self.tmp / "cli_out" / "reports" / "q1_daily_net_revenue.csv").exists())

    def test_nonzero_exit_code_on_failure(self):
        o = self.orders_file([order_row()], header=HEADER.replace("status_ts", "status_time"))
        code, _ = self.run_cli("run", "--orders", str(o), "--output-dir", str(self.tmp / "o"), "--log-level", "CRITICAL")
        self.assertEqual(code, 1)

    def test_reject_file_is_exported(self):
        c = self.customers_file([customer(), customer("C000002"), customer("C000003"), customer("C000004", segment="GOLD")])
        out = self.tmp / "o"
        self.run_cli("run", "--customers", str(c), "--output-dir", str(out), "--as-of", "2026-11-06T00:00:00Z", "--max-reject-ratio", "0.5", "--log-level", "CRITICAL")
        rej = list(out.glob("rejects_*.csv"))
        self.assertEqual(len(rej), 1)
        self.assertIn("INVALID_ENUM", rej[0].read_text())

    def test_analytical_queries_all_run_on_an_empty_warehouse(self):
        from orderflow.db import connect, init_schema
        from orderflow.reports import list_queries, run_query
        conn = connect(self.tmp / "empty.db")
        init_schema(conn)
        self.assertGreaterEqual(len(list_queries()), 8)
        for q in list_queries():
            run_query(conn, q)


if __name__ == "__main__":
    unittest.main()
