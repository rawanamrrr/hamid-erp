"""Auth-flow tests: email required/unique, phone unique (optional), email password reset."""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.contrib.auth import authenticate

from accounts.models import UserProfile, PasswordResetCode
from accounts.forms import CreateUserForm


class RegistrationFieldTests(TestCase):
    def test_email_required(self):
        form = CreateUserForm(data={'username': 'nu', 'password': 'secret1', 'is_active': True})
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_email_must_be_unique(self):
        User.objects.create_user('existing', email='dup@x.com', password='x')
        form = CreateUserForm(data={
            'username': 'newuser', 'password': 'secret1', 'email': 'DUP@x.com', 'is_active': True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_phone_optional_but_unique(self):
        # No phone → still valid (phone is optional now).
        form = CreateUserForm(data={
            'username': 'nophone', 'password': 'secret1', 'email': 'a@x.com', 'is_active': True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        # Duplicate phone → rejected.
        u = User.objects.create_user('hasphone', email='b@x.com', password='x')
        UserProfile.objects.filter(user=u).update(phone='01000000000')
        form2 = CreateUserForm(data={
            'username': 'nu2', 'password': 'secret1', 'email': 'c@x.com',
            'phone': '01000000000', 'is_active': True,
        })
        self.assertFalse(form2.is_valid())
        self.assertIn('phone', form2.errors)


class EmailResetFlowTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME='localhost')
        self.user = User.objects.create_user('cashier', email='cashier@x.com', password='oldpass')

    def test_full_reset_flow(self):
        r1 = self.client.post(reverse('forgot_password'), {'email': 'cashier@x.com'})
        self.assertRedirects(r1, reverse('forgot_password_verify'))
        rc = PasswordResetCode.objects.get(user=self.user, used=False)
        self.assertTrue(rc.is_valid())

        r2 = self.client.post(reverse('forgot_password_verify'), {
            'code': rc.code, 'new_password': 'brandnew1', 'confirm_password': 'brandnew1',
        })
        self.assertRedirects(r2, reverse('login'))
        self.assertIsNotNone(authenticate(username='cashier', password='brandnew1'))
        rc.refresh_from_db()
        self.assertTrue(rc.used)

    def test_unknown_email_does_not_issue_code(self):
        self.client.post(reverse('forgot_password'), {'email': 'nobody@x.com'})
        self.assertEqual(PasswordResetCode.objects.count(), 0)

    def test_wrong_code_rejected(self):
        self.client.post(reverse('forgot_password'), {'email': 'cashier@x.com'})
        r = self.client.post(reverse('forgot_password_verify'), {
            'code': '000000', 'new_password': 'brandnew1', 'confirm_password': 'brandnew1',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(authenticate(username='cashier', password='brandnew1'))


class OwnerResetTests(TestCase):
    """Offline, free recovery: an admin resets a user's password from user management."""
    def setUp(self):
        self.client = Client(SERVER_NAME='localhost')
        self.admin = User.objects.create_superuser('owner', 'o@x.com', 'ownerpass')
        self.cashier = User.objects.create_user('clerk', email='clerk@x.com', password='old')
        UserProfile.objects.get_or_create(user=self.cashier)

    def test_admin_resets_user_password(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('user_reset_password', args=[self.cashier.pk]),
                         {'new_password': 'freshpass1'})
        self.assertIsNotNone(authenticate(username='clerk', password='freshpass1'))

    def test_short_password_rejected(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('user_reset_password', args=[self.cashier.pk]),
                         {'new_password': '123'})
        self.assertIsNone(authenticate(username='clerk', password='123'))
