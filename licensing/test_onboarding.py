"""Reproduce the fresh-install master-creation flow (empty DB, no users)."""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User

from licensing.utils import generate_token


class FreshInstallTests(TestCase):
    def test_anonymous_can_reach_activation_page(self):
        c = Client(SERVER_NAME='localhost')
        r = c.get(reverse('license_activation'))
        # Must be reachable without logging in (no users exist yet).
        self.assertEqual(r.status_code, 200, "activation page not reachable anonymously")

    def test_create_master_user_token_on_fresh_db(self):
        self.assertEqual(User.objects.count(), 0)
        # Dev generates the token (SECRET_KEY-signed: no per-store key registered for this store).
        tok = generate_token('STORE-NEW', 'CREATE_MASTER_USER', 'owner|pass1234|owner@example.com')
        c = Client(SERVER_NAME='localhost')
        r = c.post(reverse('license_activation'), {'token': tok})
        body = r.json() if r['Content-Type'].startswith('application/json') else {'raw': r.content[:200]}
        print('RESPONSE:', r.status_code, body)
        self.assertTrue(User.objects.filter(username='owner').exists(),
                        f"master not created — response: {r.status_code} {body}")
        u = User.objects.get(username='owner')
        self.assertTrue(u.is_superuser)
        self.assertTrue(u.profile.is_master)

    def test_create_master_user_token_two_part_backward_compatible(self):
        # The legacy 2-part token (no email) must still create the master.
        tok = generate_token('STORE-OLD', 'CREATE_MASTER_USER', 'legacyowner|legacypass')
        c = Client(SERVER_NAME='localhost')
        r = c.post(reverse('license_activation'), {'token': tok})
        self.assertTrue(r.json().get('success'), r.json())
        u = User.objects.get(username='legacyowner')
        self.assertTrue(u.profile.is_master)
        self.assertEqual(u.email, '')  # no email supplied
