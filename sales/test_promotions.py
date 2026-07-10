"""Promotion-engine tests (Phase 6.7): buy-X-get-Y and N-for-price actually apply."""
from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from products.models import Product, Category
from financial.payroll_models import DealDiscount
from sales.services import compute_deal_discount


def _prod(sku, price):
    return Product.objects.create(
        name=f'P{sku}', sku=sku, cost_price=Decimal('1'),
        price_retail=Decimal(str(price)), price_semi_wholesale=Decimal(str(price)),
        price_wholesale=Decimal(str(price)),
    )


def _line(prod, qty, price):
    return {'id': prod.id, 'quantity': qty, 'price': price}


class PromoEngineTests(TestCase):
    def setUp(self):
        self.p = _prod('A', 10)
        self.now = timezone.now()

    def _deal(self, **kw):
        defaults = dict(name='D', value=Decimal('0'),
                        start_date=self.now - timedelta(days=1),
                        end_date=self.now + timedelta(days=1), apply_to_all=True)
        defaults.update(kw)
        return DealDiscount.objects.create(**defaults)

    def test_plain_percentage_unchanged(self):
        d = self._deal(promo_type='discount', discount_type='PERCENTAGE', value=Decimal('10'))
        # qualified subtotal 100 -> 10% = 10
        self.assertEqual(compute_deal_discount(d, [_line(self.p, 5, 10)], Decimal('50')), Decimal('5'))

    def test_buy_one_get_one_same_product(self):
        # Buy 1 get 1 free, 4 units in cart @10 -> 2 free -> discount 20
        d = self._deal(promo_type='buy_x_get_y', buy_x_qty=1, get_y_qty=1)
        self.assertEqual(compute_deal_discount(d, [_line(self.p, 4, 10)], Decimal('40')), Decimal('20'))
        # 3 units -> group size 2 -> 1 free -> 10
        self.assertEqual(compute_deal_discount(d, [_line(self.p, 3, 10)], Decimal('30')), Decimal('10'))

    def test_buy_x_get_y_frees_cheapest(self):
        # Buy 2 get 1 (same scope), cart has 3 @10 and 3 @4 (all_products). group size 3.
        cheap = _prod('B', 4)
        d = self._deal(promo_type='buy_x_get_y', buy_x_qty=2, get_y_qty=1)
        units = [_line(self.p, 3, 10), _line(cheap, 3, 4)]
        # 6 units / group 3 = 2 groups -> 2 free, cheapest first -> 4 + 4 = 8
        self.assertEqual(compute_deal_discount(d, units, Decimal('42')), Decimal('8'))

    def test_buy_n_for_price(self):
        # 3 for 25 on a 10-each product; 7 units -> 2 groups of 3 -> saving (30-25)*2 = 10
        d = self._deal(promo_type='buy_n_for_price', buy_n_qty=3, for_price=Decimal('25'))
        self.assertEqual(compute_deal_discount(d, [_line(self.p, 7, 10)], Decimal('70')), Decimal('10'))

    def test_n_for_price_no_saving_when_price_higher(self):
        d = self._deal(promo_type='buy_n_for_price', buy_n_qty=3, for_price=Decimal('40'))
        # 30 < 40 -> never negative
        self.assertEqual(compute_deal_discount(d, [_line(self.p, 3, 10)], Decimal('30')), Decimal('0'))
