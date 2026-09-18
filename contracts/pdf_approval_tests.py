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
        code = re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)
        entry = DocumentAccessRequest.objects.latest('created_at')
        self.assertNotEqual(entry.otp_hash, code)
        self.assertEqual(self.client.get(self.view_url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'action': 'verify', 'code': code}).status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'pending')
        self.assertEqual(entry.otp_hash, '')
        return entry

    def approve(self, entry):
        reviewer = Client()
        reviewer.force_login(self.staff)
        self.assertContains(reviewer.get(reverse('document_approval_queue')), 'guest@example.com')
        response = reviewer.post(reverse('review_document_request', args=[entry.pk]), {'action': 'approve', 'hours': '24'})
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
        reviewer.post(reverse('review_document_request', args=[entry.pk]), {'action': 'revoke'})
        self.assertEqual(self.client.get(self.view_url).status_code, 404)

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
        self.assertEqual(reader.post(reverse('review_document_request', args=[entry.pk]), {'action': 'approve'}).status_code, 404)

    def test_reviewer_cannot_approve_unverified_email_and_get_is_read_only(self):
        self.client.post(self.url, {'action': 'request', 'name': 'Guest', 'email': 'guest@example.com', 'reason': 'Review'})
        entry = DocumentAccessRequest.objects.get()
        reviewer = Client()
        reviewer.force_login(self.staff)
        url = reverse('review_document_request', args=[entry.pk])
        self.assertEqual(reviewer.get(url).status_code, 405)
        self.assertEqual(reviewer.post(url, {'action': 'approve'}).status_code, 404)

    def test_code_attempt_limit_and_single_use_qr(self):
        data = {'action': 'request', 'name': 'Guest', 'email': 'guest@example.com', 'reason': 'Review'}
        self.client.post(self.url, data)
        code = re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)
        for _ in range(5):
            self.client.post(self.url, {'action': 'verify', 'code': 'invalid'})
        self.client.post(self.url, {'action': 'verify', 'code': code})
        self.assertEqual(DocumentAccessRequest.objects.get().status, 'email_pending')
        for _ in range(5):
            response = self.client.post(self.url, data)
        self.assertContains(response, 'already been used')
        self.assertEqual(DocumentAccessRequest.objects.count(), 1)

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

    def test_expired_code_and_rejection_do_not_grant_access(self):
        self.client.post(self.url, {'action': 'request', 'name': 'Guest', 'email': 'guest@example.com', 'reason': 'Review'})
        entry = DocumentAccessRequest.objects.get()
        code = re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)
        entry.otp_expires_at = timezone.now() - timedelta(seconds=1)
        entry.save()
        self.client.post(self.url, {'action': 'verify', 'code': code})
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'email_pending')
        entry.otp_expires_at = timezone.now() + timedelta(minutes=1)
        entry.save()
        self.client.post(self.url, {'action': 'verify', 'code': code})
        reviewer = Client()
        reviewer.force_login(self.staff)
        reviewer.post(reverse('review_document_request', args=[entry.pk]), {'action': 'reject'})
        self.assertEqual(self.client.get(self.view_url).status_code, 404)

    def test_csrf_is_required_for_email_and_approval_actions(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(self.url, {'action': 'request'}).status_code, 403)
        entry = self.request_and_verify()
        strict.force_login(self.staff)
        self.assertEqual(strict.post(reverse('review_document_request', args=[entry.pk]), {'action': 'approve'}).status_code, 403)

    def test_email_failure_shows_error_without_claiming_delivery(self):
        from unittest.mock import patch
        with patch('contracts.approval_views.send_mail', side_effect=OSError('Unavailable')):
            response = self.client.post(self.url, {'action': 'request', 'name': 'Guest', 'email': 'guest@example.com', 'reason': 'Review'})
        self.assertContains(response, 'We could not send')
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
        self.assertIn(url, text)
