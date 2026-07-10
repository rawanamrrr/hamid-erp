"""WhatsApp reminder link tests (Phase 8.5)."""
from decimal import Decimal

from django.test import TestCase

from crm.models import Customer


class WhatsAppTests(TestCase):
    def test_phone_normalization_egypt_local(self):
        c = Customer.objects.create(first_name='A', last_name='B', phone='01001234567')
        self.assertEqual(c.whatsapp_phone(), '201001234567')

    def test_phone_already_international(self):
        c = Customer.objects.create(first_name='A', last_name='B', phone='201001234567')
        self.assertEqual(c.whatsapp_phone(), '201001234567')

    def test_phone_with_plus_and_spaces(self):
        c = Customer.objects.create(first_name='A', last_name='B', phone='+20 100 123 4567')
        self.assertEqual(c.whatsapp_phone(), '201001234567')

    def test_url_contains_phone_and_balance(self):
        c = Customer.objects.create(first_name='A', last_name='B', phone='01005550000',
                                    opening_balance=Decimal('250'))
        url = c.whatsapp_url()
        self.assertTrue(url.startswith('https://wa.me/201005550000?text='))
        self.assertIn('250.00', __import__('urllib.parse', fromlist=['unquote']).unquote(url))

    def test_no_phone_returns_empty(self):
        c = Customer.objects.create(first_name='A', last_name='B', phone='---')
        self.assertEqual(c.whatsapp_url(), '')
