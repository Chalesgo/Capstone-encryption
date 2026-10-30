import re
import tempfile
from pathlib import Path
from unittest.mock import patch

from Crypto.PublicKey import RSA
from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .access import RESIDENT_GROUP, is_resident
from .models import AccountSecurity, AuditLog, Contract, PasswordResetRequest


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    N8N_EMAIL_WEBHOOK_URL='',
)
class ResidentAccountTests(TestCase):
    PASSWORD = 'Resident-pass-2026!'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = tempfile.TemporaryDirectory()
        root = Path(cls.root.name)
        key = RSA.generate(2048)
        (root / 'private.pem').write_bytes(key.export_key())
        (root / 'public.pem').write_bytes(key.publickey().export_key())
        cls.configuration = override_settings(
            MEDIA_ROOT=str(root / 'media'),
            RSA_PRIVATE_KEY_PATH=str(root / 'private.pem'),
            RSA_PUBLIC_KEY_PATH=str(root / 'public.pem'),
        )
        cls.configuration.enable()

    @classmethod
    def tearDownClass(cls):
        cls.configuration.disable()
        cls.root.cleanup()
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser('resident_admin', 'admin@example.com', self.PASSWORD)
        self.published = self.make_contract('Published supply contract', status='final', is_public=True)
        self.draft = self.make_contract('Draft supply contract')

    def make_contract(self, title, status='pending', is_public=False):
        return Contract.objects.create(
            title=title, status=status, is_public=is_public, uploaded_by=self.admin,
            file=SimpleUploadedFile(f'{title}.pdf', b'%PDF-1.7\nResident test fixture', content_type='application/pdf'),
        )

    def make_resident(self, email='juan@example.com'):
        user = User.objects.create_user(email, email, self.PASSWORD, first_name='Juan', last_name='Dela Cruz')
        user.groups.add(Group.objects.get_or_create(name=RESIDENT_GROUP)[0])
        AccountSecurity.objects.create(user=user, email_verified=True)
        client = Client()
        client.force_login(user)
        return user, client

    def register_details(self, client, email='maria@example.com', password=None):
        return client.post(reverse('resident_register'), {
            'first_name': 'Maria', 'last_name': 'Santos', 'email': email,
            'password1': password or self.PASSWORD, 'password2': password or self.PASSWORD,
        })

    def test_registration_confirms_email_then_creates_resident_account(self):
        client = Client()
        response = self.register_details(client, email='Maria@Example.com')
        self.assertContains(response, 'Check your EMAIL')
        self.assertFalse(User.objects.filter(email__iexact='maria@example.com').exists())
        code = re.search(r'\b[0-9]{6}\b', mail.outbox[-1].body).group()

        response = client.post(reverse('resident_register'), {'otp': code})
        self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
        user = User.objects.get(username='maria@example.com')
        self.assertTrue(is_resident(user))
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertTrue(user.check_password(self.PASSWORD))
        self.assertEqual(user.get_full_name(), 'Maria Santos')
        self.assertTrue(AccountSecurity.objects.get(user=user).email_verified)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_registration_rejects_wrong_code_existing_email_and_weak_password(self):
        client = Client()
        self.register_details(client)
        response = client.post(reverse('resident_register'), {'otp': '000000'})
        self.assertContains(response, 'That code is invalid or expired')
        self.assertFalse(User.objects.filter(username='maria@example.com').exists())

        self.make_resident('taken@example.com')
        cache.clear()
        response = self.register_details(Client(), email='TAKEN@example.com')
        self.assertContains(response, 'An account with this email already exists')
        response = self.register_details(Client(), email='weak@example.com', password='password')
        self.assertContains(response, 'This password is too common')

    def test_unconfirmed_email_delivery_keeps_the_code_active_without_an_error(self):
        client = Client()
        with patch('contracts.messaging.send_message', return_value=False):
            response = self.register_details(client)
        self.assertContains(response, 'Check your EMAIL')
        self.assertNotContains(response, 'Delivery failed')

    def test_resident_reaches_only_verification_pages(self):
        _user, client = self.make_resident()
        for name in ('public_verify', 'verify_physical', 'open_encrypted_document'):
            self.assertEqual(client.get(reverse(name)).status_code, 200, name)
        blocked = [
            reverse('contract_list'), reverse('dashboard'), reverse('upload_contract'),
            reverse('help_tutorials'), reverse('trash_list'), reverse('document_approval_queue'),
            reverse('notification_list'), reverse('verification_contacts'),
            reverse('contract_version_history', args=[self.published.pk]),
            reverse('download_contract_version', args=[1]), '/admin/',
        ]
        for url in blocked:
            response = client.get(url)
            self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False, msg_prefix=url)
        response = client.post(reverse('bulk_update_status'), {'ids': [self.published.pk], 'status': 'pending'})
        self.assertEqual(response.status_code, 403)
        self.published.refresh_from_db()
        self.assertEqual(self.published.status, 'final')
        self.assertRedirects(client.get('/'), reverse('public_verify'), fetch_redirect_response=False)

    def test_resident_navigation_hides_the_staff_workspace(self):
        _user, client = self.make_resident()
        page = client.get(reverse('public_verify'))
        self.assertNotContains(page, 'data-tooltip="Procurement Documents"')
        self.assertNotContains(page, 'data-tooltip="Dashboard"')
        self.assertNotContains(page, 'href="/upload/"')
        self.assertContains(page, 'Signed in as Juan Dela Cruz')

    def test_resident_views_and_downloads_published_documents_with_audit_trail(self):
        user, client = self.make_resident()
        preview = client.get(reverse('preview_contract', args=[self.published.pk]))
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(user=user, action='viewed', contract=self.published).exists())

        download = client.get(reverse('download_contract', args=[self.published.pk]) + '?public=1')
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment', download['Content-Disposition'])
        self.assertTrue(b''.join(download.streaming_content).startswith(b'%PDF'))
        self.assertTrue(AuditLog.objects.filter(user=user, action='downloaded', contract=self.published).exists())

    def test_browser_navigation_to_a_pdf_is_logged_once(self):
        user, client = self.make_resident()
        url = reverse('preview_contract', args=[self.published.pk])
        client.get(url, HTTP_ACCEPT='text/html', HTTP_SEC_FETCH_MODE='navigate')
        client.get(url)
        self.assertEqual(AuditLog.objects.filter(user=user, action='viewed').count(), 1)

    def test_resident_cannot_open_unpublished_documents(self):
        _user, client = self.make_resident()
        self.assertEqual(client.get(reverse('preview_contract', args=[self.draft.pk])).status_code, 404)
        self.assertEqual(client.get(reverse('download_contract', args=[self.draft.pk])).status_code, 404)
        # Unpublishing takes effect immediately.
        self.published.status = 'pending'
        self.published.is_public = False
        self.published.save()
        self.assertEqual(client.get(reverse('preview_contract', args=[self.published.pk])).status_code, 404)

    def test_anonymous_visitors_sign_in_to_view_published_documents(self):
        login_url = f"{reverse('login')}?next={reverse('public_verify')}"
        for url in (reverse('preview_contract', args=[self.published.pk]),
                    reverse('download_contract', args=[self.published.pk]) + '?public=1'):
            self.assertRedirects(self.client.get(url), login_url, fetch_redirect_response=False)
        page = self.client.get(reverse('public_verify'))
        self.assertContains(page, f'href="{reverse("resident_register")}">Create account</a>')
        self.assertContains(page, 'Verified Documents <span class="doc-panel-count">1</span>')
        self.assertEqual(self.client.get(reverse('verify_physical')).status_code, 200)

    def test_staff_and_read_only_workspace_users_are_not_restricted(self):
        staff = User.objects.create_user('staff_member', password=self.PASSWORD, is_staff=True)
        workspace_reader = User.objects.create_user('workspace_reader', password=self.PASSWORD)
        for user in (staff, workspace_reader):
            client = Client()
            client.force_login(user)
            self.assertEqual(client.get(reverse('contract_list')).status_code, 200, user.username)

    def test_resident_resets_password_by_email_without_admin_approval(self):
        user, _client = self.make_resident()
        client = Client()
        url = reverse('password_reset_request')
        response = client.post(url, {'channel': 'email', 'identifier': 'JUAN@example.com'})
        self.assertContains(response, 'We sent a six-digit code to your email')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['juan@example.com'])
        code = re.search(r'\b[0-9]{6}\b', mail.outbox[0].body).group()
        reset = PasswordResetRequest.objects.get(user=user)
        self.assertEqual(reset.status, 'approved')

        response = client.post(url, {'stage': 'verify', 'request_id': reset.pk, 'otp': code})
        self.assertContains(response, 'Set a new password')
        new_password = 'Another-resident-pass-2026!'
        response = client.post(url, {'stage': 'password', 'request_id': reset.pk,
                                     'new_password1': new_password, 'new_password2': new_password})
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.check_password(new_password))
        reset.refresh_from_db()
        self.assertEqual(reset.status, 'used')

    def test_resident_reset_rejects_wrong_code_and_throttles_new_codes(self):
        user, _client = self.make_resident()
        client = Client()
        url = reverse('password_reset_request')
        client.post(url, {'channel': 'email', 'identifier': 'juan@example.com'})
        reset = PasswordResetRequest.objects.get(user=user)
        response = client.post(url, {'stage': 'verify', 'request_id': reset.pk, 'otp': '000000'})
        self.assertContains(response, 'That verification code is invalid or expired')
        # A second request within a minute keeps the code already sent.
        response = client.post(url, {'channel': 'email', 'identifier': 'juan@example.com'})
        self.assertContains(response, 'Enter your verification code')
        self.assertEqual(len(mail.outbox), 1)
        response = Client().post(url, {'channel': 'email', 'identifier': 'juan@example.com'})
        self.assertContains(response, 'Please wait one minute')

    def test_staff_and_unknown_emails_keep_the_admin_approval_flow(self):
        staff = User.objects.create_user('reset_staff', 'staff@example.com', self.PASSWORD, is_staff=True)
        url = reverse('password_reset_request')
        for identifier in ('staff@example.com', 'nobody@example.com'):
            cache.clear()
            response = Client().post(url, {'channel': 'email', 'identifier': identifier})
            self.assertContains(response, 'waiting for administrator approval')
            self.assertNotContains(response, 'We sent a six-digit code')
        self.assertEqual(PasswordResetRequest.objects.get(user=staff).status, 'pending')

    def test_admin_dashboard_reports_resident_activity(self):
        _user, client = self.make_resident()
        client.get(reverse('preview_contract', args=[self.published.pk]))
        client.get(reverse('download_contract', args=[self.published.pk]) + '?public=1')
        admin = Client()
        admin.force_login(self.admin)
        stats = admin.get(reverse('dashboard')).context['resident_stats']
        self.assertEqual((stats['accounts'], stats['views'], stats['downloads']), (1, 1, 1))
        self.assertEqual(stats['most_viewed_title'], 'Published supply contract')
        staff = User.objects.create_user('dashboard_staff', password=self.PASSWORD, is_staff=True)
        staff_client = Client()
        staff_client.force_login(staff)
        self.assertIsNone(staff_client.get(reverse('dashboard')).context['resident_stats'])
