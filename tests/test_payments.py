import sqlite3
import unittest
from datetime import datetime, timezone

from tillpoint import db, reports
from tillpoint.api import App

NOON = datetime(2025, 3, 10, 12, 0, tzinfo=timezone.utc)


class WebhookRetryTest(unittest.TestCase):
    """The provider retries a delivery until it gets a 2xx, so the same event id can arrive many times."""

    def setUp(self):
        self.conn = db.connect()
        self.app = App(self.conn)
        self.app.catalog.add_product("MUG", "Mug", 1200, stock=10)
        self.order = self.app.orders.create("ana", [("MUG", 1)])
        self.event = {"id": "evt_1", "type": "payment.succeeded",
                      "order_id": self.order["id"], "amount_cents": 1200}

    def rows(self):
        return self.conn.execute("SELECT event_id, amount_cents FROM payments").fetchall()

    def test_retried_event_is_recorded_once(self):
        for _ in range(3):
            self.assertEqual(self.app.payments.handle_event(self.event, now=NOON), "paid")
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.app.payments.paid_cents(self.order["id"]), 1200)

    def test_retried_event_does_not_inflate_daily_revenue(self):
        for _ in range(3):
            self.app.payments.handle_event(self.event, now=NOON)
        self.assertEqual(reports.daily_sales(self.conn, "2025-03-10"),
                         {"day": "2025-03-10", "revenue_cents": 1200, "orders": 1})

    def test_retry_over_http_gets_2xx_every_time(self):
        for _ in range(3):
            status, payload = self.app.handle("POST", "/webhooks/payments", self.event)
            self.assertEqual((status, payload), (200, {"status": "paid"}))
        self.assertEqual(len(self.rows()), 1)

    def test_retry_after_the_order_was_cancelled_still_gets_2xx(self):
        self.app.payments.handle_event(self.event, now=NOON)
        self.app.orders.cancel(self.order["id"])
        status, payload = self.app.handle("POST", "/webhooks/payments", self.event)
        self.assertEqual((status, payload), (200, {"status": "cancelled"}))
        self.assertEqual(len(self.rows()), 1)

    def test_same_event_id_with_different_details_is_a_conflict_and_not_recorded(self):
        self.app.payments.handle_event(self.event, now=NOON)
        for changed in ({"amount_cents": 99999}, {"order_id": self.order["id"] + 1}):
            status, payload = self.app.handle("POST", "/webhooks/payments", dict(self.event, **changed))
            self.assertEqual(status, 409, payload)
        self.assertEqual(self.rows()[0]["amount_cents"], 1200)
        self.assertEqual(len(self.rows()), 1)

    def test_database_refuses_a_second_row_for_one_event_id(self):
        # Backstop for two workers handling the same retry at once.
        self.app.payments.handle_event(self.event, now=NOON)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO payments (order_id, event_id, amount_cents, received_at) VALUES (?, ?, ?, ?)",
                (self.order["id"], "evt_1", 1200, "2025-03-10T12:00:00Z"))


if __name__ == "__main__":
    unittest.main()
