# Copy of contracts/tests.py, class EndToEndWorkflowTests (lines 1507-1630) plus the file's imports.
# The runnable original is contracts/tests.py.

from .pdf_storage import read_pdf, write_pdf
from datetime import timedelta
import base64
import json
import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path
from unittest import expectedFailure
from unittest.mock import patch

import fitz
from Crypto.PublicKey import RSA
from django.contrib.auth.models import Permission, User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import (
    AuditLog, Contract, ContractVersion, Folder, PhysicalVerificationManifest, Tutorial,
)
from .physical_verification import (
    build_manifest, canonical_json, encode_page_token, sign_manifest,
    verify_manifest_signature,
)
from .forms import ContractForm, sanitize_tutorial_html
from .utils import (
    decrypt_cf,
    encrypt_cf,
    embed_data_in_image,
    extract_data_from_image,
    extract_lsb_marker_from_pdf,
    generate_canonical_fingerprint,
    generate_file_hash,
    generate_hmac,
    generate_qr_code,
    generate_vector_fingerprint,
    log_activity,
    stamp_seal_on_pdf,
    verify_hmac,
)


class EndToEndWorkflowTests(TestCase):
    """Real-PDF workflow coverage for the locally configured SealGuard stack."""

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
        self.staff = User.objects.create_user('e2e-staff', password='E2E-password-2026!', is_staff=True)
        self.client.force_login(self.staff)

    @staticmethod
    def make_pdf(number):
        document = fitz.open()
        page_count = 1 + (number % 4)
        for page_number in range(page_count):
            page = document.new_page()
            page.insert_text((72, 72), f'E2E document {number}, page {page_number + 1}')
            if page_number == 0 and number % 3 == 0:
                page.draw_rect(fitz.Rect(72, 110, 240, 180), color=(0, 0.4, 0.3), fill=(0.8, 0.9, 0.85))
        result = document.tobytes()
        document.close()
        return result

    def process_documents(self, count):
        processed = []
        for number in range(count):
            original = self.make_pdf(number)
            response = self.client.post(reverse('upload_contract'), {
                'title': f'E2E document {number}',
                'file': SimpleUploadedFile(
                    f'e2e-document-{number}.pdf', original,
                    content_type='application/pdf',
                ),
            })
            self.assertEqual(response.status_code, 302, response.content[:200])
            contract = Contract.objects.get(title=f'E2E document {number}')
            contract.refresh_from_db()
            self.assertTrue(contract.encrypted_cf)
            self.assertTrue(contract.wrapped_key)
            self.assertTrue(contract.hmac_value)
            self.assertTrue(contract.file and Path(contract.file.path).is_file())
            processed.append((contract, original))
        return processed

    def test_failed_revision_does_not_leave_contract_without_version_row(self):
        (contract, _original), = self.process_documents(1)
        file_before = contract.file.name
        with patch('contracts.views.ContractVersion.objects.create', side_effect=RuntimeError('db down')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('add_revision', args=[contract.id]), {
                    'file': SimpleUploadedFile('revision.pdf', self.make_pdf(7), content_type='application/pdf'),
                })
        contract.refresh_from_db()
        self.assertEqual(contract.file.name, file_before)
        self.assertTrue(contract.versions.filter(file=contract.file.name).exists())

    def test_e2e01_complete_lifecycle_matrix(self):
        processed = self.process_documents(30)
        for contract, _original in processed:
            with self.subTest(contract=contract.id):
                version = contract.versions.get(version_number=1)
                self.assertEqual(contract.vector_fingerprint, generate_vector_fingerprint(contract.file.path))
                self.assertTrue(version.file and Path(version.file.path).is_file())
                retrieved = self.client.get(reverse('preview_contract', args=[contract.id]))
                self.assertEqual(retrieved.status_code, 200)
                retrieved_bytes = b''.join(retrieved.streaming_content)
                self.assertEqual(retrieved_bytes, read_pdf(contract.file.path))

    def test_e2e03_staff_and_public_verification_matrix(self):
        processed = self.process_documents(30)
        public_client = Client()
        for contract, _original in processed:
            sealed_bytes = read_pdf(contract.file.path)
            for verifier in (self.client, public_client):
                with self.subTest(contract=contract.id, verifier=verifier is self.client):
                    response = verifier.post(reverse('public_verify'), {
                        'pdf_file': SimpleUploadedFile(
                            'verification-upload.pdf', sealed_bytes,
                            content_type='application/pdf',
                        ),
                    })
                    self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
                    self.assertEqual(verifier.session['verify_result'], 'authentic')
        self.assertEqual(
            AuditLog.objects.filter(action='viewed', note__startswith='Public verification: authentic').count(),
            60,
        )

    def test_e2e04_persistence_after_new_client_session(self):
        processed = self.process_documents(20)
        restarted_client = Client()
        restarted_client.force_login(self.staff)
        for contract, _original in processed:
            with self.subTest(contract=contract.id):
                response = restarted_client.get(reverse('download_contract', args=[contract.id]))
                self.assertEqual(response.status_code, 200)
                response_bytes = b''.join(response.streaming_content)
                self.assertEqual(response_bytes, read_pdf(contract.file.path))


