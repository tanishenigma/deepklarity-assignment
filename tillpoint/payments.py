"""Payment provider webhooks. The provider retries a delivery until it gets a 2xx, so events can repeat."""
from . import db


class PaymentError(Exception):
    pass


class Payments:
    def __init__(self, conn):
        self.conn = conn

    def handle_event(self, event, now=None):
        """event: dict(id, type, order_id, amount_cents). Returns the order's status after the event."""
        if event.get("type") != "payment.succeeded":
            return None
        # The lock comes first so a retry handled by another worker at the same moment is seen below.
        # The unique index on payments.event_id is the backstop if this ever runs without it.
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            status = self._record(event, now)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return status

    def _record(self, event, now):
        order = self.conn.execute("SELECT * FROM orders WHERE id = ?", (event["order_id"],)).fetchone()
        if order is None:
            raise PaymentError(f"payment for unknown order {event['order_id']}")
        seen = self.conn.execute("SELECT * FROM payments WHERE event_id = ?", (event["id"],)).fetchone()
        if seen is not None:
            # A retry of an event we already took: answer as before, record nothing.
            if seen["order_id"] != order["id"] or seen["amount_cents"] != event["amount_cents"]:
                raise PaymentError(f"event {event['id']} was already recorded with different details")
            return order["status"]
        if order["status"] == "cancelled":
            raise PaymentError(f"order {order['id']} is cancelled")
        self.conn.execute(
            "INSERT INTO payments (order_id, event_id, amount_cents, received_at) VALUES (?, ?, ?, ?)",
            (order["id"], event["id"], event["amount_cents"], db.to_iso(now or db.utcnow())),
        )
        if order["status"] == "pending" and self.paid_cents(order["id"]) >= order["total_cents"]:
            self.conn.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order["id"],))
            return "paid"
        return order["status"]

    def paid_cents(self, order_id):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS paid FROM payments WHERE order_id = ?", (order_id,)
        ).fetchone()
        return row["paid"]
