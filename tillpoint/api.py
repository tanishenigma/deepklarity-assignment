"""A tiny JSON API over the order desk. handle() is the whole surface; server.py puts it on a socket."""
import json
import re

from . import pricing, reports
from .catalog import Catalog, OutOfStock, UnknownProduct
from .orders import OrderError, Orders
from .payments import PaymentError, Payments


class App:
    def __init__(self, conn):
        self.conn = conn
        self.catalog = Catalog(conn)
        self.orders = Orders(conn, self.catalog)
        self.payments = Payments(conn)

    def handle(self, method, path, body=None):
        """Returns (status, payload)."""
        body = body or {}
        try:
            if method == "POST" and path == "/orders":
                order = self.orders.create(
                    body["customer"], [(i["sku"], i["qty"]) for i in body["items"]],
                    region=body.get("region", "NONE"), coupon_code=body.get("coupon"))
                return 201, order
            m = re.fullmatch(r"/orders/(\d+)", path)
            if method == "GET" and m:
                return 200, self.orders.get(int(m.group(1)))
            m = re.fullmatch(r"/orders/(\d+)/cancel", path)
            if method == "POST" and m:
                return 200, self.orders.cancel(int(m.group(1)))
            m = re.fullmatch(r"/products/([\w-]+)", path)
            if method == "GET" and m:
                p = self.catalog.get_product(m.group(1))
                return 200, {"sku": p.sku, "name": p.name, "price_cents": p.price_cents, "stock": p.stock}
            if method == "PUT" and m:
                self.catalog.update_price(m.group(1), body["price_cents"])
                return 200, {"sku": m.group(1), "price_cents": body["price_cents"]}
            if method == "POST" and path == "/webhooks/payments":
                status = self.payments.handle_event(body)
                payload = {"status": status}
                over = self.payments.overpaid_cents(body["order_id"]) if status is not None else 0
                if over:
                    payload["overpaid_cents"] = over
                return 200, payload
            m = re.fullmatch(r"/reports/daily/(\d{4}-\d{2}-\d{2})", path)
            if method == "GET" and m:
                return 200, reports.daily_sales(self.conn, m.group(1), int(body.get("utc_offset_minutes", 0)))
            return 404, {"error": "not found"}
        except (UnknownProduct, OrderError) as e:
            return 404, {"error": str(e)}
        except (OutOfStock, pricing.CouponRejected, PaymentError) as e:
            return 409, {"error": str(e)}
        except (KeyError, ValueError, TypeError) as e:
            return 400, {"error": f"bad request: {e}"}


def dumps(payload):
    return json.dumps(payload, sort_keys=True)
