"""
Double-entry posting engine tests (Phase 4.2 / 4.6).

Verifies every posted entry balances and that sale/refund legs land on the right
nominal accounts, plus that the derived trial balance and P&L are correct.
"""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User
from django.db.models import Sum

from crm.models import Customer
from sales.models import Order, OrderItem
from products.models import Product
from financial.models import JournalEntry, JournalLine
from financial.posting import post_sale, post_refund, nominal, cash_account
from financial import reports


def _product(cost='6'):
    return Product.objects.create(
        name='P', sku=f'SKU{cost}', cost_price=Decimal(cost),
        price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
        price_wholesale=Decimal('8'),
    )


class PostingBalanceTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('p', password='x')
        self.c = Customer.objects.create(first_name='C', last_name='D', phone='PP')
        self.p = _product('6')

    def _entry_balances(self, ref):
        agg = JournalLine.objects.filter(entry__reference_number=ref).aggregate(
            d=Sum('debit'), c=Sum('credit'))
        self.assertEqual(agg['d'], agg['c'], f"{ref} not balanced")
        return agg['d']

    def _line(self, ref, code):
        return JournalLine.objects.filter(
            entry__reference_number=ref, account__code=code).aggregate(
            d=Sum('debit'), c=Sum('credit'))

    def test_cash_sale_legs(self):
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('100'),
                                 subtotal_amount=Decimal('100'), received_amount=Decimal('100'),
                                 cash_paid=Decimal('100'))
        OrderItem.objects.create(order=o, product=self.p, quantity=Decimal('10'), price=Decimal('10'),
                                 sell_unit='box', cost_price=Decimal('6'))
        post_sale(o)
        self._entry_balances('SALE-{}'.format(o.id))
        # revenue credited 100
        self.assertEqual(self._line(f'SALE-{o.id}', '4100')['c'], Decimal('100.00'))
        # COGS 60 debit, inventory 60 credit
        self.assertEqual(self._line(f'SALE-{o.id}', '5100')['d'], Decimal('60.00'))
        self.assertEqual(self._line(f'SALE-{o.id}', '1300')['c'], Decimal('60.00'))
        # cash drawer debited 100 (no AR)
        self.assertEqual(self._line(f'SALE-{o.id}', '1200')['d'] or Decimal('0'), Decimal('0'))

    def test_credit_sale_debits_ar(self):
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('100'),
                                 subtotal_amount=Decimal('100'), received_amount=Decimal('30'),
                                 cash_paid=Decimal('30'))
        OrderItem.objects.create(order=o, product=self.p, quantity=Decimal('10'), price=Decimal('10'),
                                 sell_unit='box', cost_price=Decimal('6'))
        post_sale(o)
        self._entry_balances(f'SALE-{o.id}')
        self.assertEqual(self._line(f'SALE-{o.id}', '1200')['d'], Decimal('70.00'))  # AR = 100-30

    def test_overpaid_sale_credits_ar(self):
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('100'),
                                 subtotal_amount=Decimal('100'), received_amount=Decimal('120'),
                                 cash_paid=Decimal('120'))
        OrderItem.objects.create(order=o, product=self.p, quantity=Decimal('10'), price=Decimal('10'),
                                 sell_unit='box', cost_price=Decimal('6'))
        post_sale(o)
        self._entry_balances(f'SALE-{o.id}')
        self.assertEqual(self._line(f'SALE-{o.id}', '1200')['c'], Decimal('20.00'))  # overpay -> credit AR

    def test_strip_cogs_uses_box_quantity(self):
        sp = Product.objects.create(name='S', sku='STRIP', cost_price=Decimal('100'),
                                    price_retail=Decimal('15'), price_semi_wholesale=Decimal('14'),
                                    price_wholesale=Decimal('13'), strips_per_box=10)
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('150'),
                                 subtotal_amount=Decimal('150'), received_amount=Decimal('150'),
                                 cash_paid=Decimal('150'))
        # sell 10 strips = 1 box; cost_price is per box (100) -> COGS must be 100, not 1000
        OrderItem.objects.create(order=o, product=sp, quantity=Decimal('10'), price=Decimal('15'),
                                 sell_unit='strip', cost_price=Decimal('100'))
        post_sale(o)
        self.assertEqual(self._line(f'SALE-{o.id}', '5100')['d'], Decimal('100.00'))


class StatementTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('p2', password='x')
        self.c = Customer.objects.create(first_name='C', last_name='D', phone='PP2')
        self.p = _product('6')

    def test_trial_balance_and_pnl(self):
        for _ in range(3):
            o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('100'),
                                     subtotal_amount=Decimal('100'), received_amount=Decimal('100'),
                                     cash_paid=Decimal('100'))
            OrderItem.objects.create(order=o, product=self.p, quantity=Decimal('10'), price=Decimal('10'),
                                     sell_unit='box', cost_price=Decimal('6'))
            post_sale(o)
        tb = reports.trial_balance()
        self.assertTrue(tb['balanced'])
        pnl = reports.income_statement()
        self.assertEqual(pnl['sales_revenue'], Decimal('300.00'))
        self.assertEqual(pnl['cogs'], Decimal('180.00'))
        self.assertEqual(pnl['gross_profit'], Decimal('120.00'))
