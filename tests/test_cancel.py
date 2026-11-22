import unittest

from tillpoint import db
from tillpoint.api import App
from tillpoint.orders import OrderError


class CancelRestockTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()
        self.app = App(self.conn)
        self.app.catalog.add_product("MUG", "Mug", 1200, stock=10)
        self.app.catalog.add_product("TEE", "T-shirt", 2500, stock=5)

    def stock(self, sku):
        return self.conn.execute("SELECT stock FROM products WHERE sku = ?", (sku,)).fetchone()["stock"]

    def test_cancel_unpaid_order_returns_stock(self):
        order = self.app.orders.create("ana", [("MUG", 3)])
        self.assertEqual(order["status"], "pending")
        self.assertEqual(self.stock("MUG"), 7)
        self.app.orders.cancel(order["id"])
        self.assertEqual(self.stock("MUG"), 10)
        self.assertEqual(self.app.catalog.get_product("MUG").stock, 10)

    def test_cancel_unpaid_multi_item_order_returns_every_line(self):
        order = self.app.orders.create("ana", [("MUG", 3), ("TEE", 2)])
        self.app.orders.cancel(order["id"])
        self.assertEqual((self.stock("MUG"), self.stock("TEE")), (10, 5))

    def test_cancelling_twice_returns_stock_only_once(self):
        order = self.app.orders.create("ana", [("MUG", 3)])
        self.app.orders.cancel(order["id"])
        self.app.orders.cancel(order["id"])
        self.assertEqual(self.stock("MUG"), 10)

    def test_shipped_order_cannot_be_cancelled_and_keeps_stock_taken(self):
        order = self.app.orders.create("ana", [("MUG", 3)])
        self.app.payments.handle_event(
            {"id": "evt_1", "type": "payment.succeeded", "order_id": order["id"], "amount_cents": 3600})
        self.app.orders.ship(order["id"])
        with self.assertRaises(OrderError):
            self.app.orders.cancel(order["id"])
        self.assertEqual(self.stock("MUG"), 7)


if __name__ == "__main__":
    unittest.main()
