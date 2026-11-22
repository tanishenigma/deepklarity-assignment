"""Sales reports for the shop owner."""
from datetime import datetime, timedelta, timezone

from . import db


def day_bounds_utc(day, utc_offset_minutes=0):
    """The UTC instants at which a shop-local calendar day starts and ends. day is 'YYYY-MM-DD'."""
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start -= timedelta(minutes=utc_offset_minutes)
    return db.to_iso(start), db.to_iso(start + timedelta(days=1))


def daily_sales(conn, day, utc_offset_minutes=0):
    """Money received on one shop-local day, and how many orders it came from."""
    start, end = day_bounds_utc(day, utc_offset_minutes)
    row = conn.execute(
        "SELECT COALESCE(SUM(credited_cents), 0) AS revenue, COUNT(DISTINCT order_id) AS orders"
        " FROM payments WHERE credited_cents > 0 AND received_at >= ? AND received_at < ?",
        (start, end),
    ).fetchone()
    return {"day": day, "revenue_cents": row["revenue"], "orders": row["orders"]}


def overpayments(conn):
    """Orders that received more money than they cost, so finance can refund the difference."""
    rows = conn.execute(
        "SELECT order_id, SUM(amount_cents - credited_cents) AS overpaid_cents FROM payments"
        " GROUP BY order_id HAVING overpaid_cents > 0 ORDER BY order_id"
    ).fetchall()
    return [dict(r) for r in rows]


def top_products(conn, limit=5):
    """Best sellers by units, counting orders that were paid or shipped."""
    rows = conn.execute(
        "SELECT i.sku, SUM(i.qty) AS units, SUM(i.qty * i.unit_price_cents) AS gross_cents"
        " FROM order_items i JOIN orders o ON o.id = i.order_id"
        " WHERE o.status IN ('paid', 'shipped') GROUP BY i.sku ORDER BY units DESC, i.sku LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
