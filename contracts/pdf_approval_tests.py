import io
import re
import tempfile
from datetime import timedelta
from pathlib import Path

import fitz
from Crypto.PublicKey import RSA
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Contract, ContractVersion, DocumentAccessLink, DocumentAccessRequest
from .pdf_storage import MAGIC, encrypt_pdf_bytes, decrypt_pdf_bytes, PDFDecryptionError, read_pdf, write_pdf
from .utils import generate_canonical_fingerprint


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EncryptedPDFApprovalTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = tempfile.TemporaryDirectory()
        root = Path(cls.root.name)
        key = RSA.generate(2048)
        (root / 'private.pem').write_bytes(key.export_key())
        (root / 'public.pem').write_bytes(key.publickey().export_key())
        cls.configuration = override_settings(MEDIA_ROOT=str(root / 'media'),
            RSA_PRIVATE_KEY_PATH=str(root / 'private.pem'), RSA_PUBLIC_KEY_PATH=str(root / 'public.pem'))
        cls.configuration.enable()

    @classmethod
    def tearDownClass(cls):
        cls.configuration.disable()
        cls.root.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.staff = User.objects.create_user('reviewer', is_staff=True)
        self.admin = User.objects.create_user('approver', is_staff=True, is_superuser=True)
        self.other = User.objects.create_user('reader')
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'Confidential contract content')
            self.pdf = pdf.tobytes()
        self.contract = Contract.objects.create(title='Confidential title', uploaded_by=self.staff)
        self.contract.file.save('protected.pdf', ContentFile(self.pdf))
        self.version = ContractVersion.objects.create(contract=self.contract, version_number=1,
            file=self.contract.file.name, created_by=self.staff)
        self.link = DocumentAccessLink.objects.create(contract=self.contract)
        self.url = reverse('request_document_access', args=[self.link.token])
        self.view_url = reverse('approved_document_pdf', args=[self.link.token])

    def request_and_verify(self):
        response = self.client.post(self.url, {'action': 'request', 'name': 'Guest',
            'email': 'guest@example.com', 'reason': 'Review the agreement', 'organization': 'Example'})
        self.assertEqual(response.status_code, 302)
        entry = DocumentAccessRequest.objects.latest('created_at')
        self.assertEqual(entry.status, 'pending')
        self.assertIsNone(entry.email_verified_at)
        self.assertEqual(entry.otp_hash, '')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.get(self.view_url).status_code, 404)
        return entry

    def test_qr_request_form_is_shown_even_with_existing_document_access(self):
        for public in (False, True):
            self.contract.is_public = public
            self.contract.save()
            for user in (None, self.staff):
                self.client.logout()
                if user:
                    self.client.force_login(user)
                response = self.client.get(self.url)
                if public:
                    self.assertContains(response, 'This is a public document')
                    self.assertContains(response, 'No account, sign-in, or access request is needed')
                    self.assertContains(response, 'id="approved-view-button"')
                    self.assertNotContains(response, 'Submit access request')
                else:
                    self.assertContains(response, 'Full name')
                    self.assertContains(response, 'Submit access request')
                    self.assertNotContains(response, 'id="approved-view-button"')
                self.assertNotContains(response, 'You already have access')
                self.assertEqual(self.client.get(self.view_url).status_code, 404)

    def test_submission_lock_is_retryable_and_does_not_approve(self):
        from unittest.mock import patch
        from django.db import OperationalError
        data = {'action': 'request', 'name': 'Guest', 'email': 'guest@example.com', 'reason': 'Review'}
        with patch('django.db.models.query.QuerySet.update', side_effect=OperationalError('database is locked')):
            response = self.client.post(self.url, data)
        self.assertContains(response, 'Please submit your request again shortly')
        self.assertFalse(DocumentAccessRequest.objects.exists())
        self.link.refresh_from_db()
        self.assertTrue(self.link.is_active)
        self.assertEqual(self.client.get(self.view_url).status_code, 404)
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        entry = DocumentAccessRequest.objects.get(link=self.link)
        self.assertEqual(entry.status, 'pending')
        reviewer = Client()
        reviewer.force_login(self.staff)
        self.assertContains(reviewer.get(reverse('document_approval_queue')), 'guest@example.com')

    def test_approved_request_uses_embedded_panel_and_scoped_view_url(self):
        entry = self.request_and_verify()
        self.approve(entry)
        response = self.client.get(self.url)
        self.assertContains(response, 'id="approved-viewer"')
        self.assertContains(response, 'id="approved-view-button"')
        self.assertContains(response, self.view_url)
        self.assertNotContains(response, reverse('preview_contract', args=[self.contract.pk]))

    def approve(self, entry):
        reviewer = Client()
        reviewer.force_login(self.admin)
        self.assertContains(reviewer.get(reverse('document_approval_queue')), 'guest@example.com')
        response = reviewer.post(reverse('review_document_request', args=[entry.pk]), {'action': 'approve', 'note': 'Checked requester details', 'duration': '24', 'unit': 'hours'})
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        return reviewer

    def test_storage_is_encrypted_and_round_trip_is_exact(self):
        raw = Path(self.contract.file.path).read_bytes()
        self.assertTrue(raw.startswith(MAGIC))
        self.assertNotIn(b'%PDF-', raw)
        self.assertEqual(decrypt_pdf_bytes(raw), self.pdf)
        self.assertNotEqual(encrypt_pdf_bytes(self.pdf), encrypt_pdf_bytes(self.pdf))
        with Contract.objects.get(pk=self.contract.pk).file.open('rb') as document:
            self.assertEqual(document.read(), self.pdf)

    def test_tampering_wrong_key_and_plaintext_fail_closed(self):
        raw = Path(self.contract.file.path).read_bytes()
        for changed in [raw[:-1] + bytes([raw[-1] ^ 1]), raw[:15], raw[:20] + bytes([raw[20] ^ 1]) + raw[21:], self.pdf]:
            with self.assertRaises(PDFDecryptionError):
                decrypt_pdf_bytes(changed)
        other_key = Path(self.root.name) / 'other-key.pem'
        other_key.write_bytes(RSA.generate(2048).export_key())
        with override_settings(RSA_PRIVATE_KEY_PATH=str(other_key)):
            with self.assertRaises(PDFDecryptionError):
                decrypt_pdf_bytes(raw)

    def test_authorized_preview_decrypts_without_changing_disk(self):
        before = Path(self.contract.file.path).read_bytes()
        self.client.force_login(self.staff)
        response = self.client.get(reverse('preview_contract', args=[self.contract.pk]))
        self.assertEqual(b''.join(response.streaming_content), self.pdf)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(Path(self.contract.file.path).read_bytes(), before)

    def test_corrupt_storage_returns_safe_error(self):
        path = Path(self.contract.file.path)
        before = path.read_bytes()
        path.write_bytes(before[:-1] + bytes([before[-1] ^ 1]))
        self.client.force_login(self.staff)
        response = self.client.get(reverse('preview_contract', args=[self.contract.pk]))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(self.pdf, response.content)
        path.write_bytes(before)

    def test_skip_sealing_still_encrypts_upload_and_preserves_fingerprint(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('upload_contract'), {'title': 'Fresh upload', 'skip_encryption': '1',
            'file': SimpleUploadedFile('fresh.pdf', self.pdf, content_type='application/pdf')})
        self.assertEqual(response.status_code, 302)
        contract = Contract.objects.get(title='Fresh upload')
        self.assertTrue(Path(contract.file.path).read_bytes().startswith(MAGIC))
        self.assertEqual(contract.fingerprint, generate_canonical_fingerprint(contract.file.path))

    def test_complete_guest_approval_and_session_binding(self):
        entry = self.request_and_verify()
        self.approve(entry)
        self.assertEqual(Client().get(self.view_url).status_code, 404)
        response = self.client.get(self.view_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), self.pdf)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.get(reverse('download_contract', args=[self.contract.pk])).status_code, 302)

    def test_grant_expires_and_can_be_revoked(self):
        entry = self.request_and_verify()
        reviewer = self.approve(entry)
        entry.expires_at = timezone.now() - timedelta(seconds=1)
        entry.save()
        self.assertEqual(self.client.get(self.view_url).status_code, 404)
        entry.expires_at = timezone.now() + timedelta(hours=1)
        entry.save()
        reviewer.post(reverse('review_document_request', args=[entry.pk]), {'action': 'revoke', 'note': 'Checked requester details'})
        self.assertEqual(self.client.get(self.view_url).status_code, 404)

    def test_approver_is_kept_after_another_staff_member_revokes(self):
        entry = self.request_and_verify()
        self.approve(entry)
        self.assertEqual(entry.approved_by, self.admin)
        self.assertIsNotNone(entry.approved_at)

        second = User.objects.create_user('second-reviewer', is_staff=True, is_superuser=True)
        revoker = Client()
        revoker.force_login(second)
        revoker.post(reverse('review_document_request', args=[entry.pk]), {'action': 'revoke', 'note': 'Checked requester details'})
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'revoked')
        self.assertEqual(entry.approved_by, self.admin)
        self.assertEqual(entry.reviewed_by, second)

        queue = revoker.get(reverse('document_approval_queue'), {'status': 'revoked'})
        self.assertContains(queue, 'Approved by <strong>approver</strong>')
        self.assertContains(queue, 'Revoked by <strong>second-reviewer</strong>')

    def test_approval_does_not_expose_future_revision(self):
        entry = self.request_and_verify()
        self.approve(entry)
        self.contract.file.save('future.pdf', ContentFile(b'%PDF-1.4 future revision'))
        ContractVersion.objects.create(contract=self.contract, version_number=2, file=self.contract.file.name)
        response = self.client.get(self.view_url)
        self.assertEqual(b''.join(response.streaming_content), self.pdf)

    def test_reader_cannot_approve_or_fetch_queue_or_sheet(self):
        entry = self.request_and_verify()
        reader = Client()
        reader.force_login(self.other)
        for url in [reverse('document_approval_queue'), reverse('document_access_sheet', args=[self.contract.pk])]:
            self.assertEqual(reader.get(url).status_code, 404)
        self.assertEqual(reader.post(reverse('review_document_request', args=[entry.pk]), {'action': 'approve', 'note': 'Checked requester details'}).status_code, 404)

    def test_reviewer_can_approve_submitted_request_and_get_is_read_only(self):
        entry = self.request_and_verify()
        reviewer = Client()
        reviewer.force_login(self.admin)
        url = reverse('review_document_request', args=[entry.pk])
        self.assertEqual(reviewer.get(url).status_code, 405)
        self.assertEqual(reviewer.post(url, {'action': 'approve', 'note': 'Checked requester details'}).status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'approved')

    def test_staff_can_read_queue_but_only_admin_decides(self):
        entry = self.request_and_verify()
        staff = Client()
        staff.force_login(self.staff)
        queue = staff.get(reverse('document_approval_queue'))
        self.assertContains(queue, 'guest@example.com')
        self.assertContains(queue, 'Only an administrator can approve')
        self.assertNotContains(queue, 'Approve viewing')
        url = reverse('review_document_request', args=[entry.pk])
        for action in ('approve', 'reject'):
            self.assertEqual(staff.post(url, {'action': action, 'note': 'Looks fine', 'duration': '24', 'unit': 'hours'}).status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'pending')

    def test_decision_requires_a_reason(self):
        entry = self.request_and_verify()
        reviewer = Client()
        reviewer.force_login(self.admin)
        url = reverse('review_document_request', args=[entry.pk])
        for note in ('', '   '):
            response = reviewer.post(url, {'action': 'approve', 'note': note, 'duration': '24', 'unit': 'hours'}, follow=True)
            self.assertContains(response, 'Add a note explaining your decision')
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'pending')
        reviewer.post(url, {'action': 'reject', 'note': 'Requester could not be identified'})
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'rejected')
        self.assertEqual(entry.review_note, 'Requester could not be identified')
        self.assertContains(self.client.get(self.url), 'Requester could not be identified')

    def test_admin_sets_any_duration_within_bounds(self):
        entry = self.request_and_verify()
        reviewer = Client()
        reviewer.force_login(self.admin)
        url = reverse('review_document_request', args=[entry.pk])
        queue = reviewer.get(reverse('document_approval_queue'))
        self.assertContains(queue, 'name="duration"')
        self.assertContains(queue, 'data-duration="15" data-unit="minutes"')
        rejected = [('10', 'minutes'), ('4', 'days'), ('0', 'hours'), ('-5', 'hours'), ('abc', 'hours'),
                    ('2', 'weeks'), ('99999999999999', 'days')]
        for duration, unit in rejected:
            response = reviewer.post(url, {'action': 'approve', 'note': 'Checked', 'duration': duration, 'unit': unit}, follow=True)
            self.assertContains(response, 'between 15 minutes and 3 days')
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'pending')
        before = timezone.now()
        reviewer.post(url, {'action': 'approve', 'note': 'Checked', 'duration': '90', 'unit': 'minutes'})
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'approved')
        self.assertAlmostEqual((entry.expires_at - before).total_seconds(), 90 * 60, delta=30)

    def test_submission_preserves_single_use_qr(self):
        data = {'action': 'request', 'name': 'Guest', 'email': 'guest@example.com', 'reason': 'Review'}
        self.client.post(self.url, data)
        for _ in range(5):
            response = self.client.post(self.url, data)
        self.assertContains(response, 'already been used')
        self.assertEqual(DocumentAccessRequest.objects.count(), 1)
        self.assertEqual(DocumentAccessRequest.objects.get().status, 'pending')
        self.assertEqual(self.client.get(self.view_url).status_code, 404)

    def test_qr_sheet_contains_no_confidential_pdf_content(self):
        self.client.force_login(self.staff)
        with override_settings(PUBLIC_BASE_URL='https://sealguard.example'):
            response = self.client.get(reverse('document_access_sheet', args=[self.contract.pk]))
        data = b''.join(response.streaming_content)
        with fitz.open(stream=data, filetype='pdf') as sheet:
            text = ''.join(page.get_text() for page in sheet)
            self.assertIn('Scan to request access', text)
            self.assertIn('https://sealguard.example/document-access/', text)
            self.assertNotIn('Confidential', text)
            self.assertTrue(sheet[0].get_images())

    def test_guest_page_does_not_disclose_private_title(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Confidential title')

    def test_rejection_does_not_grant_access(self):
        entry = self.request_and_verify()
        reviewer = Client()
        reviewer.force_login(self.admin)
        reviewer.post(reverse('review_document_request', args=[entry.pk]), {'action': 'reject', 'note': 'Checked requester details'})
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'rejected')
        self.assertEqual(self.client.get(self.view_url).status_code, 404)

    def test_csrf_is_required_for_email_and_approval_actions(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(self.url, {'action': 'request'}).status_code, 403)
        entry = self.request_and_verify()
        strict.force_login(self.staff)
        self.assertEqual(strict.post(reverse('review_document_request', args=[entry.pk]), {'action': 'approve', 'note': 'Checked requester details'}).status_code, 403)

    def test_submission_does_not_depend_on_email_delivery(self):
        from unittest.mock import patch
        with patch('contracts.approval_views.send_mail', side_effect=OSError('Unavailable')) as sender:
            response = self.client.post(self.url, {'action': 'request', 'name': 'Guest', 'email': 'guest@example.com', 'reason': 'Review'})
        self.assertEqual(response.status_code, 302)
        sender.assert_not_called()
        self.assertEqual(DocumentAccessRequest.objects.get().status, 'pending')
        self.assertEqual(self.client.get(self.view_url).status_code, 404)

    def test_migration_is_resumable_and_preserves_plaintext_bytes(self):
        from django.conf import settings
        legacy = Path(settings.MEDIA_ROOT) / 'legacy.pdf'
        legacy.write_bytes(self.pdf)
        output = io.StringIO()
        call_command('encrypt_pdf_storage', stdout=output)
        self.assertEqual(legacy.read_bytes(), self.pdf)
        call_command('encrypt_pdf_storage', apply=True, stdout=output)
        after = legacy.read_bytes()
        self.assertTrue(after.startswith(MAGIC))
        self.assertEqual(read_pdf(legacy), self.pdf)
        call_command('encrypt_pdf_storage', apply=True, stdout=output)
        self.assertEqual(legacy.read_bytes(), after)

    def test_public_document_still_decrypts_for_anonymous_view(self):
        self.contract.is_public = True
        self.contract.save()
        response = self.client.get(reverse('preview_contract', args=[self.contract.pk]))
        self.assertEqual(b''.join(response.streaming_content), self.pdf)
        self.assertTrue(Path(self.contract.file.path).read_bytes().startswith(MAGIC))

    def test_sealed_pdf_contains_clickable_verification_link(self):
        from .utils import stamp_seal_on_pdf
        from PIL import Image
        seal = Path(self.root.name) / 'seal.png'
        Image.new('RGBA', (120, 120), (30, 95, 85, 255)).save(seal)
        output = Path(self.root.name) / 'linked.pdf'
        url = 'https://sealguard.example/verify/'
        stamp_seal_on_pdf(self.contract.file.path, output, str(seal), verify_url=url)
        with fitz.open(output) as document:
            links = [link.get('uri') for page in document for link in page.get_links()]
            text = ''.join(page.get_text() for page in document)
        self.assertIn(url, links)
        self.assertIn('sealguard.example/verify', text)

    def test_encrypted_downloads_reopen_only_for_authorized_user(self):
        from .encrypted_documents import PACKAGE_MAGIC
        self.client.force_login(self.staff)
        for route, pk in [('download_contract', self.contract.pk), ('download_contract_version', self.version.pk)]:
            response = self.client.get(reverse(route, args=[pk]))
            data = b''.join(response.streaming_content)
            self.assertTrue(data.startswith(PACKAGE_MAGIC))
            self.assertNotIn(self.pdf, data)
            self.assertIn('.sgpdf', response['Content-Disposition'])
            self.assertIn('attachment', response['Content-Disposition'])
            def upload(client, payload=data):
                return client.post(reverse('open_encrypted_document'), {
                    'document': SimpleUploadedFile('download.sgpdf', payload)})
            opened = upload(self.client)
            self.assertEqual(opened.status_code, 200)
            self.assertContains(opened, 'encrypted-viewer-frame')
            self.assertContains(opened, 'hide_sidebar')
            reader = Client()
            reader.force_login(self.other)
            self.assertContains(upload(reader), 'Unable to open this file')
            self.assertContains(upload(Client()), 'Unable to open this file')
            self.assertContains(upload(self.client, data[:-1] + bytes([data[-1] ^ 1])), 'Unable to open this file')
            self.assertContains(upload(self.client, self.pdf), 'Unable to open this file')

    def test_bulk_zip_contains_only_encrypted_packages(self):
        import zipfile
        from .encrypted_documents import PACKAGE_MAGIC
        self.client.force_login(self.staff)
        response = self.client.post(reverse('bulk_download_contracts'), {'ids': [self.contract.pk]})
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertTrue(archive.namelist())
            for name in archive.namelist():
                self.assertTrue(name.endswith('.sgpdf'))
                self.assertTrue(archive.read(name).startswith(PACKAGE_MAGIC))

    def test_guest_package_reopening_requires_live_approval(self):
        entry = self.request_and_verify()
        reviewer = self.approve(entry)
        response = self.client.get(self.view_url + '?download=encrypted')
        package = b''.join(response.streaming_content)
        def reopen():
            return self.client.post(reverse('open_encrypted_document'), {
                'document': SimpleUploadedFile('guest.sgpdf', package)})
        self.assertEqual(reopen().status_code, 200)
        reviewer.post(reverse('review_document_request', args=[entry.pk]), {'action': 'revoke', 'note': 'Checked requester details'})
        self.assertContains(reopen(), 'Unable to open this file')
        self.assertEqual(self.client.get(self.view_url + '?download=encrypted').status_code, 404)

    def test_browser_navigation_uses_internal_viewer(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('preview_contract', args=[self.contract.pk]), HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, 302)
        self.assertIn('mobile-pdf.html?file=', response.url)

    def test_package_permission_is_checked_before_decryption_and_again_after_revocation(self):
        from unittest.mock import patch
        from .encrypted_documents import package_bytes
        package = package_bytes(self.contract, self.contract.file, self.version)
        self.client.force_login(self.other)
        def reopen():
            return self.client.post(reverse('open_encrypted_document'), {
                'document': SimpleUploadedFile('document.sgpdf', package)})
        with patch('contracts.encrypted_documents.decrypt_pdf_bytes') as decrypt:
            self.assertContains(reopen(), 'Unable to open this file')
            decrypt.assert_not_called()
        self.contract.collaborators.add(self.other)
        self.assertEqual(reopen().status_code, 200)
        self.assertContains(reopen(), 'encrypted-viewer-frame')
        self.contract.collaborators.remove(self.other)
        self.assertContains(reopen(), 'Unable to open this file')

    def test_encrypted_upload_requires_csrf(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse('open_encrypted_document')).status_code, 403)


    def sgpdf_request(self):
        from .encrypted_documents import package_bytes
        self.package = package_bytes(self.contract, self.version.file, self.version)
        response = self.client.post(reverse('open_encrypted_document'), {
            'document': SimpleUploadedFile('agreement.sgpdf', self.package)}, REMOTE_ADDR='203.0.113.9')
        self.assertContains(response, 'Request PDF access')
        link = DocumentAccessLink.objects.get(category='sgpdf')
        url = reverse('request_document_access', args=[link.token])
        self.assertEqual(self.client.post(url, {'action': 'request', 'name': 'Public requester',
            'email': 'public@example.com', 'reason': 'Please review'}, REMOTE_ADDR='203.0.113.9').status_code, 302)
        return DocumentAccessRequest.objects.get(link=link)

    def test_sgpdf_public_request_exact_version_preview_and_audit(self):
        from .models import AuditLog
        entry = self.sgpdf_request()
        self.assertEqual(entry.ip_address, '203.0.113.9')
        self.assertTrue(AuditLog.objects.filter(verification_result='Unauthorized decryption').exists())
        preview = reverse('review_request_pdf', args=[entry.pk])
        self.assertEqual(self.client.get(preview).status_code, 302)
        reader = Client()
        reader.force_login(self.other)
        self.assertEqual(reader.get(preview).status_code, 404)
        reviewer = Client()
        reviewer.force_login(self.admin)
        response = reviewer.get(preview)
        self.assertEqual(b''.join(response.streaming_content), self.pdf)
        self.assertContains(reviewer.get(reverse('document_approval_queue')), 'SGPDF decryption request')
        self.assertContains(reviewer.get(reverse('document_approval_queue')), '203.0.113.9')
        # A later revision must not replace the version the guest requested.
        self.contract.file.save('later.pdf', ContentFile(self.pdf))
        ContractVersion.objects.create(contract=self.contract, version_number=2, file=self.contract.file.name)
        reviewer.post(reverse('review_document_request', args=[entry.pk]), {'action': 'approve', 'note': 'Checked requester details', 'duration': '1', 'unit': 'hours'})
        entry.refresh_from_db()
        self.assertEqual(entry.approved_version_id, self.version.pk)
        view = reverse('approved_document_pdf', args=[entry.link.token])
        self.assertEqual(self.client.get(view).status_code, 200)
        self.assertEqual(Client().get(view).status_code, 404)
        self.assertTrue(AuditLog.objects.filter(action='approved', note__contains=str(entry.pk)).exists())
        self.assertTrue(AuditLog.objects.filter(action='viewed', note__contains=str(entry.pk)).count() >= 2)
        reviewer.post(reverse('review_document_request', args=[entry.pk]), {'action': 'revoke', 'note': 'Checked requester details'})
        self.assertEqual(self.client.get(view).status_code, 404)

    def test_sgpdf_rejection_and_modified_ciphertext_block_access(self):
        from .models import AuditLog
        entry = self.sgpdf_request()
        reviewer = Client()
        reviewer.force_login(self.admin)
        preview = reverse('review_request_pdf', args=[entry.pk])
        Path(self.version.file.path).write_bytes(b'damaged ciphertext')
        self.assertEqual(reviewer.get(preview).status_code, 404)
        review = reverse('review_document_request', args=[entry.pk])
        self.assertEqual(reviewer.post(review, {'action': 'approve', 'note': 'Checked requester details'}).status_code, 404)
        self.assertEqual(reviewer.post(review, {'action': 'reject', 'note': 'Checked requester details'}).status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action='rejected', note__contains=str(entry.pk)).exists())
        self.assertEqual(self.client.get(reverse('approved_document_pdf', args=[entry.link.token])).status_code, 404)

    def test_verify_redirects_encrypted_extension_and_magic(self):
        from .encrypted_documents import package_bytes
        for name in ('agreement.sgpdf', 'renamed.pdf'):
            response = self.client.post(reverse('public_verify'), {'pdf_file': SimpleUploadedFile(
                name, package_bytes(self.contract, self.version.file, self.version))}, follow=True)
            self.assertContains(response, 'Encrypted documents')
            self.assertEqual(response.context['filename_hint'], 'encrypted')
            self.assertNotIn('verify_preview_token', self.client.session)
