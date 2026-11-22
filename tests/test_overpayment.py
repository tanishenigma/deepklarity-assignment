import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from tillpoint import db, reports
from tillpoint.api import App

NOON = datetime(2025, 3, 10, 12, 0, tzinfo=timezone.utc)


class OverpaymentTest(unittest.TestCase):
    """Policy: an order is credited up to its total. Money beyond that is kept, flagged as an
    overpayment for a refund, and never counted as revenue. The webhook still answers 2xx."""

    def setUp(self):
        self.conn = db.connect()
        self.app = App(self.conn)
        self.app.catalog.add_product("MUG", "Mug", 1000, stock=10)
        self.order = self.app.orders.create("ana", [("MUG", 1)])  # total 1000

    def pay(self, event_id, amount, now=NOON):
        return self.app.payments.handle_event(
            {"id": event_id, "type": "payment.succeeded", "order_id": self.order["id"], "amount_cents": amount},
            now=now)

    def status(self):
        return self.app.orders.get(self.order["id"])["status"]

    def test_second_full_payment_is_held_as_overpayment(self):
        self.assertEqual(self.pay("evt_1", 1000), "paid")
        self.assertEqual(self.pay("evt_2", 1000), "paid")
        self.assertEqual(self.status(), "paid")
        self.assertEqual(self.app.payments.paid_cents(self.order["id"]), 1000)
        self.assertEqual(self.app.payments.overpaid_cents(self.order["id"]), 1000)

    def test_overpayment_is_not_counted_as_revenue(self):
        self.pay("evt_1", 1000)
        self.pay("evt_2", 1000)
        self.assertEqual(reports.daily_sales(self.conn, "2025-03-10"),
                         {"day": "2025-03-10", "revenue_cents": 1000, "orders": 1})

    def test_both_payments_are_still_recorded(self):
        self.pay("evt_1", 1000)
        self.pay("evt_2", 1000)
        rows = self.conn.execute(
            "SELECT event_id, amount_cents, credited_cents FROM payments ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("evt_1", 1000, 1000), ("evt_2", 1000, 0)])

    def test_partial_payments_are_capped_at_the_total(self):
        self.assertEqual(self.pay("evt_1", 600), "pending")
        self.assertEqual(self.pay("evt_2", 600), "paid")  # 400 credited, 200 over
        self.assertEqual(self.app.payments.paid_cents(self.order["id"]), 1000)
        self.assertEqual(self.app.payments.overpaid_cents(self.order["id"]), 200)

    def test_exact_and_under_payments_are_unchanged(self):
        self.assertEqual(self.pay("evt_1", 400), "pending")
        self.assertEqual(self.app.payments.overpaid_cents(self.order["id"]), 0)
        self.assertEqual(self.pay("evt_2", 600), "paid")
        self.assertEqual(self.app.payments.overpaid_cents(self.order["id"]), 0)

    def test_retry_of_the_overpaying_event_does_not_change_anything(self):
        self.pay("evt_1", 1000)
        self.pay("evt_2", 1000)
        self.pay("evt_2", 1000)
        self.assertEqual(self.app.payments.overpaid_cents(self.order["id"]), 1000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 2)

    def test_webhook_flags_the_overpayment_and_still_returns_200(self):
        def post(event_id):
            return self.app.handle("POST", "/webhooks/payments", {
                "id": event_id, "type": "payment.succeeded",
                "order_id": self.order["id"], "amount_cents": 1000})
        self.assertEqual(post("evt_1"), (200, {"status": "paid"}))
        self.assertEqual(post("evt_2"), (200, {"status": "paid", "overpaid_cents": 1000}))
        self.assertEqual(post("evt_2"), (200, {"status": "paid", "overpaid_cents": 1000}))

    def test_overpayments_report_lists_orders_to_refund(self):
        self.assertEqual(reports.overpayments(self.conn), [])
        self.pay("evt_1", 1000)
        self.pay("evt_2", 1000)
        self.assertEqual(reports.overpayments(self.conn),
                         [{"order_id": self.order["id"], "overpaid_cents": 1000}])


class PaymentsMigrationTest(unittest.TestCase):
    def test_existing_database_gets_credited_column_backfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            old = sqlite3.connect(path)
            old.executescript("""
                CREATE TABLE payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL, amount_cents INTEGER NOT NULL, received_at TEXT NOT NULL);
                INSERT INTO payments (order_id, event_id, amount_cents, received_at)
                    VALUES (1, 'evt_old', 1200, '2025-03-10T12:00:00Z');""")
            old.close()
            for _ in range(2):  # a second open must not try to migrate again
                conn = db.connect(path)
                row = conn.execute("SELECT amount_cents, credited_cents FROM payments").fetchone()
                self.assertEqual(tuple(row), (1200, 1200))
                conn.close()


if __name__ == "__main__":
    unittest.main()
