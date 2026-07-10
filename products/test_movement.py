"""Item movement card tests (Phase 5.2)."""
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth.models import User

from products.models import Product, Warehouse, StockTransaction


def _product():
    return Product.objects.create(
        name='M', sku='MOV1', cost_price=Decimal('5'),
        price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
        price_wholesale=Decimal('8'),
    )


class MovementCardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('mv', 'm@x.com', 'x')
        self.client = Client()
        self.client.force_login(self.admin)
        self.wh = Warehouse.objects.create(name='W1', is_active=True)
        self.p = _product()

    def test_running_balance(self):
        StockTransaction.objects.create(product=self.p, warehouse=self.wh, transaction_type='IN', quantity=Decimal('100'))
        StockTransaction.objects.create(product=self.p, warehouse=self.wh, transaction_type='OUT', quantity=Decimal('30'))
        StockTransaction.objects.create(product=self.p, warehouse=self.wh, transaction_type='RET_IN', quantity=Decimal('5'))
        resp = self.client.get(f'/products/products/{self.p.id}/movement/')
        self.assertEqual(resp.status_code, 200)
        rows = resp.context['rows']
        self.assertEqual([r['balance'] for r in rows], [Decimal('100'), Decimal('70'), Decimal('75')])
        self.assertEqual(resp.context['closing'], Decimal('75'))
        self.assertEqual(resp.context['total_in'], Decimal('105'))
        self.assertEqual(resp.context['total_out'], Decimal('30'))
