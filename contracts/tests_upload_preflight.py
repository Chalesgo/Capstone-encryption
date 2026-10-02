import tempfile
from unittest.mock import patch

import fitz
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Contract


class UploadPreflightTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('preflight-staff', is_staff=True)
        self.client.force_login(self.staff)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def post(self, payload):
        return self.client.post(reverse('check_duplicate_upload'), {
            'title': 'Validation screenshot',
            'file': SimpleUploadedFile('invalid_upload.pdf', payload, content_type='application/pdf'),
        })

    def test_invalid_pdf_is_rejected_before_duplicate_processing(self):
        with patch('contracts.views._existing_sealed_upload') as lookup:
            for payload in [b'This is plain text', b'%PDF-not-a-real-pdf']:
                with self.subTest(payload=payload):
                    response = self.post(payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()['error'], 'invalid_file')
                    self.assertIn('This file is not a PDF.', response.json()['message'])
            lookup.assert_not_called()
        self.assertEqual(Contract.objects.count(), 0)

    def test_oversized_file_returns_upload_limit_message(self):
        response = self.post(b'x' * (15 * 1024 * 1024 + 1))
        self.assertEqual(response.status_code, 400)
        self.assertIn('maximum allowed size is 15 MB', response.json()['message'])
        self.assertEqual(Contract.objects.count(), 0)

    def test_password_protected_pdf_is_rejected(self):
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), 'Protected fixture')
            payload = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='user')
        response = self.post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Password-protected PDFs are not supported', response.json()['message'])

    def test_valid_pdf_still_reaches_duplicate_lookup(self):
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), 'Valid upload fixture')
            payload = document.tobytes()
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(response.json()['duplicates'], [])
        self.assertEqual(Contract.objects.count(), 0)
