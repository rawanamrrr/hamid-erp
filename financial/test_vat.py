"""VAT report tests (Phase 4.8)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from settings.models import SystemSetting
from crm.models import Customer
from sales.models import Order, OrderItem
from products.models import Supplier, PurchaseInvoice, PurchaseInvoiceItem, Product
from financial.reports import vat_report


class VatReportTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('vat', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='VAT')

    def _sale(self, total):
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal(total),
                                 subtotal_amount=Decimal(total), received_amount=Decimal(total))
        OrderItem.objects.create(order=o, product=None, quantity=Decimal('1'), price=Decimal(total))
        return o

    def test_rate_zero_no_tax(self):
        SystemSetting.objects.create(shop_name='S', vat_rate=Decimal('0'))
        self._sale('100')
        v = vat_report()
        self.assertEqual(v['output_vat'], Decimal('0.00'))
        self.assertEqual(v['net_payable'], Decimal('0.00'))

    def test_inclusive_output_vat(self):
        SystemSetting.objects.create(shop_name='S', vat_rate=Decimal('14'))
        self._sale('114')  # 100 + 14 VAT inclusive
        v = vat_report()
        self.assertEqual(v['output_vat'], Decimal('14.00'))
        self.assertEqual(v['net_sales_ex'], Decimal('100.00'))

    def test_net_payable_subtracts_input_vat(self):
        SystemSetting.objects.create(shop_name='S', vat_rate=Decimal('14'))
        self._sale('114')
        s = Supplier.objects.create(name='Sup')
        inv = PurchaseInvoice.objects.create(supplier=s, status='CONFIRMED',
                                             total_amount=Decimal('50'), net_amount=Decimal('50'))
        p = Product.objects.create(name='P', sku='VATP', cost_price=Decimal('5'),
                                   price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
                                   price_wholesale=Decimal('8'))
        # save() recomputes tax_amount as 14% of the 50 taxable base = 7.00
        PurchaseInvoiceItem.objects.create(invoice=inv, product=p, quantity=Decimal('1'),
                                           unit_price=Decimal('50'), subtotal=Decimal('50'),
                                           tax_rate=Decimal('14'))
        v = vat_report()
        self.assertEqual(v['input_vat'], Decimal('7.00'))
        self.assertEqual(v['net_payable'], Decimal('7.00'))  # 14 output - 7 input
