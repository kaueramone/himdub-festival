"""Monetary rounding and captured fees across provider changes."""
import sys
from pathlib import Path
from types import SimpleNamespace as Item
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fees import cart_processing_fee_cents, order_processing_fee_cents, stored_processing_fee_cents


class ProcessingFeeTests(unittest.TestCase):
    def test_teya_rounds_total_once_without_fixed_charge(self):
        for subtotal, expected in [(0,0),(100,2),(18000,376),(36500,763),(5000,105)]:
            with self.subTest(subtotal=subtotal):
                items=[Item(price_cents=subtotal,quantity=1)]
                lines=[{'product':Item(effective_price_cents=subtotal),'quantity':1}]
                self.assertEqual(order_processing_fee_cents(items,'teya'),expected)
                self.assertEqual(cart_processing_fee_cents(lines,'teya'),expected)
        # Per-unit rounding would yield 4 cents. The basket fee is 5 cents.
        items=[Item(price_cents=60,quantity=4)]
        self.assertEqual(order_processing_fee_cents(items,'teya'),5)
        self.assertEqual(order_processing_fee_cents([Item(price_cents=120,quantity=2)],'teya'),5)

    def test_stripe_keeps_existing_per_unit_charge(self):
        items=[Item(price_cents=18000,quantity=2)]
        self.assertEqual(order_processing_fee_cents(items,'stripe'),590)
        self.assertEqual(order_processing_fee_cents(items),590)
        self.assertEqual(order_processing_fee_cents(items,'teya'),752)

    def test_existing_order_fee_does_not_depend_on_current_provider(self):
        for fee in (295,376,0):
            order=Item(total_cents=18000+fee,items=[Item(price_cents=18000,quantity=1)])
            self.assertEqual(stored_processing_fee_cents(order),fee)

