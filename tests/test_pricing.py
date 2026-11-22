import unittest

from tillpoint import db, pricing
from tillpoint.api import App


def coupon(kind, value, code="C"):
    return {"code": code, "kind": kind, "value": value, "min_subtotal_cents": 0, "max_uses": None, "uses": 0}


class CouponTaxTest(unittest.TestCase):
    """A coupon is a pre-tax discount: tax is charged on subtotal minus discount."""

    def price(self, region, c, subtotal=10000):
        return pricing.price_order([(subtotal, 1)], region, c)

    def test_percent_coupon_taxed_after_discount(self):
        ten = coupon("percent", 10)
        # (subtotal 10000, discount 1000) -> taxable 9000
        self.assertEqual(self.price("CA", ten), {  # 9000 * 7.25% = 652.5 -> 653
            "subtotal_cents": 10000, "discount_cents": 1000, "tax_cents": 653, "total_cents": 9653})
        self.assertEqual(self.price("NY", ten), {  # 9000 * 8.88% = 799.2 -> 799
            "subtotal_cents": 10000, "discount_cents": 1000, "tax_cents": 799, "total_cents": 9799})
        self.assertEqual(self.price("DE", ten), {  # 9000 * 19% = 1710
            "subtotal_cents": 10000, "discount_cents": 1000, "tax_cents": 1710, "total_cents": 10710})

    def test_fixed_coupon_taxed_after_discount(self):
        # taxable 9500; 9500 * 7.25% = 688.75 -> 689
        self.assertEqual(self.price("CA", coupon("fixed", 500)), {
            "subtotal_cents": 10000, "discount_cents": 500, "tax_cents": 689, "total_cents": 10189})

    def test_coupon_covering_whole_subtotal_means_no_tax(self):
        for c in (coupon("percent", 100), coupon("fixed", 5000), coupon("fixed", 9999)):
            with self.subTest(kind=c["kind"], value=c["value"]):
                self.assertEqual(self.price("DE", c, subtotal=5000), {
                    "subtotal_cents": 5000, "discount_cents": 5000, "tax_cents": 0, "total_cents": 0})

    def test_no_coupon_unchanged(self):
        self.assertEqual(self.price("CA", None), {
            "subtotal_cents": 10000, "discount_cents": 0, "tax_cents": 725, "total_cents": 10725})

    def test_order_receipt_uses_discounted_tax(self):
        app = App(db.connect())
        app.catalog.add_product("MUG", "Mug", 10000, stock=5)
        app.conn.execute("INSERT INTO coupons (code, kind, value) VALUES ('TEN', 'percent', 10)")
        order = app.orders.create("ana", [("MUG", 1)], region="CA", coupon_code="TEN")
        self.assertEqual((order["discount_cents"], order["tax_cents"], order["total_cents"]), (1000, 653, 9653))


if __name__ == "__main__":
    unittest.main()
