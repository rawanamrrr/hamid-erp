"""Landed-cost allocation tests (Phase 7.2)."""
from decimal import Decimal

from django.test import TestCase

from products.models import (Product, Warehouse, Supplier, PurchaseInvoice,
                             PurchaseInvoiceItem, StockBatch)
from products.inventory_services import apply_purchase_invoice_stock


def _product(sku):
    return Product.objects.create(name=f'P{sku}', sku=sku, cost_price=Decimal('0'),
                                  price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
                                  price_wholesale=Decimal('8'))


class LandedCostTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(name='W', is_active=True)
        self.sup = Supplier.objects.create(name='S')

    def _invoice(self, landed):
        return PurchaseInvoice.objects.create(supplier=self.sup, warehouse=self.wh,
                                              status='CONFIRMED', landed_cost=Decimal(landed),
                                              is_stock_applied=False)

    def test_landed_cost_allocated_by_value(self):
        inv = self._invoice('100')
        a, b = _product('LA'), _product('LB')
        # Line A: 10 units @ 10 = 100 value ; Line B: 10 units @ 30 = 300 value. Total 400.
        PurchaseInvoiceItem.objects.create(invoice=inv, product=a, quantity=Decimal('10'),
                                           unit_price=Decimal('10'), subtotal=Decimal('100'))
        PurchaseInvoiceItem.objects.create(invoice=inv, product=b, quantity=Decimal('10'),
                                           unit_price=Decimal('30'), subtotal=Decimal('300'))
        apply_purchase_invoice_stock(inv)
        # A share = 100 * 100/400 = 25 over 10 units = 2.5/unit -> cost 12.5
        # B share = 100 * 300/400 = 75 over 10 units = 7.5/unit -> cost 37.5
        ba = StockBatch.objects.get(product=a)
        bb = StockBatch.objects.get(product=b)
        self.assertEqual(ba.purchase_price, Decimal('12.50'))
        self.assertEqual(bb.purchase_price, Decimal('37.50'))

    def test_zero_landed_cost_unchanged(self):
        inv = self._invoice('0')
        a = _product('LC')
        PurchaseInvoiceItem.objects.create(invoice=inv, product=a, quantity=Decimal('5'),
                                           unit_price=Decimal('20'), subtotal=Decimal('100'))
        apply_purchase_invoice_stock(inv)
        self.assertEqual(StockBatch.objects.get(product=a).purchase_price, Decimal('20'))
