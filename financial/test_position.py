"""Financial position tests (Phase 9.4)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from financial.models import Account
from crm.models import Customer, CustomerPayment
from products.models import Product, Warehouse, StockBatch
from sales.models import Expense
from financial.reports import financial_position


class PositionTests(TestCase):
    def test_cash_ar_inventory_expenses(self):
        Account.objects.create(name='Drawer', account_type='CASH_DRAWER', balance=Decimal('500'))
        Account.objects.create(name='Bank', account_type='BANK', balance=Decimal('1000'))

        u = User.objects.create_user('p', password='x')
        c = Customer.objects.create(first_name='A', last_name='B', phone='POS1')
        CustomerPayment.objects.create(customer=c, user=u, amount=Decimal('300'), transaction_type='debt')  # owes 300

        wh = Warehouse.objects.create(name='W', is_active=True)
        p = Product.objects.create(name='X', sku='POSX', cost_price=Decimal('5'),
                                   price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
                                   price_wholesale=Decimal('8'))
        StockBatch.objects.create(product=p, warehouse=wh, purchase_price=Decimal('5'),
                                  initial_quantity=Decimal('10'), current_quantity=Decimal('10'))  # value 50

        Expense.objects.create(title='Rent', category='rent', amount=Decimal('200'), user=u)

        d = financial_position()
        self.assertEqual(d['cash_total'], Decimal('1500'))   # 500 + 1000
        self.assertEqual(d['ar'], Decimal('300'))
        self.assertEqual(d['inventory_value'], Decimal('50'))
        self.assertEqual(d['exp_total'], Decimal('200'))
        # net = 1500 + 300 + 50 - 0(ap) = 1850
        self.assertEqual(d['net_worth_est'], Decimal('1850'))
