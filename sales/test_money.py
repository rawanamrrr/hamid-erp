"""
Money & ledger invariant tests (Phase 0.4) covering the Phase 1 fixes:
- customer balance reflects orders, payments and returns
- cash vs credit returns settle correctly
- credit_paid creates no phantom revenue
- Transaction links to its source order by FK (not description matching)
- voiding an order excludes it from sales/balances
"""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from crm.models import Customer, CustomerPayment
from sales.models import Order, OrderItem
from sales.utils import record_sale_transaction
from financial.models import Account, Transaction


class CustomerBalanceTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('cashier', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='P1')

    def test_manual_debt_and_credit_return(self):
        self.assertEqual(self.c.get_balance(), Decimal('0.00'))
        CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('100'),
                                       transaction_type='debt')
        self.assertEqual(self.c.get_balance(), Decimal('100.00'))
        # credit return of 40 reduces debt
        CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('40'),
                                       transaction_type='payment', payment_method='return_credit')
        self.assertEqual(self.c.get_balance(), Decimal('60.00'))

    def test_cash_return_is_balance_neutral(self):
        CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('100'),
                                       transaction_type='debt')
        # cash return: credit + offsetting debt => net zero on balance
        CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('30'),
                                       transaction_type='payment', payment_method='return_credit')
        CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('30'),
                                       transaction_type='debt', payment_method='return_cash_payout')
        self.assertEqual(self.c.get_balance(), Decimal('100.00'))


class SaleFinancialTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('cashier2', password='x')
        self.c = Customer.objects.create(first_name='C', last_name='D', phone='P2')

    def _order(self, total, cash=0, credit=0):
        return Order.objects.create(
            user=self.u, customer=self.c, total_amount=Decimal(total),
            subtotal_amount=Decimal(total), received_amount=Decimal(cash),
            cash_paid=Decimal(cash), credit_paid=Decimal(credit), payment_method='cash',
        )

    def test_cash_sale_links_transaction_to_order(self):
        o = self._order('100', cash='100')
        record_sale_transaction(o, self.u)
        txns = Transaction.objects.filter(order=o, transaction_type='SALE')
        self.assertEqual(txns.count(), 1)
        self.assertEqual(txns.first().amount, Decimal('100.00'))
        # cash drawer balance went up by 100
        drawer = Account.objects.get(account_type='CASH_DRAWER')
        self.assertEqual(drawer.balance, Decimal('100.00'))

    def test_credit_paid_creates_no_revenue_transaction(self):
        # customer pays 30 cash + 70 from existing credit
        o = self._order('100', cash='30', credit='70')
        record_sale_transaction(o, self.u)
        # only the 30 cash hits the drawer; credit_paid must NOT create a transaction
        sale_txns = Transaction.objects.filter(order=o, transaction_type='SALE')
        self.assertEqual(sale_txns.count(), 1)
        self.assertEqual(sale_txns.first().amount, Decimal('30.00'))
        self.assertFalse(Account.objects.filter(account_type='CREDIT_PAYMENT').exists())


class VoidExclusionTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('cashier3', password='x')
        self.c = Customer.objects.create(first_name='E', last_name='F', phone='P3')

    def test_void_excluded_from_active_and_balance(self):
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('50'),
                                 subtotal_amount=Decimal('50'))
        OrderItem.objects.create(order=o, product=None, quantity=Decimal('1'), price=Decimal('50'))
        self.assertEqual(Order.objects.active().count(), 1)
        self.assertEqual(self.c.get_balance(), Decimal('50.00'))
        o.status = Order.STATUS_VOID
        o.save(update_fields=['status'])
        self.assertEqual(Order.objects.active().count(), 0)
        self.assertEqual(self.c.get_balance(), Decimal('0.00'))  # voided invoice drops out
