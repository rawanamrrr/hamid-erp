"""Tests for the batch of read-only reports (Phases 9.3, 5.11, 5.6, 7.4)."""
from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from crm.models import Customer
from sales.models import Order, OrderItem
from products.models import Product, Warehouse, StockBatch, Supplier, PurchaseInvoice
from financial.reports import profitability
from products.aging import supplier_aging


def _product(sku, cost='6'):
    return Product.objects.create(name=f'P{sku}', sku=sku, cost_price=Decimal(cost),
                                  price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
                                  price_wholesale=Decimal('8'))


class ProfitabilityTests(TestCase):
    def test_profit_and_margin(self):
        u = User.objects.create_user('pf', password='x')
        c = Customer.objects.create(first_name='A', last_name='B', phone='PF')
        p = _product('PF1', '6')
        o = Order.objects.create(user=u, customer=c, total_amount=Decimal('100'),
                                 subtotal_amount=Decimal('100'), received_amount=Decimal('100'))
        OrderItem.objects.create(order=o, product=p, quantity=Decimal('10'), price=Decimal('10'),
                                 sell_unit='box', cost_price=Decimal('6'))
        rows, totals = profitability()
        self.assertEqual(totals['revenue'], Decimal('100.00'))
        self.assertEqual(totals['cogs'], Decimal('60.00'))
        self.assertEqual(totals['profit'], Decimal('40.00'))
        self.assertEqual(rows[0]['name'], p.name)


class SupplierAgingTests(TestCase):
    def test_open_invoice_aged(self):
        s = Supplier.objects.create(name='ACME')
        inv = PurchaseInvoice.objects.create(supplier=s, status='CONFIRMED',
                                             total_amount=Decimal('500'), net_amount=Decimal('500'),
                                             paid_amount=Decimal('200'))
        PurchaseInvoice.objects.filter(pk=inv.pk).update(created_at=timezone.now() - timedelta(days=45))
        ag = supplier_aging(s)
        self.assertEqual(ag['d30'], Decimal('300.00'))  # 500 - 200, 45 days old
        self.assertEqual(ag['total'], Decimal('300.00'))
