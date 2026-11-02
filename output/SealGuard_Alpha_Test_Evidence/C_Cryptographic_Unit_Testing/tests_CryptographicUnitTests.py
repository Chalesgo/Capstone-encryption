# Copy of contracts/tests.py, class CryptographicUnitTests (lines 1631-1891) plus the file's imports.
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


class CryptographicUnitTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temp_directory = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temp_directory.name)
        rsa_key = RSA.generate(2048)
        cls.private_key_path = cls.temp_path / 'private.pem'
        cls.public_key_path = cls.temp_path / 'public.pem'
        cls.private_key_path.write_bytes(rsa_key.export_key())
        cls.public_key_path.write_bytes(rsa_key.publickey().export_key())

    @classmethod
    def tearDownClass(cls):
        cls.temp_directory.cleanup()
        super().tearDownClass()

    def make_pdf(self, name, *, text='Cryptographic fixture', image_bytes=None, metadata=None, extra_page=False, vector=None):
        path = self.temp_path / name
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), text)
        if image_bytes:
            page.insert_image(fitz.Rect(72, 100, 172, 200), stream=image_bytes)
        if vector:
            page.draw_line(fitz.Point(72, 220), fitz.Point(172, 220), color=vector)
        if extra_page:
            document.new_page().insert_text((72, 72), 'Additional structure')
        if metadata:
            document.set_metadata(metadata)
        document.save(path)
        document.close()
        return path

    def encrypt(self, value):
        return encrypt_cf(value, str(self.public_key_path))

    def test_cry01_sha256_consistency_matrix(self):
        for number in range(30):
            data = f'unchanged input {number}'.encode()
            first = generate_file_hash(SimpleUploadedFile(f'sha-{number}-a.bin', data))
            second = generate_file_hash(SimpleUploadedFile(f'sha-{number}-b.bin', data))
            with self.subTest(number=number):
                self.assertEqual(first, second)
                self.assertEqual(first, hashlib.sha256(data).hexdigest())

    def test_cry02_canonical_fingerprint_consistency_matrix(self):
        for number in range(20):
            path = self.make_pdf(f'unchanged-{number}.pdf')
            values = [generate_canonical_fingerprint(path) for _ in range(3)]
            with self.subTest(number=number):
                self.assertEqual(values[0], values[1])
                self.assertEqual(values[1], values[2])

    def test_cry03_fingerprint_change_detection_matrix(self):
        image = Image.new('RGB', (40, 40), (10, 20, 30))
        image_buffer = tempfile.NamedTemporaryFile(suffix='.png', dir=self.temp_path, delete=False)
        image.save(image_buffer, format='PNG')
        image_buffer.close()
        image_bytes = Path(image_buffer.name).read_bytes()
        image.close()
        changed_image = Image.new('RGB', (40, 40), (11, 20, 30))
        changed_image_path = self.temp_path / 'changed-carrier.png'
        changed_image.save(changed_image_path)
        changed_image_bytes = changed_image_path.read_bytes()
        changed_image.close()
        cases = []
        for number in range(10):
            cases.append((f'text-{number}', self.make_pdf(f'text-a-{number}.pdf', text='Text A'), self.make_pdf(f'text-b-{number}.pdf', text='Text B')))
            cases.append((f'image-{number}', self.make_pdf(f'image-a-{number}.pdf', image_bytes=image_bytes), self.make_pdf(f'image-b-{number}.pdf', image_bytes=changed_image_bytes)))
            metadata_a = {'title': 'A', 'author': 'SealGuard'}
            metadata_b = {'title': 'B', 'author': 'SealGuard'}
            cases.append((f'metadata-{number}', self.make_pdf(f'metadata-a-{number}.pdf', metadata=metadata_a), self.make_pdf(f'metadata-b-{number}.pdf', metadata=metadata_b)))
            cases.append((f'structure-{number}', self.make_pdf(f'structure-a-{number}.pdf'), self.make_pdf(f'structure-b-{number}.pdf', extra_page=True)))
            cases.append((f'vector-{number}', self.make_pdf(f'vector-a-{number}.pdf', vector=(1, 0, 0)), self.make_pdf(f'vector-b-{number}.pdf', vector=(0, 0, 1))))
        for label, first_path, second_path in cases:
            with self.subTest(case=label):
                first = generate_canonical_fingerprint(first_path)
                second = generate_canonical_fingerprint(second_path)
                if label.startswith('vector-'):
                    self.assertEqual(first, second, 'Vector-only changes are intentionally excluded by canonicalization')
                    self.assertNotEqual(
                        generate_vector_fingerprint(first_path),
                        generate_vector_fingerprint(second_path),
                    )
                else:
                    self.assertNotEqual(first, second)

    def test_cry04_aes_round_trip_matrix(self):
        payloads = ['s' * 8 for _ in range(10)] + ['m' * 1024 for _ in range(10)] + ['l' * 10000 for _ in range(10)]
        for number, payload in enumerate(payloads):
            encrypted, hmac_value, wrapped_key, iv = self.encrypt(payload)
            with self.subTest(number=number):
                self.assertEqual(decrypt_cf(encrypted, wrapped_key, iv, hmac_value, str(self.private_key_path)), payload)

    def test_cry05_incorrect_aes_key_or_wrapping_key_fails_matrix(self):
        wrong_key = RSA.generate(2048)
        wrong_private_path = self.temp_path / 'wrong-private.pem'
        wrong_private_path.write_bytes(wrong_key.export_key())
        for number in range(10):
            encrypted, hmac_value, wrapped_key, iv = self.encrypt(f'wrong-key-{number}')
            with self.subTest(number=number):
                with self.assertRaises(Exception):
                    decrypt_cf(encrypted, wrapped_key, iv, hmac_value, str(wrong_private_path))

    def test_cry06_corrupted_ciphertext_is_rejected_matrix(self):
        for number in range(10):
            encrypted, hmac_value, wrapped_key, iv = self.encrypt(f'ciphertext-{number}')
            corrupted = bytearray(base64.b64decode(encrypted))
            corrupted[0] ^= 1
            with self.subTest(number=number):
                with self.assertRaises(Exception):
                    decrypt_cf(base64.b64encode(corrupted).decode(), wrapped_key, iv, hmac_value, str(self.private_key_path))

    def test_cry07_hmac_validation_matrix(self):
        for number in range(20):
            value = f'hmac-{number}'
            key = os.urandom(32)
            expected = generate_hmac(value, key)
            with self.subTest(valid=number):
                self.assertTrue(verify_hmac(value, key, expected))
            with self.subTest(altered=number):
                self.assertFalse(verify_hmac(value + 'altered', key, expected))

    def test_cry08_rsa_key_wrapping_matrix(self):
        for number in range(20):
            value = f'wrapped-{number}'
            encrypted, hmac_value, wrapped_key, iv = self.encrypt(value)
            with self.subTest(number=number):
                self.assertEqual(decrypt_cf(encrypted, wrapped_key, iv, hmac_value, str(self.private_key_path)), value)
        wrong_key = RSA.generate(2048)
        wrong_private_path = self.temp_path / 'wrong-rsa-private.pem'
        wrong_private_path.write_bytes(wrong_key.export_key())
        for number in range(10):
            encrypted, hmac_value, wrapped_key, iv = self.encrypt(f'wrong-rsa-{number}')
            with self.subTest(wrong_key=number):
                with self.assertRaises(Exception):
                    decrypt_cf(encrypted, wrapped_key, iv, hmac_value, str(wrong_private_path))

    def test_cry09_lsb_embed_extract_capacity_matrix(self):
        source = self.temp_path / 'carrier.png'
        Image.new('RGB', (128, 128), (120, 140, 160)).save(source)
        capacity = (128 * 128 * 3) // 8 - len('||END||') - 1
        payloads = [f'small-{number}' for number in range(10)]
        payloads += [('medium-' + ('m' * 500)) for _ in range(10)]
        payloads += [('near-' + ('n' * (capacity - 10))) for _ in range(10)]
        for number, payload in enumerate(payloads):
            output = self.temp_path / f'carrier-output-{number}.png'
            embed_data_in_image(str(source), str(output), payload)
            with self.subTest(number=number):
                self.assertEqual(extract_data_from_image(str(output)), payload)

    def test_lsb_survives_transparent_seal_pdf_embedding(self):
        source_pdf = self.make_pdf('transparent-seal-source.pdf')
        carrier = self.temp_path / 'transparent-carrier.png'
        embedded = self.temp_path / 'transparent-embedded.png'
        sealed_pdf = self.temp_path / 'transparent-sealed.pdf'

        image = Image.new('RGBA', (160, 160), (255, 255, 255, 0))
        for y in range(40, 160):
            for x in range(160):
                image.putpixel((x, y), (30, 95, 85, 255))
        image.save(carrier)

        payload = 'transparent-seal-marker-' + ('x' * 320)
        embed_data_in_image(str(carrier), str(embedded), payload)
        stamp_seal_on_pdf(source_pdf, sealed_pdf, embedded, encrypted_cf=payload)

        self.assertEqual(extract_data_from_image(str(embedded)), payload)
        self.assertEqual(extract_lsb_marker_from_pdf(str(sealed_pdf), payload), payload)

    def test_cry10_invalid_lsb_payloads_are_not_accepted_matrix(self):
        corrupt_image_path = self.temp_path / 'corrupt-carrier.png'
        Image.new('RGB', (40, 40), (50, 60, 70)).save(corrupt_image_path)
        corrupt_image_bytes = corrupt_image_path.read_bytes()
        for number in range(10):
            empty_pdf = self.make_pdf(f'empty-seal-{number}.pdf')
            with self.subTest(absent=number):
                self.assertIsNone(extract_lsb_marker_from_pdf(str(empty_pdf), 'expected-marker'))
        for number in range(10):
            corrupt_pdf = self.make_pdf(f'corrupt-seal-{number}.pdf', image_bytes=corrupt_image_bytes)
            with self.subTest(corrupt=number):
                self.assertIsNone(extract_lsb_marker_from_pdf(str(corrupt_pdf), 'expected-marker'))
        source = self.temp_path / 'small-carrier.png'
        Image.new('RGB', (16, 16), (10, 10, 10)).save(source)
        for number in range(10):
            with self.subTest(over_capacity=number):
                with self.assertRaises(ValueError):
                    embed_data_in_image(str(source), str(self.temp_path / f'oversized-{number}.png'), 'x' * 1000)

    def test_cry12_sensitive_data_inspection_matrix(self):
        secret_document_text = 'PRIVATE-DOCUMENT-CONTENTS-MUST-NOT-LEAK'
        plaintext_password = 'Never-expose-this-password-2026!'
        user = User.objects.create_user('crypto-inspection-user', password=plaintext_password, is_staff=True)
        contracts = []
        for number in range(20):
            original_cf = hashlib.sha256(f'original-{number}'.encode()).hexdigest()
            encrypted, hmac_value, wrapped_key, iv = self.encrypt(original_cf)
            contract = Contract.objects.create(
                title=f'Sensitive Inspection Document {number}',
                recipient=user,
                file=f'contracts/inspection-{number}.pdf',
                original_fingerprint=original_cf,
                encrypted_cf=encrypted,
                wrapped_key=wrapped_key,
                aes_iv=iv,
                hmac_value=hmac_value,
                aes_key='',
            )
            contracts.append(contract)

        # Database inspection: the AES key field remains empty and no stored
        # cryptographic value is the original plaintext fingerprint.
        for contract in contracts:
            with self.subTest(database_record=contract.id):
                self.assertEqual(contract.aes_key, '')
                self.assertNotEqual(contract.encrypted_cf, contract.original_fingerprint)
                self.assertNotEqual(contract.wrapped_key, contract.original_fingerprint)

        logs = [
            AuditLog(
                contract=contract,
                user=user,
                action='viewed',
                note='Sensitive-data inspection activity',
            )
            for contract in contracts
        ]
        AuditLog.objects.bulk_create(logs)
        for log in AuditLog.objects.filter(note='Sensitive-data inspection activity')[:20]:
            with self.subTest(audit_log=log.id):
                self.assertNotIn(plaintext_password, str(log))
                self.assertNotIn(secret_document_text, str(log))

        user.refresh_from_db()
        self.assertNotEqual(user.password, plaintext_password)
        self.assertTrue(user.password.startswith(('pbkdf2_', 'argon2', 'bcrypt')))

        self.client.force_login(user)
        response_urls = [
            reverse('dashboard'),
            reverse('contract_list'),
            reverse('export_dashboard_report'),
            reverse('public_verify'),
        ] * 5
        sensitive_values = [
            plaintext_password,
            secret_document_text,
            *(contract.encrypted_cf for contract in contracts),
            *(contract.wrapped_key for contract in contracts),
            *(contract.hmac_value for contract in contracts),
        ]
        for number, url in enumerate(response_urls):
            response = self.client.get(url)
            body = response.content.decode('utf-8', errors='replace')
            with self.subTest(http_response=number, url=url):
                self.assertIn(response.status_code, {200, 302})
                for sensitive_value in sensitive_values:
                    self.assertNotIn(sensitive_value, body)


