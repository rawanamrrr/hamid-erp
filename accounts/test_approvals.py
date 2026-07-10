"""Manager-approval workflow tests (Phase 3.3)."""
from django.test import TestCase
from django.contrib.auth.models import User

from accounts.models import UserProfile, ApprovalRequest
from accounts.approvals import (
    authorize_inline, is_authorizer, record_approvals, OVER_DISCOUNT, BELOW_COST,
)


class ApprovalAuthTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user('cashier', password='cashpass')
        UserProfile.objects.get_or_create(user=self.cashier)

        self.mgr = User.objects.create_user('mgr', password='mgrpass')
        mp, _ = UserProfile.objects.get_or_create(user=self.mgr)
        mp.direct_permissions = {'sales': ['manage']}
        mp.save()

        self.su = User.objects.create_superuser('su', 's@x.com', 'supass')

    def test_is_authorizer(self):
        self.assertTrue(is_authorizer(User.objects.get(pk=self.mgr.pk)))
        self.assertTrue(is_authorizer(self.su))
        self.assertFalse(is_authorizer(self.cashier))

    def test_authorize_inline_rejects_bad_credentials(self):
        self.assertIsNone(authorize_inline('mgr', 'WRONG', self.cashier))
        self.assertIsNone(authorize_inline('', '', self.cashier))

    def test_authorize_inline_rejects_self_and_non_authorizer(self):
        # A cashier cannot approve their own override.
        self.assertIsNone(authorize_inline('cashier', 'cashpass', self.cashier))
        # A non-authorizer (the cashier) can't authorize someone else's override either.
        self.assertIsNone(authorize_inline('cashier', 'cashpass', self.mgr))

    def test_authorize_inline_accepts_manager(self):
        u = authorize_inline('mgr', 'mgrpass', self.cashier)
        self.assertIsNotNone(u)
        self.assertEqual(u.username, 'mgr')

    def test_record_approvals_persists_one_row_per_violation(self):
        approver = authorize_inline('mgr', 'mgrpass', self.cashier)
        rows = record_approvals(self.cashier, approver, [
            {'kind': OVER_DISCOUNT, 'message': 'd', 'detail': {'cap': 10}},
            {'kind': BELOW_COST, 'message': 'c', 'detail': {'product_id': 1}},
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(ApprovalRequest.objects.count(), 2)
        r = ApprovalRequest.objects.get(kind=OVER_DISCOUNT)
        self.assertEqual(r.status, 'approved')
        self.assertEqual(r.requested_by, self.cashier)
        self.assertEqual(r.approved_by, approver)
        self.assertIsNotNone(r.decided_at)
