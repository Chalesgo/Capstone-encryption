# Copy of contracts/tests.py, class PublicVerificationCryptoTests (lines 1892-2493) plus the file's imports.
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


class PublicVerificationCryptoTests(TestCase):
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
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            RSA_PRIVATE_KEY_PATH=str(self.private_key_path),
            RSA_PUBLIC_KEY_PATH=str(self.public_key_path),
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        media_root = Path(self.media_directory.name)
        source_path = media_root / 'source.pdf'
        default_seal_path = media_root / 'seals' / 'default_seal.png'
        seal_path = media_root / 'seal.png'
        sealed_path = media_root / 'sealed.pdf'
        default_seal_path.parent.mkdir(parents=True, exist_ok=True)

        source = fitz.open()
        source.new_page().insert_text((72, 72), 'Official procurement agreement: PHP 10,000.00')
        source.save(source_path)
        source.close()
        Image.new('RGBA', (160, 160), (30, 95, 85, 255)).save(default_seal_path)

        self.original_fingerprint = generate_canonical_fingerprint(source_path)
        encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
            self.original_fingerprint,
            str(self.public_key_path),
        )
        embed_data_in_image(default_seal_path, seal_path, encrypted)
        stamp_seal_on_pdf(
            source_path,
            sealed_path,
            seal_path,
            encrypted_cf=encrypted,
        )
        self.sealed_bytes = sealed_path.read_bytes()
        self.sealed_fingerprint = generate_canonical_fingerprint(sealed_path)
        self.vector_fingerprint = generate_vector_fingerprint(sealed_path)
        self.encrypted_marker = encrypted

        stored_path = media_root / 'contracts' / 'enrolled.pdf'
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        write_pdf(stored_path, self.sealed_bytes)
        self.contract = Contract.objects.create(
            title='Cryptographic verification fixture',
            file='contracts/enrolled.pdf',
            fingerprint=self.sealed_fingerprint,
            vector_fingerprint=self.vector_fingerprint,
            original_fingerprint=self.original_fingerprint,
            encrypted_cf=encrypted,
            wrapped_key=wrapped_key,
            aes_iv=aes_iv,
            hmac_value=hmac_value,
            aes_key='',
        )
        self.version = ContractVersion.objects.create(
            contract=self.contract,
            version_number=1,
            source='upload',
            file='contracts/enrolled.pdf',
            fingerprint=self.sealed_fingerprint,
            vector_fingerprint=self.vector_fingerprint,
            previous_fingerprint='',
            encrypted_cf=encrypted,
            wrapped_key=wrapped_key,
            aes_iv=aes_iv,
            hmac_value=hmac_value,
        )

    def verify(self, pdf_bytes=None):
        upload = SimpleUploadedFile(
            'verification.pdf',
            pdf_bytes if pdf_bytes is not None else self.sealed_bytes,
            content_type='application/pdf',
        )
        response = self.client.post(reverse('public_verify'), {'pdf_file': upload})
        self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
        return self.client.session['verify_result'], self.client.session['verify_debug_log']

    def mutate_pdf(self, mutation):
        document = fitz.open(stream=self.sealed_bytes, filetype='pdf')
        mutation(document)
        mutated = document.tobytes(garbage=4, deflate=True)
        document.close()
        return mutated

    def test_valid_authentic_sealed_pdf(self):
        result, debug_log = self.verify()

        self.assertEqual(result, 'authentic', debug_log)
        self.assertIn('[PASS] Metadata marker belongs to the matched document', debug_log)
        self.assertIn('[PASS] Original fingerprint decrypted with AES-256-CBC and HMAC-SHA256 verified', debug_log)
        self.assertEqual(AuditLog.objects.get(action='viewed').contract, self.contract)

    def test_misleading_filenames_do_not_control_classification(self):
        with fitz.open(stream=self.sealed_bytes, filetype='pdf') as document:
            document[0].insert_text((72, 210), 'Unauthorized alteration')
            modified = document.tobytes()
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), 'Unregistered reference')
            unknown = document.tobytes()
        for data, expected in ((self.sealed_bytes, 'authentic'), (modified, 'tampered'), (unknown, 'not_found')):
            for name in ('authentic_original_legit.pdf', 'tampered_fake_modified.pdf', 'unknown.pdf'):
                with self.subTest(expected=expected, filename=name):
                    response = self.client.post(reverse('public_verify'), {
                        'pdf_file': SimpleUploadedFile(name, data, content_type='application/pdf'),
                    })
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(self.client.session['verify_result'], expected)
                    self.assertFalse(any('Demo simulation' in entry for entry in self.client.session['verify_debug_log']))

    def test_issued_pdf_upload_offers_existing_copy_without_creating_revision(self):
        user = User.objects.create_user('issued-copy-staff', is_staff=True)
        self.contract.uploaded_by = user
        self.contract.save(update_fields=['uploaded_by'])
        self.client.force_login(user)
        for route, data in (
            (reverse('check_duplicate_upload'), {}),
            (reverse('upload_contract'), {'title': 'Renamed copy', 'force_new': '1'}),
            (reverse('add_revision', args=[self.contract.id]), {}),
        ):
            response = self.client.post(route, {
                **data,
                'file': SimpleUploadedFile('renamed.pdf', self.sealed_bytes, content_type='application/pdf'),
            })
            self.assertEqual(response.status_code, 200)
            if route == reverse('check_duplicate_upload'):
                self.assertEqual(response.json()['already_authenticated']['version'], 1)
            else:
                self.assertContains(response, 'View existing document')
                self.assertTrue(response.context['already_authenticated'])
        self.assertEqual(Contract.objects.count(), 1)
        self.assertEqual(self.contract.versions.count(), 1)

    def test_legacy_transparent_seal_uses_enrollment_seal_compatibility(self):
        media_root = Path(self.media_directory.name)
        source_path = media_root / 'source.pdf'
        legacy_seal_path = media_root / 'legacy-transparent-seal.png'
        legacy_pdf_path = media_root / 'legacy-transparent-sealed.pdf'

        image = Image.new('RGBA', (160, 160), (255, 255, 255, 0))
        for y in range(40, 160):
            for x in range(160):
                image.putpixel((x, y), (30, 95, 85, 255))

        pixels = list(image.getdata())
        payload_bits = ''.join(
            format(ord(character), '08b')
            for character in self.encrypted_marker + '||END||'
        )
        encoded_pixels = []
        bit_index = 0
        for red, green, blue, alpha in pixels:
            channels = [red, green, blue]
            for channel_index in range(3):
                if bit_index < len(payload_bits):
                    channels[channel_index] = (
                        channels[channel_index] & ~1
                    ) | int(payload_bits[bit_index])
                    bit_index += 1
            encoded_pixels.append((*channels, alpha))
        image.putdata(encoded_pixels)
        image.save(legacy_seal_path)

        stamp_seal_on_pdf(
            source_path,
            legacy_pdf_path,
            legacy_seal_path,
            encrypted_cf=self.encrypted_marker,
        )
        legacy_bytes = legacy_pdf_path.read_bytes()
        legacy_fingerprint = generate_canonical_fingerprint(legacy_pdf_path)

        self.assertIsNone(
            extract_lsb_marker_from_pdf(str(legacy_pdf_path), self.encrypted_marker)
        )
        self.assertEqual(
            extract_data_from_image(str(legacy_seal_path)),
            self.encrypted_marker,
        )

        stored_path = media_root / 'contracts' / 'enrolled.pdf'
        stored_path.write_bytes(legacy_bytes)
        self.contract.fingerprint = legacy_fingerprint
        self.contract.seal_image = 'legacy-transparent-seal.png'
        self.contract.save(update_fields=['fingerprint', 'seal_image'])
        self.version.fingerprint = legacy_fingerprint
        self.version.save(update_fields=['fingerprint'])

        result, debug_log = self.verify(legacy_bytes)

        self.assertEqual(result, 'authentic', debug_log)
        self.assertIn('[PASS] Legacy enrollment seal LSB marker validated', debug_log)

    def test_modified_text_is_rejected(self):
        modified = self.mutate_pdf(
            lambda document: document[0].insert_text((72, 110), 'ALTERED AMOUNT: PHP 900,000.00')
        )

        result, debug_log = self.verify(modified)

        self.assertEqual(result, 'tampered')
        audit_log = AuditLog.objects.get(action='reported_tampering')
        self.assertEqual(audit_log.verification_debug_log, '\n'.join(debug_log))
        self.assertIn('[FAIL] Sealed PDF fingerprint does not match', audit_log.verification_debug_log)

    def test_modified_vector_is_rejected_with_pdf_editor_warning(self):
        modified = self.mutate_pdf(
            lambda document: document[0].draw_line(
                (72, 150), (260, 150), color=(1, 0, 0), width=3,
            )
        )

        result, debug_log = self.verify(modified)

        self.assertEqual(result, 'tampered')
        self.assertIn(
            '[FAIL] Vector drawing fingerprint differs from the official record',
            debug_log,
        )
        self.assertTrue(any('re-saved with a PDF editor' in line for line in debug_log))
        audit_log = AuditLog.objects.get(action='reported_tampering')
        self.assertIn('PDF editor', audit_log.note)
        self.assertIn('Even without a visible change', audit_log.verification_debug_log)

    def test_modified_or_replaced_metadata_marker_is_rejected(self):
        other_marker, _hmac, _wrapped, _iv = encrypt_cf(
            'a' * 64,
            str(self.public_key_path),
        )
        Contract.objects.create(
            title='Different marker owner',
            file='contracts/other.pdf',
            fingerprint='b' * 64,
            encrypted_cf=other_marker,
        )

        def replace_marker(document):
            metadata = document.metadata
            metadata['keywords'] = f'SEALGUARD:{other_marker}'
            document.set_metadata(metadata)

        result, _debug_log = self.verify(self.mutate_pdf(replace_marker))

        self.assertEqual(result, 'tampered')

    def test_invalid_hmac_is_rejected(self):
        self.contract.hmac_value = '0' * 64
        self.contract.save(update_fields=['hmac_value'])

        result, _debug_log = self.verify()

        self.assertEqual(result, 'tampered')

    def test_invalid_wrapped_aes_key_is_rejected(self):
        self.contract.wrapped_key = base64.b64encode(b'X' * 256).decode()
        self.contract.save(update_fields=['wrapped_key'])

        result, _debug_log = self.verify()

        self.assertEqual(result, 'tampered')

    @patch('contracts.views.extract_lsb_marker_from_pdf', return_value=None)
    def test_missing_or_changed_lsb_marker_is_rejected(self, _extract_marker):
        result, _debug_log = self.verify()
        self.assertEqual(result, 'tampered')

    def test_changed_sealed_pdf_fingerprint_is_rejected(self):
        def change_non_marker_metadata(document):
            metadata = document.metadata
            metadata['subject'] = 'Unauthorized metadata change'
            document.set_metadata(metadata)

        result, _debug_log = self.verify(self.mutate_pdf(change_non_marker_metadata))

        self.assertEqual(result, 'tampered')

    def test_missing_record_with_authentication_evidence_returns_possible_modification(self):
        self.contract.delete()

        result, _debug_log = self.verify()

        self.assertEqual(result, 'tampered')
        self.assertEqual(self.client.session['verify_filename_hint'], 'unlinked_modification')
        report = AuditLog.objects.get(action='reported_tampering')
        self.assertEqual(report.verification_result, 'Possible Modification')
        self.assertIsNone(report.contract_id)
        self.assertTrue(report.evidence_file)

    def test_editor_export_without_markers_retains_warning_evidence_and_trace(self):
        def export_without_markers(document):
            document.set_metadata({'keywords': '', 'creator': 'PDF editor', 'producer': 'PDF editor'})
            for page in document:
                for image in page.get_images(full=True):
                    page.delete_image(image[0])
                page.insert_text((72, 110), 'Edited agreement amount: PHP 50,000.00')

        modified = self.mutate_pdf(export_without_markers)
        result, debug_log = self.verify(modified)

        self.assertEqual(result, 'tampered')
        self.assertEqual(self.client.session['verify_filename_hint'], 'unlinked_modification')
        self.assertIn('[PASS] Footer check: Barangay footer detected', debug_log)
        self.assertTrue(any('no active official record could be identified' in line for line in debug_log))
        report = AuditLog.objects.get(action='reported_tampering')
        self.assertEqual(report.verification_result, 'Possible Modification')
        self.assertIsNone(report.contract_id)
        self.assertEqual(read_pdf(report.evidence_file.path), modified)
        self.assertIn('removed or changed', report.verification_debug_log)
        response = self.client.get(reverse('public_verify'))
        self.assertContains(response, 'Possible Modification')
        self.assertContains(response, 'Its original official record could not be identified')
        self.assertNotContains(response, 'This document differs from its official authenticated record.')

    def test_unregistered_pdf_without_authentication_evidence_remains_not_found(self):
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), 'An unrelated unregistered agreement')
            pdf_bytes = document.tobytes()
        result, _debug_log = self.verify(pdf_bytes)
        self.assertEqual(result, 'not_found')
        report = AuditLog.objects.get(action='verification')
        self.assertEqual(report.verification_result, 'Not Found')
        self.assertFalse(report.evidence_file)

    def test_copied_authentication_footer_never_grants_authenticity(self):
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), 'Unregistered document with a copied footer')
            page.insert_text((72, 110), 'Barangay Sto. Nino - Digitally Authenticated Document')
            pdf_bytes = document.tobytes()
        result, _debug_log = self.verify(pdf_bytes)
        self.assertEqual(result, 'tampered')
        self.assertIsNone(AuditLog.objects.get(action='reported_tampering').contract_id)

    def test_revision_chain_metadata_does_not_override_pdf_authenticity(self):
        self.version.previous_fingerprint = 'broken-link'
        self.version.save(update_fields=['previous_fingerprint'])

        result, _debug_log = self.verify()

        self.assertEqual(result, 'authentic')

    def test_e_accuracy_controlled_100_file_matrix_and_audit_events(self):
        """Run the 100-file accuracy dataset through real enrollment/verification."""
        staff = User.objects.create_user('accuracy-staff', password='Accuracy-password-2026!', is_staff=True)
        self.client.force_login(staff)

        def build_pdf(number, profile):
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), {
                'single-word': 'AUTHENTIC',
                'short-phrase': 'Official record',
                'paragraph': 'This paragraph represents a controlled procurement record for testing.',
                'special-character': 'Amount: PHP 10,000.00 / Ref. #A-01',
                'mixed': 'Mixed text and image control',
                'realistic': f'Procurement Contract {number}: Barangay supply agreement',
            }.get(profile, f'Accuracy fixture {number}'))
            if profile == 'mixed':
                image = Image.new('RGB', (80, 40), (30, 95, 85))
                image_buffer = io.BytesIO()
                image.save(image_buffer, format='PNG')
                page.insert_image(fitz.Rect(72, 100, 152, 140), stream=image_buffer.getvalue())
            if profile == 'realistic':
                page.draw_rect(fitz.Rect(72, 110, 240, 180), color=(0, 0.4, 0.3), fill=(0.8, 0.9, 0.85))
            result = document.tobytes()
            document.close()
            return result

        profiles = ['single-word', 'short-phrase', 'paragraph', 'special-character', 'mixed', 'realistic']
        enrolled = []
        for number in range(30):
            raw = build_pdf(number, profiles[number % len(profiles)])
            # Several controls share identical content on purpose, so each is
            # enrolled as a separate document, as the uploader would choose.
            response = self.client.post(reverse('upload_contract'), {
                'title': f'Accuracy control {number}',
                'force_new': '1',
                'file': SimpleUploadedFile(
                    f'accuracy-control-{number}.pdf', raw,
                    content_type='application/pdf',
                ),
            })
            self.assertEqual(response.status_code, 302)
            enrolled.append((Contract.objects.get(title=f'Accuracy control {number}'), raw))

        public_client = Client()

        def verify_bytes(pdf_bytes, filename):
            response = public_client.post(reverse('public_verify'), {
                'pdf_file': SimpleUploadedFile(filename, pdf_bytes, content_type='application/pdf'),
            })
            self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
            return public_client.session['verify_result']

        observed = {'authentic': 0, 'tampered': 0, 'not_found': 0, 'error': 0}
        for contract, _raw in enrolled:
            result = verify_bytes(read_pdf(contract.file.path), f'accuracy-authentic-{contract.id}.pdf')
            observed[result] += 1

        def mutate(pdf_bytes, category):
            document = fitz.open(stream=pdf_bytes, filetype='pdf')
            page = document[0]
            if category == 'text':
                page.insert_text((72, 210), 'Unauthorized text modification')
            elif category == 'image':
                image = Image.new('RGB', (40, 40), (200, 40, 40))
                image_buffer = io.BytesIO()
                image.save(image_buffer, format='PNG')
                page.insert_image(fitz.Rect(260, 100, 300, 140), stream=image_buffer.getvalue())
            elif category == 'vector':
                page.draw_line((72, 220), (260, 220), color=(1, 0, 0), width=3)
            elif category == 'metadata':
                metadata = document.metadata
                metadata['subject'] = 'Unauthorized metadata modification'
                document.set_metadata(metadata)
            elif category == 'structural':
                document.new_page()
            result = document.tobytes(garbage=4, deflate=True)
            document.close()
            return result

        category_results = {}
        for category in ('text', 'image', 'vector', 'metadata', 'structural'):
            category_results[category] = []
            for number in range(10):
                source_contract = enrolled[(number + len(category)) % len(enrolled)][0]
                result = verify_bytes(
                    mutate(read_pdf(source_contract.file.path), category),
                    f'accuracy-{category}-{number}.pdf',
                )
                category_results[category].append(result)
                observed[result] += 1

        for number in range(20):
            unknown = build_pdf(100 + number, 'realistic')
            result = verify_bytes(unknown, f'accuracy-unregistered-{number}.pdf')
            observed[result] += 1

        self.assertEqual(observed, {'authentic': 30, 'tampered': 50, 'not_found': 20, 'error': 0})
        self.assertEqual(set(category_results['text']), {'tampered'})
        self.assertEqual(set(category_results['image']), {'tampered'})
        self.assertEqual(set(category_results['metadata']), {'tampered'})
        self.assertEqual(set(category_results['structural']), {'tampered'})
        self.assertEqual(set(category_results['vector']), {'tampered'})
        self.assertEqual(
            AuditLog.objects.filter(verification_result__gt='').count(),
            100,
        )

    def test_acc01_to_acc07_three_repetition_accuracy_matrix(self):
        """Run the controlled dataset three times without demo filename hints."""
        staff = User.objects.create_user('acc-staff', password='Accuracy-password-2026!', is_staff=True)
        self.client.force_login(staff)

        def build_pdf(number, profile='control'):
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), f'Accuracy repetition fixture {number} {profile}')
            if profile == 'mixed':
                image = Image.new('RGB', (80, 40), (30, 95, 85))
                image_buffer = io.BytesIO()
                image.save(image_buffer, format='PNG')
                page.insert_image(fitz.Rect(72, 100, 152, 140), stream=image_buffer.getvalue())
            if profile == 'realistic':
                page.draw_rect(fitz.Rect(72, 110, 240, 180), color=(0, 0.4, 0.3), fill=(0.8, 0.9, 0.85))
            result = document.tobytes()
            document.close()
            return result

        enrolled = []
        for number in range(30):
            raw = build_pdf(number, 'mixed' if number % 5 == 0 else 'realistic')
            response = self.client.post(reverse('upload_contract'), {
                'title': f'Accuracy repetition control {number}',
                'file': SimpleUploadedFile(
                    f'acc-control-{number}.pdf', raw,
                    content_type='application/pdf',
                ),
            })
            self.assertEqual(response.status_code, 302)
            enrolled.append((Contract.objects.get(title=f'Accuracy repetition control {number}'), raw))

        def mutate(pdf_bytes, category):
            document = fitz.open(stream=pdf_bytes, filetype='pdf')
            page = document[0]
            if category == 'text':
                page.insert_text((72, 210), 'Accuracy text alteration')
            elif category == 'image':
                image = Image.new('RGB', (40, 40), (200, 40, 40))
                image_buffer = io.BytesIO()
                image.save(image_buffer, format='PNG')
                page.insert_image(fitz.Rect(260, 100, 300, 140), stream=image_buffer.getvalue())
            elif category == 'vector':
                page.draw_line((72, 220), (260, 220), color=(1, 0, 0), width=3)
            elif category == 'metadata':
                metadata = document.metadata
                metadata['subject'] = 'Accuracy metadata alteration'
                document.set_metadata(metadata)
            elif category == 'structural':
                document.new_page()
            result = document.tobytes(garbage=4, deflate=True)
            document.close()
            return result

        public_client = Client()
        observed = []
        expected = []
        decryption_matches = 0
        sealed_hash_differences = 0

        for contract, original_bytes in enrolled:
            with contract.file.open('rb') as stored_file:
                sealed_bytes = stored_file.read()
            original_hash = hashlib.sha256(original_bytes).hexdigest()
            sealed_hash = hashlib.sha256(sealed_bytes).hexdigest()
            if original_hash != sealed_hash:
                sealed_hash_differences += 1
            # The implementation protects the fingerprint, not the PDF, so
            # this is the available decryption-integrity measurement.
            decrypted_cf = decrypt_cf(
                contract.encrypted_cf,
                contract.wrapped_key,
                contract.aes_iv,
                contract.hmac_value,
                str(self.private_key_path),
            )
            if decrypted_cf == contract.original_fingerprint:
                decryption_matches += 1

        # Repeat each file three times. Names deliberately avoid authentic,
        # tampered, and unknown so the filename demonstration branch is excluded.
        cases = []
        for contract, _original_bytes in enrolled:
            cases.append(('authentic', read_pdf(contract.file.path), 'acc-control'))
        for category in ('text', 'image', 'vector', 'metadata', 'structural'):
            for number in range(10):
                contract = enrolled[(number + len(category)) % len(enrolled)][0]
                cases.append(('tampered', mutate(read_pdf(contract.file.path), category), f'acc-{category}'))
        for number in range(20):
            cases.append(('not_found', build_pdf(100 + number, 'unregistered'), 'acc-unregistered'))

        for repetition in range(3):
            for expected_result, pdf_bytes, label in cases:
                response = public_client.post(reverse('public_verify'), {
                    'pdf_file': SimpleUploadedFile(
                        f'{label}-rep-{repetition}.pdf', pdf_bytes,
                        content_type='application/pdf',
                    ),
                })
                self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
                observed.append(public_client.session['verify_result'])
                expected.append(expected_result)

        correct = sum(actual == wanted for actual, wanted in zip(observed, expected))
        false_positives = sum(
            actual in {'tampered', 'not_found'}
            for actual, wanted in zip(observed, expected)
            if wanted == 'authentic'
        )
        false_negatives = sum(
            actual == 'authentic'
            for actual, wanted in zip(observed, expected)
            if wanted == 'tampered'
        )
        unknown_correct = sum(
            actual == 'not_found'
            for actual, wanted in zip(observed, expected)
            if wanted == 'not_found'
        )

        self.assertEqual(len(observed), 300)
        self.assertEqual(correct, 300)
        self.assertEqual(false_positives, 0)
        self.assertEqual(false_negatives, 0)
        self.assertEqual(unknown_correct, 60)
        self.assertEqual(decryption_matches, 30)
        self.assertEqual(sealed_hash_differences, 30)
        self.assertEqual(
            AuditLog.objects.filter(verification_result__gt='').count(),
            300,
        )
        self.assertEqual(correct / 300 * 100, 100.0)


