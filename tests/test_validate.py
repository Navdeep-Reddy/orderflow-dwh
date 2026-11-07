"""Unit tests for the pure row-validation rules (boundaries, nulls, formats)."""
import unittest

from orderflow.validate import Reject, validate_customer, validate_order_row
from tests.helpers import customer

LIMIT = "2026-11-07T00:00:00Z"


def order(**over):
    base = {"order_id": "ORD-00000001", "line_no": "1", "customer_id": "C000001", "product_code": "P0001",
            "product_name": "Widget", "category": "Tools", "quantity": "2", "unit_price": "10.50",
            "currency": "inr", "order_ts": "2026-02-01T10:00:00Z", "status": "placed",
            "status_ts": "2026-02-01T10:00:00Z"}
    base.update(over)
    return base


class OrderValidation(unittest.TestCase):
    def code(self, **over):
        with self.assertRaises(Reject) as cm:
            validate_order_row(order(**over), LIMIT)
        return cm.exception.code

    def test_happy_path_normalises_and_derives(self):
        r = validate_order_row(order(), LIMIT)
        self.assertEqual((r["currency"], r["status"]), ("INR", "PLACED"))      # upper-cased
        self.assertEqual((r["unit_price_minor"], r["line_amount_minor"]), (1050, 2100))  # exact integer money

    def test_missing_and_blank_required_fields(self):
        for f in ["order_id", "customer_id", "quantity", "unit_price", "status_ts", "product_name"]:
            self.assertEqual(self.code(**{f: ""}), "MISSING_REQUIRED_FIELD", f)
            self.assertEqual(self.code(**{f: "   "}), "MISSING_REQUIRED_FIELD", f)
            self.assertEqual(self.code(**{f: None}), "MISSING_REQUIRED_FIELD", f)

    def test_blank_category_is_allowed_and_defaulted(self):
        self.assertEqual(validate_order_row(order(category=""), LIMIT)["category"], "UNCATEGORISED")

    def test_quantity_boundaries(self):
        self.assertEqual(validate_order_row(order(quantity="1"), LIMIT)["quantity"], 1)
        self.assertEqual(validate_order_row(order(quantity="10000"), LIMIT)["quantity"], 10000)
        self.assertEqual(self.code(quantity="0"), "OUT_OF_RANGE")
        self.assertEqual(self.code(quantity="10001"), "OUT_OF_RANGE")
        self.assertEqual(self.code(quantity="-1"), "OUT_OF_RANGE")
        self.assertEqual(self.code(quantity="1.5"), "INVALID_FORMAT")
        self.assertEqual(self.code(quantity="two"), "INVALID_FORMAT")

    def test_price_boundaries(self):
        self.assertEqual(validate_order_row(order(unit_price="0.00"), LIMIT)["line_amount_minor"], 0)  # free item
        self.assertEqual(validate_order_row(order(unit_price="1000000.00"), LIMIT)["unit_price_minor"], 100_000_000)
        self.assertEqual(validate_order_row(order(unit_price="12.300"), LIMIT)["unit_price_minor"], 1230)
        self.assertEqual(self.code(unit_price="1000000.01"), "OUT_OF_RANGE")
        self.assertEqual(self.code(unit_price="-0.01"), "OUT_OF_RANGE")
        self.assertEqual(self.code(unit_price="12.345"), "INVALID_FORMAT")   # would lose precision
        self.assertEqual(self.code(unit_price="1e3"), "INVALID_FORMAT")
        self.assertEqual(self.code(unit_price="NaN"), "INVALID_FORMAT")

    def test_enums_and_id_formats(self):
        self.assertEqual(self.code(currency="GBP"), "INVALID_ENUM")
        self.assertEqual(self.code(status="LOST"), "INVALID_ENUM")
        self.assertEqual(self.code(order_id="ORD-1"), "INVALID_FORMAT")
        self.assertEqual(self.code(customer_id="X1"), "INVALID_FORMAT")
        self.assertEqual(self.code(product_code="Z9"), "INVALID_FORMAT")
        self.assertEqual(self.code(line_no="0"), "OUT_OF_RANGE")
        self.assertEqual(self.code(line_no="x"), "INVALID_FORMAT")

    def test_timestamps(self):
        self.assertEqual(validate_order_row(order(order_ts="2026-03-01T05:30:00+05:30",
                                                  status_ts="2026-03-01T05:30:00+05:30"), LIMIT)["order_ts"],
                         "2026-03-01T00:00:00Z")                       # offset normalised to UTC
        self.assertEqual(self.code(order_ts="2026-02-01 10:00:00"), "INVALID_TIMESTAMP")   # naive: no zone
        self.assertEqual(self.code(order_ts="2026-13-01T10:00:00Z"), "INVALID_TIMESTAMP")  # impossible month
        self.assertEqual(self.code(order_ts="01/02/2026"), "INVALID_TIMESTAMP")

    def test_domain_rules(self):
        self.assertEqual(self.code(status_ts="2026-01-31T10:00:00Z"), "STATUS_BEFORE_ORDER")
        self.assertEqual(self.code(status_ts="2099-01-01T00:00:00Z"), "FUTURE_TIMESTAMP")
        # exactly at the limit is allowed, one second later is not
        ok = validate_order_row(order(status_ts=LIMIT), LIMIT)
        self.assertEqual(ok["status_ts"], LIMIT)
        self.assertEqual(self.code(status_ts="2026-11-07T00:00:01Z"), "FUTURE_TIMESTAMP")


class CustomerValidation(unittest.TestCase):
    def code(self, **over):
        with self.assertRaises(Reject) as cm:
            validate_customer(customer(**over))
        return cm.exception.code

    def test_happy_path_normalises(self):
        r = validate_customer(customer(email="ASHA@Example.COM", country="in", segment="smb"))
        self.assertEqual((r["email"], r["country"], r["segment"]), ("asha@example.com", "IN", "SMB"))

    def test_rejections(self):
        self.assertEqual(self.code(name=""), "MISSING_REQUIRED_FIELD")
        self.assertEqual(self.code(email="nope"), "INVALID_FORMAT")
        self.assertEqual(self.code(customer_id="c1"), "INVALID_FORMAT")
        self.assertEqual(self.code(segment="GOLD"), "INVALID_ENUM")
        self.assertEqual(self.code(country="Narnia"), "INVALID_ENUM")
        self.assertEqual(self.code(updated_at="yesterday"), "INVALID_TIMESTAMP")
        self.assertEqual(self.code(customer_id=123456), "INVALID_FORMAT")   # wrong JSON type

    def test_missing_key_entirely(self):
        rec = customer()
        del rec["segment"]
        with self.assertRaises(Reject) as cm:
            validate_customer(rec)
        self.assertEqual(cm.exception.code, "MISSING_REQUIRED_FIELD")


if __name__ == "__main__":
    unittest.main()
