"""Re-exported copies identified by their page QR, and the reported-copy comparison."""
import tempfile
from pathlib import Path

import fitz
from Crypto.PublicKey import RSA
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import AuditLog, Contract
from .pdf_storage import read_pdf
from .views import has_barangay_footer


class ReportedCopyTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key_directory = tempfile.TemporaryDirectory()
        key = RSA.generate(2048)
        cls.private_key_path = Path(cls.key_directory.name) / 'private.pem'
        cls.public_key_path = Path(cls.key_directory.name) / 'public.pem'
        cls.private_key_path.write_bytes(key.export_key())
        cls.public_key_path.write_bytes(key.publickey().export_key())

    @classmethod
    def tearDownClass(cls):
        cls.key_directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        media_root = Path(self.media_directory.name)
        (media_root / 'seals').mkdir(parents=True, exist_ok=True)
        Image.new('RGBA', (180, 180), (30, 95, 85, 255)).save(media_root / 'seals' / 'default_seal.png')
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            RSA_PRIVATE_KEY_PATH=str(self.private_key_path),
            RSA_PUBLIC_KEY_PATH=str(self.public_key_path),
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.staff = User.objects.create_user('compare-staff', password='Compare-password-2026!', is_staff=True)
        self.client.force_login(self.staff)

    @staticmethod
    def pdf_with_text(*lines):
        document = fitz.open()
        page = document.new_page()
        for index, line in enumerate(lines):
            page.insert_text((72, 72 + 18 * index), line)
        data = document.tobytes()
        document.close()
        return data

    def upload_and_seal(self):
        original = self.pdf_with_text(
            'CONTRACT FOR PREVENTIVE MAINTENANCE',
            'Total contract price: PHP 76,800.00',
        )
        response = self.client.post(reverse('upload_contract'), {
            'title': 'Aircon Maintenance',
            'file': SimpleUploadedFile('aircon.pdf', original, content_type='application/pdf'),
        })
        self.assertEqual(response.status_code, 302)
        contract = Contract.objects.get(title='Aircon Maintenance')
        return contract, read_pdf(contract.file.path)

    @staticmethod
    def reexport_with_new_amount(sealed_bytes):
        """Imitate an editor re-export: metadata marker gone, amount changed."""
        document = fitz.open(stream=sealed_bytes, filetype='pdf')
        page = document[0]
        for rect in page.search_for('76,800.00'):
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        page.insert_text((300, 90), '90,800.00')
        document.set_metadata({})
        data = document.tobytes(garbage=4, deflate=True)
        document.close()
        return data

    def verify(self, pdf_bytes):
        response = self.client.post(reverse('public_verify'), {
            'pdf_file': SimpleUploadedFile('copy.pdf', pdf_bytes, content_type='application/pdf'),
        })
        self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
        return self.client.session['verify_result'], self.client.session['verify_debug_log']

    def test_reexported_copy_is_linked_through_its_page_qr(self):
        contract, sealed = self.upload_and_seal()
        result, debug_log = self.verify(self.reexport_with_new_amount(sealed))

        self.assertEqual(result, 'tampered', debug_log)
        self.assertTrue(any('Page QR code links to active contract' in line for line in debug_log), debug_log)
        report = AuditLog.objects.get(action='reported_tampering')
        self.assertEqual(report.contract, contract)
        self.assertTrue(report.evidence_file)

    def test_unsealed_barangay_document_is_still_not_found(self):
        unsealed = self.pdf_with_text(
            'Barangay Sto. Niño, City of Biñan, Laguna',
            'Brgy. Tubigan, Biñan City',
        )
        result, debug_log = self.verify(unsealed)
        self.assertEqual(result, 'not_found', debug_log)

    def test_footer_wording_alone_is_recognised(self):
        path = Path(self.media_directory.name) / 'footer.pdf'
        path.write_bytes(self.pdf_with_text('VERIFY THIS DOCUMENT ONLINE', 'Scan to Verify'))
        self.assertTrue(has_barangay_footer(str(path)))

    def test_comparison_shows_both_documents_and_the_changed_text(self):
        contract, sealed = self.upload_and_seal()
        self.verify(self.reexport_with_new_amount(sealed))
        report = AuditLog.objects.get(action='reported_tampering')

        history = self.client.get(reverse('audit_verification_history', args=[report.id])).json()
        compare_url = reverse('compare_reported_copy', args=[report.id])
        self.assertEqual(history['compare_url'], compare_url)

        response = self.client.get(compare_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'file=' + reverse('audit_evidence_preview', args=[report.id]))
        self.assertContains(response, 'Official record · v1')
        only_official = ' '.join(response.context['only_official'])
        only_reported = ' '.join(response.context['only_reported'])
        self.assertIn('76,800.00', only_official)
        self.assertIn('90,800.00', only_reported)

    def test_comparison_is_not_offered_for_an_authentic_check(self):
        _contract, sealed = self.upload_and_seal()
        result, debug_log = self.verify(sealed)
        self.assertEqual(result, 'authentic', debug_log)
        entry = AuditLog.objects.filter(verification_result='Authentic').latest('id')

        history = self.client.get(reverse('audit_verification_history', args=[entry.id])).json()
        self.assertIsNone(history['compare_url'])
        self.assertEqual(self.client.get(reverse('compare_reported_copy', args=[entry.id])).status_code, 404)

    def test_comparison_requires_a_staff_account(self):
        contract, sealed = self.upload_and_seal()
        self.verify(self.reexport_with_new_amount(sealed))
        report = AuditLog.objects.get(action='reported_tampering')

        reader = User.objects.create_user('compare-reader', password='Reader-password-2026!')
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse('compare_reported_copy', args=[report.id])).status_code, 404)
