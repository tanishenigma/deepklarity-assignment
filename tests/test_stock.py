import os
import tempfile
import threading
import unittest

from tillpoint import db
from tillpoint.api import App
from tillpoint.catalog import Catalog, OutOfStock


class TwoWorkerStockTest(unittest.TestCase):
    """Two workers = two connections to one SQLite file, each with its own Catalog cache."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "stock.db")
        seed = db.connect(self.path)
        seed.execute("INSERT INTO products (sku, name, price_cents, stock) VALUES ('MUG', 'Mug', 1200, 1)")
        seed.close()
        self.conns = []

    def tearDown(self):
        for c in self.conns:
            c.close()
        self.dir.cleanup()

    def connect(self):
        conn = db.connect(self.path)
        self.conns.append(conn)
        return conn

    def stock(self):
        return self.connect().execute("SELECT stock FROM products WHERE sku = 'MUG'").fetchone()["stock"]

    def test_stale_cache_in_second_worker_cannot_oversell(self):
        worker_a, worker_b = App(self.connect()), App(self.connect())
        self.assertEqual(worker_b.handle("GET", "/products/MUG")[1]["stock"], 1)  # warms B's cache: stock=1
        order = {"customer": "a", "items": [{"sku": "MUG", "qty": 1}]}
        self.assertEqual(worker_a.handle("POST", "/orders", order)[0], 201)       # stock is now 0
        status, payload = worker_b.handle("POST", "/orders", dict(order, customer="b"))
        self.assertEqual(status, 409, payload)
        self.assertEqual(self.stock(), 0)
        orders = self.connect().execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        self.assertEqual(orders, 1)

    def test_stale_price_cache_in_second_worker_is_not_used_for_orders(self):
        worker_a, worker_b = App(self.connect()), App(self.connect())
        self.assertEqual(worker_b.handle("GET", "/products/MUG")[1]["price_cents"], 1200)  # warms B's cache
        self.assertEqual(worker_a.handle("PUT", "/products/MUG", {"price_cents": 1500})[0], 200)
        status, order = worker_b.handle("POST", "/orders", {"customer": "b", "items": [{"sku": "MUG", "qty": 1}]})
        self.assertEqual(status, 201, order)
        self.assertEqual(order["items"][0]["unit_price_cents"], 1500)
        self.assertEqual((order["subtotal_cents"], order["total_cents"]), (1500, 1500))

    def test_two_reservations_for_one_unit_cannot_both_succeed(self):
        # Both workers read the stock before either one writes. A reserve that checks
        # and then updates in separate steps lets both through; an atomic one cannot.
        gate = threading.Barrier(2)

        class Gated(Catalog):
            def get_product(self, sku):
                product = super().get_product(sku)
                try:
                    gate.wait(timeout=1)
                except threading.BrokenBarrierError:
                    pass
                return product

        results = []

        def worker():
            conn = db.connect(self.path)  # per-thread connection, closed by its own thread
            try:
                Gated(conn).reserve("MUG", 1)
                results.append("ok")
            except OutOfStock:
                results.append("out")
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["ok", "out"])
        self.assertEqual(self.stock(), 0)


if __name__ == "__main__":
    unittest.main()
