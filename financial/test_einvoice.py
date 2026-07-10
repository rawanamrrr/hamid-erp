"""E-invoice JSON builder tests (Phase 10.2)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from settings.models import SystemSetting
from crm.models import Customer
from sales.models import Order, OrderItem
from products.models import Product
from financial.einvoice import build_invoice_json


class EInvoiceTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('ei', password='x')
        SystemSetting.objects.create(shop_name='My Shop', vat_number='ETA-100', vat_rate=Decimal('14'))
        self.c = Customer.objects.create(first_name='ACME', last_name='Co', phone='EI1', tax_number='CUST-55')

    def test_document_structure(self):
        p = Product.objects.create(name='Widget', sku='W1', egs_code='EG-123',
                                   cost_price=Decimal('5'), price_retail=Decimal('10'),
                                   price_semi_wholesale=Decimal('9'), price_wholesale=Decimal('8'))
        o = Order.objects.create(user=self.u, customer=self.c, invoice_number='INV-2026-00001',
                                 total_amount=Decimal('114'), subtotal_amount=Decimal('114'),
                                 received_amount=Decimal('114'))
        OrderItem.objects.create(order=o, product=p, quantity=Decimal('10'), price=Decimal('11.40'),
                                 cost_price=Decimal('5'))
        doc = build_invoice_json(o)
        self.assertEqual(doc['internalId'], 'INV-2026-00001')
        self.assertEqual(doc['issuer']['taxId'], 'ETA-100')
        self.assertEqual(doc['receiver']['taxId'], 'CUST-55')
        self.assertEqual(len(doc['invoiceLines']), 1)
        line = doc['invoiceLines'][0]
        self.assertEqual(line['itemCode'], 'EG-123')
        self.assertEqual(line['itemCodeType'], 'EGS')
        # 114 inclusive at 14% -> tax 14, net 100
        self.assertAlmostEqual(doc['totalTaxAmount'], 14.0, places=1)
        self.assertAlmostEqual(doc['netAmount'], 100.0, places=1)

    def test_barcode_fallback_code_type(self):
        p = Product.objects.create(name='B', sku='B1', barcode='6221000000001',
                                   cost_price=Decimal('1'), price_retail=Decimal('2'),
                                   price_semi_wholesale=Decimal('2'), price_wholesale=Decimal('2'))
        o = Order.objects.create(user=self.u, total_amount=Decimal('2'), subtotal_amount=Decimal('2'))
        OrderItem.objects.create(order=o, product=p, quantity=Decimal('1'), price=Decimal('2'))
        line = build_invoice_json(o)['invoiceLines'][0]
        self.assertEqual(line['itemCode'], '6221000000001')
        self.assertEqual(line['itemCodeType'], 'GS1')
