import hashlib
import re
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import PasswordResetRequestAdmin
from .forms import StaffRegistrationForm
from .messaging import normalize_phone, issue_challenge, check_challenge, send_message, code_digest, notify_account
from .models import AccountSecurity, StaffInvitation, SMSMessageLog, EmailMessageLog, PasswordResetRequest


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SMS_DELIVERY_BACKEND='',
    N8N_EMAIL_WEBHOOK_URL='',
    SECURE_SSL_REDIRECT=False,
)
class SMSVerificationTests(TestCase):
    def setUp(self):
        cache.clear()

    def code(self, model=SMSMessageLog):
        return re.search(r'\b[0-9]{6}\b', model.objects.order_by('-pk').first().body).group()

    def staff(self):
        user = User.objects.create_user('staff-sms', 'staff@example.com', 'OldPassword!123', is_staff=True)
        AccountSecurity.objects.create(user=user, phone_number='+639171234567', phone_verified=True)
        return user

    def approve(self, reset):
        model_admin = PasswordResetRequestAdmin(PasswordResetRequest, admin.site)
        with patch.object(model_admin, 'message_user'):
            model_admin.approve_reset_requests(RequestFactory().get('/'), PasswordResetRequest.objects.filter(pk=reset.pk))
        reset.refresh_from_db()

    def test_phone_normalization_and_validation(self):
        for phone in ['0917 123 4567', '+63 917 123 4567', '639171234567']:
            self.assertEqual(normalize_phone(phone), '+639171234567')
        for phone in ['abc', '12345', '+0123456789', '09171234567<script>']:
            with self.assertRaises(ValidationError):
                normalize_phone(phone)
        self.assertFalse(StaffRegistrationForm({'username': 'new', 'email': 'a@b.com'}).is_valid())

    def test_sms_registration_verifies_only_selected_contact(self):
        token = 'invite-sms'
        invite = StaffInvitation.objects.create(token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=timezone.now()+timedelta(hours=1))
        url = reverse('staff_register') + '?invite=' + token
        response = self.client.post(url, {'username': 'newstaff', 'email': 'new@example.com', 'phone_number': '09171234567', 'channel': 'sms'})
        self.assertContains(response, '+639171234567')
        code = self.code()
        self.assertEqual(SMSMessageLog.objects.order_by('-pk').first().status, 'queued')
        response = self.client.post(url, {'otp': code, 'phone_number': '+639999999999'})
        self.assertRedirects(response, reverse('password_change_required'), fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)
        security = AccountSecurity.objects.get(user__username='newstaff')
        self.assertTrue(security.phone_verified)
        self.assertFalse(security.email_verified)
        self.assertEqual(security.phone_number, '+639171234567')
        invite.refresh_from_db()
        self.assertIsNotNone(invite.used_at)
        self.assertEqual(EmailMessageLog.objects.count(), 0)

    def test_email_registration_still_works(self):
        token = 'invite-email'
        StaffInvitation.objects.create(token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=timezone.now()+timedelta(hours=1))
        url = reverse('staff_register') + '?invite=' + token
        self.client.post(url, {'username': 'emailstaff', 'email': 'new@example.com', 'phone_number': '09171234567', 'channel': 'email'})
        self.client.post(url, {'otp': self.code(EmailMessageLog)})
        security = AccountSecurity.objects.get(user__username='emailstaff')
        self.assertTrue(security.email_verified)
        self.assertFalse(security.phone_verified)

    def test_sms_preference_falls_back_to_email_until_sms_backend_exists(self):
        user = User.objects.create_user('admin-notify', 'admin@example.com', 'Password!123', is_superuser=True)
        AccountSecurity.objects.create(
            user=user, phone_number='+639171234567', phone_verified=True,
            preferred_channel='sms',
        )
        self.assertTrue(notify_account(user, 'Reset approval', 'Please approve the request.'))
        self.assertEqual(EmailMessageLog.objects.get().recipient, 'admin@example.com')
        self.assertEqual(SMSMessageLog.objects.count(), 0)

    @override_settings(
        N8N_EMAIL_WEBHOOK_URL='https://n8n.example.test/webhook/sealguard/email-delivery',
        N8N_EMAIL_WEBHOOK_HEADER='X-SealGuard-Webhook',
        N8N_EMAIL_WEBHOOK_SECRET='test-secret',
    )
    def test_email_delivery_uses_n8n_when_configured(self):
        response = MagicMock(status=200)
        response.read.return_value = b'{"ok": true, "channel": "email"}'
        with patch('contracts.messaging.urlopen') as opener:
            opener.return_value.__enter__.return_value = response
            self.assertTrue(send_message('email', 'recipient@example.com', 'Subject', 'Body'))
        opener.assert_called_once()
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, 'https://n8n.example.test/webhook/sealguard/email-delivery')
        self.assertEqual(request.get_header('X-sealguard-webhook'), 'test-secret')
        self.assertEqual(EmailMessageLog.objects.get().delivered, True)

    @override_settings(
        N8N_EMAIL_WEBHOOK_URL='https://n8n.example.test/webhook/sealguard/email-delivery',
        N8N_EMAIL_WEBHOOK_SECRET='',
    )
    def test_incomplete_n8n_email_configuration_fails_closed(self):
        with patch('contracts.messaging.urlopen') as opener:
            self.assertFalse(send_message('email', 'recipient@example.com', 'Subject', 'Body'))
        opener.assert_not_called()
        self.assertFalse(EmailMessageLog.objects.get().delivered)

    def test_challenge_expiry_attempt_limit_and_replay(self):
        issue_challenge('test', 'sms', '+639171234567', 'Test')
        code = self.code()
        for _ in range(5):
            self.assertIsNone(check_challenge('test', 'bad'))
        self.assertIsNone(check_challenge('test', code))
        cache.clear()
        issue_challenge('test', 'sms', '+639171234567', 'Test')
        code = self.code()
        self.assertIsNotNone(check_challenge('test', code))
        self.assertIsNone(check_challenge('test', code))
        cache.clear()
        issue_challenge('test', 'sms', '+639171234567', 'Test')
        pending = cache.get('test'); pending['expires'] = 0; cache.set('test', pending)
        self.assertIsNone(check_challenge('test', self.code()))

    def test_send_throttle_cross_session_and_failure_state(self):
        issue_challenge('one', 'sms', '+639171234567', 'Test')
        with self.assertRaises(ValidationError):
            issue_challenge('two', 'sms', '+639171234567', 'Test')
        with override_settings(SMS_DELIVERY_BACKEND='invalid.backend'):
            self.assertFalse(send_message('sms', '+639181234567', 'Test', 'Hello'))
        self.assertEqual(SMSMessageLog.objects.order_by('-pk').first().status, 'failed')

    def test_existing_user_can_verify_phone_and_choose_sms(self):
        user = User.objects.create_user('existing', 'existing@example.com', 'Password!123')
        self.client.force_login(user)
        url = reverse('verification_contacts')
        self.client.post(url, {'channel': 'sms', 'phone_number': '09171234567'})
        self.assertFalse(AccountSecurity.objects.get(user=user).phone_verified)
        self.client.post(url, {'action': 'verify', 'otp': self.code()})
        security = AccountSecurity.objects.get(user=user)
        self.assertTrue(security.phone_verified)
        self.assertEqual(security.preferred_channel, 'sms')
        self.assertContains(self.client.get(url), 'Verified')

    def test_sms_reset_requires_admin_and_session_then_changes_password(self):
        user = self.staff()
        url = reverse('password_reset_request')
        self.client.post(url, {'channel': 'sms', 'identifier': '09171234567'})
        reset = PasswordResetRequest.objects.get(user=user)
        self.assertEqual(reset.status, 'pending')
        self.assertEqual(SMSMessageLog.objects.count(), 0)
        self.approve(reset)
        code = self.code()
        self.assertNotEqual(code, reset.otp)
        other = Client()
        other.post(url, {'stage': 'verify', 'request_id': reset.pk, 'otp': code})
        self.assertNotIn('password_reset_verified_id', other.session)
        response = self.client.post(url, {'stage': 'verify', 'request_id': reset.pk, 'otp': code})
        self.assertEqual(response.context['step'], 'password')
        response = self.client.post(url, {'stage': 'password', 'request_id': reset.pk, 'new_password1': 'NewStrongPass!945', 'new_password2': 'NewStrongPass!945'})
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db(); reset.refresh_from_db()
        self.assertTrue(user.check_password('NewStrongPass!945'))
        self.assertEqual(reset.status, 'used')
        self.assertEqual(reset.otp, '')

    def test_unverified_phone_cannot_request_reset(self):
        user = self.staff()
        AccountSecurity.objects.filter(user=user).update(phone_verified=False)
        self.client.post(reverse('password_reset_request'), {'channel': 'sms', 'identifier': '09171234567'})
        self.assertFalse(PasswordResetRequest.objects.exists())

    def test_email_reset_and_attempt_limit(self):
        user = self.staff()
        url = reverse('password_reset_request')
        self.client.post(url, {'channel': 'email', 'identifier': user.email})
        reset = PasswordResetRequest.objects.get(user=user)
        self.approve(reset)
        code = self.code(EmailMessageLog)
        for _ in range(5):
            self.client.post(url, {'stage': 'verify', 'request_id': reset.pk, 'otp': 'bad'})
        self.client.post(url, {'stage': 'verify', 'request_id': reset.pk, 'otp': code})
        self.assertNotIn('password_reset_verified_id', self.client.session)

    def test_changed_phone_invalidates_reset(self):
        user = self.staff()
        url = reverse('password_reset_request')
        self.client.post(url, {'channel': 'sms', 'identifier': '09171234567'})
        reset = PasswordResetRequest.objects.get(user=user); self.approve(reset)
        code = self.code()
        AccountSecurity.objects.filter(user=user).update(phone_number='+639181234567')
        self.client.post(url, {'stage': 'verify', 'request_id': reset.pk, 'otp': code})
        self.assertNotIn('password_reset_verified_id', self.client.session)

    def test_sms_admin_restricted_to_superusers(self):
        user = self.staff(); self.client.force_login(user)
        url = reverse('admin:contracts_smsmessagelog_changelist')
        self.assertEqual(self.client.get(url).status_code, 302)
        user.is_superuser = True; user.save()
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_qr_contact_accepts_sms_without_email(self):
        from .approval_views import RequestForm
        form = RequestForm({'name': 'Guest', 'contact_channel': 'sms', 'phone_number': '09171234567', 'reason': 'Review'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone_number'], '+639171234567')

    def test_qr_sms_submission_is_stored_and_visible_to_reviewer(self):
        from .models import Contract, DocumentAccessLink, DocumentAccessRequest
        reviewer = self.staff()
        contract = Contract.objects.create(title='SMS request test', uploaded_by=reviewer)
        link = DocumentAccessLink.objects.create(contract=contract)
        response = self.client.post(reverse('request_document_access', args=[link.token]),
            {'action': 'request', 'name': 'SMS Guest', 'contact_channel': 'sms', 'phone_number': '09181234567', 'reason': 'Review'})
        self.assertEqual(response.status_code, 302)
        entry = DocumentAccessRequest.objects.get(link=link)
        self.assertEqual(entry.phone_number, '+639181234567')
        self.assertEqual(entry.contact_channel, 'sms')
        self.assertEqual(entry.status, 'pending')
        self.client.force_login(reviewer)
        self.assertContains(self.client.get(reverse('document_approval_queue')), '+639181234567')

    def test_successful_email_reset_verification(self):
        user = self.staff()
        url = reverse('password_reset_request')
        self.client.post(url, {'identifier': user.email})
        reset = PasswordResetRequest.objects.get(user=user); self.approve(reset)
        response = self.client.post(url, {'stage': 'verify', 'request_id': reset.pk, 'otp': self.code(EmailMessageLog)})
        self.assertEqual(response.context['step'], 'password')

    def test_expired_reset_code_is_rejected(self):
        user = self.staff()
        url = reverse('password_reset_request')
        self.client.post(url, {'channel': 'sms', 'identifier': '09171234567'})
        reset = PasswordResetRequest.objects.get(user=user); self.approve(reset)
        PasswordResetRequest.objects.filter(pk=reset.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.client.post(url, {'stage': 'verify', 'request_id': reset.pk, 'otp': self.code()})
        self.assertNotIn('password_reset_verified_id', self.client.session)
