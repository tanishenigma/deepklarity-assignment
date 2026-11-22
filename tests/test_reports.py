import unittest
from datetime import datetime, timezone

from tillpoint import db, reports
from tillpoint.api import App

IST = 330  # UTC+5:30 in minutes


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class DayBoundsTest(unittest.TestCase):
    def test_positive_offset_day_starts_before_utc_midnight(self):
        # India local 2025-03-10 00:00 is 2025-03-09 18:30 UTC.
        self.assertEqual(reports.day_bounds_utc("2025-03-10", IST),
                         ("2025-03-09T18:30:00Z", "2025-03-10T18:30:00Z"))

    def test_negative_offset_day_starts_after_utc_midnight(self):
        # US Pacific (UTC-8) local midnight is 08:00 UTC the same date.
        self.assertEqual(reports.day_bounds_utc("2025-03-10", -480),
                         ("2025-03-10T08:00:00Z", "2025-03-11T08:00:00Z"))

    def test_zero_offset_is_utc_day(self):
        self.assertEqual(reports.day_bounds_utc("2025-03-10"),
                         ("2025-03-10T00:00:00Z", "2025-03-11T00:00:00Z"))


class DailySalesOffsetTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()
        self.app = App(self.conn)
        self.app.catalog.add_product("MUG", "Mug", 1200, stock=100)
        self.n = 0

    def pay(self, when):
        """One 1200-cent order, paid at the given UTC instant."""
        self.n += 1
        order = self.app.orders.create("ana", [("MUG", 1)])
        self.app.payments.handle_event(
            {"id": f"evt_{self.n}", "type": "payment.succeeded",
             "order_id": order["id"], "amount_cents": 1200}, now=when)

    def sales(self, day):
        return reports.daily_sales(self.conn, day, IST)["revenue_cents"]

    def test_morning_sale_in_india_lands_on_its_own_day(self):
        self.pay(utc(2025, 3, 10, 2, 30))  # 08:00 IST on Mar 10
        self.assertEqual(self.sales("2025-03-10"), 1200)
        self.assertEqual(self.sales("2025-03-09"), 0)

    def test_boundary_payments_split_between_days(self):
        self.pay(utc(2025, 3, 9, 18, 29, 59))   # 23:59:59 IST Mar 9
        self.pay(utc(2025, 3, 9, 18, 30, 0))    # 00:00:00 IST Mar 10
        self.pay(utc(2025, 3, 10, 18, 29, 59))  # 23:59:59 IST Mar 10
        self.pay(utc(2025, 3, 10, 18, 30, 0))   # 00:00:00 IST Mar 11
        self.assertEqual(self.sales("2025-03-09"), 1200)
        self.assertEqual(self.sales("2025-03-10"), 2400)
        self.assertEqual(self.sales("2025-03-11"), 1200)


if __name__ == "__main__":
    unittest.main()
