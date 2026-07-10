"""Token replay-protection tests (single-use licensing tokens)."""
from django.test import TestCase, Client
from django.urls import reverse

from licensing.models import SystemLicense
from licensing.utils import generate_token, token_fingerprint


class TokenReplayTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME='localhost')
        self.url = reverse('license_activation')
        self.store_id = 'STORE-TESTREPLAY'
        # Pre-create the license so generate_token uses its (fallback SECRET_KEY) signing key.
        self.lic = SystemLicense.objects.create(store_id=self.store_id)

    def _post(self, token):
        return self.client.post(self.url, {'token': token})

    def test_extend_subscription_token_is_single_use(self):
        token = generate_token(self.store_id, 'EXTEND_SUBSCRIPTION', '30', expires_in_minutes=60)

        # First use succeeds and extends the subscription.
        r1 = self._post(token)
        self.assertEqual(r1.json().get('success'), True)
        self.lic.refresh_from_db()
        first_expiry = self.lic.subscription_expires_at
        self.assertIsNotNone(first_expiry)
        self.assertIn(token_fingerprint(token), self.lic.used_token_hashes)

        # Replay of the SAME token is rejected and does NOT extend further.
        r2 = self._post(token)
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.json().get('success'), False)
        self.lic.refresh_from_db()
        self.assertEqual(self.lic.subscription_expires_at, first_expiry,
                         "Replayed token must not stack subscription time")

    def test_distinct_tokens_still_work(self):
        # Two different tokens (different value) are independent and both apply.
        t1 = generate_token(self.store_id, 'EXTEND_SUBSCRIPTION', '10', expires_in_minutes=60)
        t2 = generate_token(self.store_id, 'EXTEND_SUBSCRIPTION', '20', expires_in_minutes=60)
        self.assertNotEqual(token_fingerprint(t1), token_fingerprint(t2))
        self.assertTrue(self._post(t1).json().get('success'))
        self.assertTrue(self._post(t2).json().get('success'))
        self.lic.refresh_from_db()
        self.assertEqual(len(self.lic.used_token_hashes), 2)
