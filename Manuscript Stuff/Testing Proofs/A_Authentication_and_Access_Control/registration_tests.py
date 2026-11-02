import hashlib
import secrets
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AccountSecurity, StaffInvitation


class StaffRegistrationTests(TestCase):
    def test_verified_registration_uses_django_password_and_redirects_to_login(self):
        admin = User.objects.create_superuser('registration-admin', password='admin-pass')
        raw_token = secrets.token_urlsafe(24)
        invitation = StaffInvitation.objects.create(
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            created_by=admin,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        register_url = f'{reverse("staff_register")}?invite={raw_token}'
        self.client.get(register_url)

        with patch('contracts.views.check_challenge', return_value={
            'username': 'new-staff',
            'email': 'new-staff@example.test',
            'phone_number': '+639171234567',
            'channel': 'email',
        }), patch('contracts.views.notify_account', return_value=True):
            response = self.client.post(register_url, {'otp': '123456', 'invite': raw_token})

        self.assertRedirects(response, reverse('password_change_required'), fetch_redirect_response=False)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        user = User.objects.get(username='new-staff')
        self.assertTrue(user.check_password('Welcome123!'))
        self.assertTrue(AccountSecurity.objects.get(user=user).must_change_password)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.used_at)
        self.assertNotIn('staff_invite_token', self.client.session)
