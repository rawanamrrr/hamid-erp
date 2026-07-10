"""Invoice visibility scoping tests (Phase 3.5)."""
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth.models import User

from crm.models import Customer
from sales.models import Order


class VisibilityTests(TestCase):
    def setUp(self):
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='V1')
        self.cashier = User.objects.create_user('cashier', password='x')
        self.other = User.objects.create_user('other', password='x')
        self.admin = User.objects.create_superuser('boss', 'b@x.com', 'x')
        self.o_mine = Order.objects.create(user=self.cashier, customer=self.c, total_amount=Decimal('10'),
                                           subtotal_amount=Decimal('10'))
        self.o_theirs = Order.objects.create(user=self.other, customer=self.c, total_amount=Decimal('20'),
                                             subtotal_amount=Decimal('20'))

    def test_cashier_sees_only_own(self):
        client = Client(); client.force_login(self.cashier)
        resp = client.get('/sales/orders/')
        ids = set(resp.context['orders'].values_list('id', flat=True))
        self.assertIn(self.o_mine.id, ids)
        self.assertNotIn(self.o_theirs.id, ids)

    def test_superuser_sees_all(self):
        client = Client(); client.force_login(self.admin)
        resp = client.get('/sales/orders/')
        ids = set(resp.context['orders'].values_list('id', flat=True))
        self.assertIn(self.o_mine.id, ids)
        self.assertIn(self.o_theirs.id, ids)
