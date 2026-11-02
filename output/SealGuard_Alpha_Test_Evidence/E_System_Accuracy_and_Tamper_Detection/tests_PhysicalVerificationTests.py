# Copy of contracts/tests.py, class PhysicalVerificationTests (lines 2494-2906) plus the file's imports.
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


class PhysicalVerificationTests(TestCase):
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
        seal_directory = Path(self.media_directory.name) / 'seals'
        seal_directory.mkdir(parents=True, exist_ok=True)
        Image.new('RGBA', (240, 240), (35, 105, 90, 255)).save(
            seal_directory / 'default_seal.png'
        )
        self.user = User.objects.create_user('physical_staff', password='password', is_staff=True)
        self.contract, self.version, self.tokens, self.pdf_bytes = self.issue_document(
            'Contract A', ['Page one amount PHP 3,000', 'Page two terms and signature', 'Page three approval']
        )

    def issue_document(self, title, page_texts, contract=None, version_number=1):
        media_root = Path(self.media_directory.name)
        document = fitz.open()
        for text in page_texts:
            page = document.new_page()
            page.insert_text((72, 90), text, fontsize=16)
        filename = f'{title.replace(" ", "_")}_v{version_number}_{Contract.objects.count()}.pdf'
        path = media_root / 'contracts' / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        document.save(path)
        document.close()
        fingerprint = generate_canonical_fingerprint(path)
        contract = contract or Contract.objects.create(
            title=title, file=f'contracts/{filename}', fingerprint=fingerprint, uploaded_by=self.user,
            original_fingerprint=fingerprint,
        )
        version = ContractVersion.objects.create(
            contract=contract, version_number=version_number, source='upload',
            file=f'contracts/{filename}', fingerprint=fingerprint,
        )
        manifest, tokens = build_manifest(
            contract.id, version_number, path, fingerprint, timezone.now()
        )
        PhysicalVerificationManifest.objects.create(
            version=version, manifest_id=manifest['manifest_id'], manifest=manifest,
            signature=sign_manifest(manifest, self.private_key_path),
        )
        # Print each page's QR the way issued copies carry it.
        with fitz.open(path) as document:
            for page, token in zip(document, tokens):
                qr_path = media_root / f'{filename}_p{page.number + 1}.png'
                generate_qr_code(token, str(qr_path))
                page.insert_image(self.QR_RECT, filename=str(qr_path))
            pdf_bytes = document.tobytes()
        write_pdf(path, pdf_bytes)
        return contract, version, tokens, pdf_bytes

    QR_RECT = fitz.Rect(500, 750, 578, 828)

    def mark_qr(self, page_index, lines, width):
        """Draw marker strokes given as fractions of the page QR's box."""
        def draw(document):
            box = self.QR_RECT
            for x0, y0, x1, y1 in lines:
                document[page_index].draw_line(
                    fitz.Point(box.x0 + box.width * x0, box.y0 + box.height * y0),
                    fitz.Point(box.x0 + box.width * x1, box.y0 + box.height * y1),
                    color=(0.08, 0.08, 0.08), width=width,
                )
        return self.mutate_pdf(draw)

    def verify(self, tokens=None, pdf_bytes=None):
        upload = SimpleUploadedFile(
            'physical.pdf', pdf_bytes or self.pdf_bytes, content_type='application/pdf'
        )
        return self.client.post(reverse('verify_physical'), {
            'physical_file': upload,
            'page_tokens': json.dumps(tokens or self.tokens),
        })

    def test_physical_verification_page_has_loading_state_and_scan_submit_flow(self):
        response = self.client.get(reverse('verify_physical'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="physical-loading"')
        self.assertContains(response, 'Checking page identities and registered document content.')
        self.assertContains(response, 'physicalForm.requestSubmit()')
        self.assertContains(response, 'QR codes and complete pages captured.')
        self.assertContains(response, 'Physical verification in progress')
        self.assertContains(response, 'id="camera-expand"')
        self.assertContains(response, 'camera-expanded')
        self.assertContains(response, 'body.camera-view-active')
        self.assertContains(response, 'id="qr-check-result"')
        self.assertContains(response, reverse('verify_physical_qr'))
        self.assertContains(response, 'id="scan-guide-text"')
        self.assertNotContains(response, 'Step 1 of 3')
        self.assertContains(response, 'Step 3 of 3')
        self.assertContains(response, 'id="page-capture-guide"')
        self.assertContains(response, 'id="fab-capture"')
        self.assertContains(response, 'id="fab-files"')
        self.assertContains(response, 'id="scanned-sheet"')
        self.assertContains(response, 'id="scanned-list"')
        self.assertContains(response, "captureCanvas.toBlob")
        self.assertContains(response, "physicalFile.click()")
        self.assertNotContains(response, 'openSheet(physicalForm)')
        self.assertContains(response, 'class="fab-icon"')
        self.assertNotContains(response, '&#128247;')

    def scan_qr(self, page_index, pdf_bytes=None):
        """Post a page QR together with the camera frame it was read from."""
        return self.client.post(reverse('verify_physical_qr'), {
            'qr_token': self.tokens[page_index],
            'qr_frame': self.scan_frame(pdf_bytes or self.pdf_bytes, page_index),
        })

    def test_physical_qr_check_confirms_registered_page_before_full_verification(self):
        response = self.scan_qr(0)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['valid'])
        self.assertEqual(payload['contract_id'], self.contract.id)
        self.assertEqual(payload['version'], 1)
        self.assertEqual(payload['page'], 1)
        self.assertEqual(payload['total_pages'], 3)
        self.assertEqual(payload['message'], 'Page 1 of 3 found. The QR only identifies the page.')

    def test_physical_qr_check_rejects_invalid_code_safely(self):
        response = self.client.post(reverse('verify_physical_qr'), {
            'qr_token': 'not-a-sealguard-code',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['valid'])

    def test_physical_qr_preview_contains_only_the_scanned_version_page(self):
        # A newer version must not replace the page identified by the old QR.
        self.issue_document('New revision', ['Different newer page'], contract=self.contract, version_number=2)
        response = self.scan_qr(1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        data = response.json()
        self.assertEqual((data['version'], data['page']), (1, 2))
        self.assertNotIn('page_image', data)
        self.assertTrue(data['thumbnail'].startswith('data:image/png;base64,'))
        page = self.client.get(data['page_pdf_url'])
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page['Content-Type'], 'application/pdf')
        with fitz.open(stream=b''.join(page.streaming_content), filetype='pdf') as document:
            self.assertEqual(len(document), 1)
            self.assertIn('Page two terms and signature', document[0].get_text())

    def test_physical_page_pdf_rejects_invalid_token(self):
        response = self.client.get(reverse('verify_physical_page'), {'qr_token': 'SGP1.forged'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(reverse('verify_physical_page')).status_code, 404)

    def test_physical_qr_preview_rejects_trashed_document(self):
        self.contract.is_trashed = True
        self.contract.save()
        response = self.client.post(reverse('verify_physical_qr'), {'qr_token': self.tokens[0]})
        self.assertEqual(response.status_code, 410)
        self.assertNotIn('page_pdf_url', response.json())

    def mutate_pdf(self, callback):
        document = fitz.open(stream=self.pdf_bytes, filetype='pdf')
        callback(document)
        data = document.tobytes(garbage=4, deflate=True)
        document.close()
        return data

    def test_all_pages_from_correct_contract_are_verified_in_order(self):
        response = self.verify()
        self.assertEqual(response.context['result'], 'verified')
        self.assertEqual({c['qr_state'] for c in response.context['details']['comparisons']}, {'intact'})

    def test_qr_with_cross_drawn_inside_is_rejected_although_it_still_decodes(self):
        marked = self.mark_qr(1, [(.3, .3, .7, .7), (.7, .3, .3, .7)], width=4)
        response = self.verify(pdf_bytes=marked)
        self.assertEqual(response.context['result'], 'invalid')
        comparisons = response.context['details']['comparisons']
        self.assertEqual([c['qr_state'] for c in comparisons], ['intact', 'altered', 'intact'])
        self.assertGreaterEqual(comparisons[1]['qr_damage'], 0.40)
        self.assertIn('QR code on page 2 has been drawn on or defaced', response.context['details']['message'])
        self.assertTrue(AuditLog.objects.filter(action='reported_tampering', note__contains='p2=altered').exists())

    def test_qr_crossed_through_its_corners_is_reported_unreadable(self):
        marked = self.mark_qr(0, [(0, 0, 1, 1), (1, 0, 0, 1)], width=4)
        response = self.verify(pdf_bytes=marked)
        self.assertEqual(response.context['result'], 'invalid')
        self.assertEqual(response.context['details']['comparisons'][0]['qr_state'], 'unreadable')
        self.assertIn('QR code on page 1 could not be read', response.context['details']['message'])

    def test_small_mark_on_qr_requires_manual_review(self):
        marked = self.mark_qr(2, [(.55, .55, .7, .62)], width=2.4)
        response = self.verify(pdf_bytes=marked)
        self.assertEqual(response.context['result'], 'manual_review')
        comparison = response.context['details']['comparisons'][2]
        self.assertEqual((comparison['qr_state'], comparison['state']), ('damaged', 'manual_review'))

    def test_page_carrying_another_pages_qr_is_rejected(self):
        media_root = Path(self.media_directory.name)
        other_qr = media_root / 'page3.png'
        generate_qr_code(self.tokens[2], str(other_qr))

        def swap(document):
            document[1].draw_rect(self.QR_RECT, color=(1, 1, 1), fill=(1, 1, 1))
            document[1].insert_image(self.QR_RECT, filename=str(other_qr))
        response = self.verify(pdf_bytes=self.mutate_pdf(swap))
        self.assertEqual(response.context['result'], 'invalid')
        self.assertEqual(response.context['details']['comparisons'][1]['qr_state'], 'mismatch')

    def test_page_from_another_contract_is_invalid(self):
        _contract, _version, other_tokens, _data = self.issue_document(
            'Contract B', ['Other one', 'Other two', 'Other three']
        )
        response = self.verify([self.tokens[0], other_tokens[1], self.tokens[2]])
        self.assertEqual(response.context['result'], 'invalid')

    def test_page_from_another_version_is_invalid(self):
        _contract, _version, newer_tokens, _data = self.issue_document(
            'Contract A v2', ['New one', 'New two', 'New three'], self.contract, 2
        )
        response = self.verify([self.tokens[0], newer_tokens[1], self.tokens[2]])
        self.assertEqual(response.context['result'], 'invalid')

    def test_reordered_pages_are_invalid(self):
        response = self.verify([self.tokens[0], self.tokens[2], self.tokens[1]])
        self.assertEqual(response.context['result'], 'invalid')
        self.assertEqual(response.context['details']['submitted_order'], [1, 3, 2])

    def test_captured_pages_must_match_the_scanned_codes(self):
        response = self.verify(self.tokens[:2])
        self.assertEqual(response.context['result'], 'invalid')
        self.assertIn('3 page(s) were captured for 2 scanned QR code(s)', response.context['details']['message'])

    def test_only_the_scanned_pages_need_to_be_captured(self):
        def keep_pages_three_and_one(document):
            document.select([2, 0])
        response = self.verify(
            [self.tokens[2], self.tokens[0]], pdf_bytes=self.mutate_pdf(keep_pages_three_and_one)
        )
        details = response.context['details']
        self.assertEqual(response.context['result'], 'verified')
        self.assertEqual((details['checked_pages'], details['unchecked_pages']), ([1, 3], [2]))
        self.assertIn('The other 1 page(s) were not checked', details['message'])
        self.assertContains(response, 'Pages checked: 1, 3 of 3 (1 not checked)')
        self.assertTrue(AuditLog.objects.filter(note__contains='pages checked=2/3').exists())

    def test_duplicate_page_is_reported(self):
        response = self.verify([self.tokens[0], self.tokens[1], self.tokens[1]])
        self.assertEqual(response.context['result'], 'invalid')
        self.assertEqual(response.context['details']['duplicate_pages'], [2])

    def scan_frame(self, pdf_bytes, page_index):
        """Render one page the way a phone camera frame shows it."""
        with fitz.open(stream=pdf_bytes, filetype='pdf') as document:
            png = document[page_index].get_pixmap(dpi=200).tobytes('png')
        return SimpleUploadedFile('qr-frame.png', png, content_type='image/png')

    def test_scanned_qr_that_was_drawn_on_is_rejected_without_showing_the_page(self):
        # The code still decodes, but the page must not open for a defaced QR.
        marked = self.mark_qr(1, [(.3, .3, .7, .7), (.7, .3, .3, .7)], width=4)
        response = self.scan_qr(1, marked)
        payload = response.json()
        self.assertEqual(response.status_code, 422)
        self.assertFalse(payload['valid'])
        self.assertTrue(payload['tampered'])
        self.assertIn('may have been tampered with', payload['message'])
        self.assertNotIn('page_pdf_url', payload)
        self.assertNotIn('thumbnail', payload)
        self.assertTrue(AuditLog.objects.filter(
            action='reported_tampering', note__contains='page 2; condition=altered',
        ).exists())

    def test_scanned_qr_with_a_small_mark_is_rejected(self):
        marked = self.mark_qr(2, [(.55, .55, .7, .62)], width=2.4)
        payload = self.scan_qr(2, marked).json()
        self.assertTrue(payload['tampered'])

    def test_clean_scanned_qr_opens_its_page(self):
        payload = self.scan_qr(0).json()
        self.assertTrue(payload['valid'])
        self.assertEqual(payload['qr_condition'], 'intact')
        self.assertIn('page_pdf_url', payload)

    def test_scanned_qr_is_held_until_its_condition_can_be_checked(self):
        for frame in (None, SimpleUploadedFile('qr-frame.jpg', b'not an image', content_type='image/jpeg')):
            data = {'qr_token': self.tokens[0]}
            if frame:
                data['qr_frame'] = frame
            payload = self.client.post(reverse('verify_physical_qr'), data).json()
            self.assertEqual((payload['valid'], payload.get('retry')), (False, True))
            self.assertNotIn('page_pdf_url', payload)
        self.assertFalse(AuditLog.objects.filter(action='reported_tampering').exists())

    def test_code_the_phone_reads_but_the_server_cannot_is_rejected_after_retries(self):
        # The phone decoded the code, yet three frames could not be re-read: drawn over.
        def post(attempt, frame=True):
            data = {'qr_token': self.tokens[0], 'attempt': str(attempt)}
            if frame:
                data['qr_frame'] = SimpleUploadedFile('qr-frame.jpg', b'not an image', content_type='image/jpeg')
            return self.client.post(reverse('verify_physical_qr'), data)
        self.assertTrue(post(2).json()['retry'])
        self.assertTrue(post(3, frame=False).json()['retry'])
        response = post(3)
        self.assertEqual(response.status_code, 422)
        self.assertTrue(response.json()['tampered'])
        self.assertTrue(AuditLog.objects.filter(
            action='reported_tampering', note__contains='page 1; condition=unreadable',
        ).exists())

    def test_incorrect_total_page_information_is_invalid(self):
        payload = json.loads(base64.urlsafe_b64decode(
            self.tokens[0][5:] + '=' * (-len(self.tokens[0][5:]) % 4)
        ))
        payload['n'] = 99
        encoded = base64.urlsafe_b64encode(canonical_json(payload).encode()).rstrip(b'=').decode()
        response = self.verify(['SGP1.' + encoded, *self.tokens[1:]])
        self.assertEqual(response.context['result'], 'invalid')

    def test_modified_page_token_is_invalid(self):
        response = self.verify([self.tokens[0][:-1] + 'A', *self.tokens[1:]])
        self.assertEqual(response.context['result'], 'invalid')

    def test_invalid_manifest_signature_is_invalid(self):
        manifest = self.version.physical_manifest
        manifest.signature = base64.b64encode(b'not a signature').decode()
        manifest.save(update_fields=['signature'])
        response = self.verify()
        self.assertEqual(response.context['result'], 'invalid')

    def test_copied_valid_qr_on_altered_page_detects_differences(self):
        def alter(document):
            page = document[0]
            # Replace the content but keep the genuine page QR.
            page.add_redact_annot(fitz.Rect(0, 0, page.rect.width, self.QR_RECT.y0 - 10), fill=(1, 1, 1))
            page.apply_redactions()
            page.insert_text((72, 90), 'FORGED PAGE amount PHP 1,500', fontsize=16)
        response = self.verify(pdf_bytes=self.mutate_pdf(alter))
        self.assertEqual(response.context['result'], 'differences')

    def test_authorized_staff_can_open_difference_comparison(self):
        self.client.force_login(self.user)
        def alter(document):
            document[0].insert_text((72, 140), 'UNAUTHORIZED CLAUSE ' * 12, fontsize=14)
        response = self.verify(pdf_bytes=self.mutate_pdf(alter))
        review_url = response.context['details']['review_url']
        review = self.client.get(review_url)
        self.assertEqual(review.status_code, 200)
        self.assertContains(review, 'Submitted scan')
        self.assertContains(review, 'Registered official version')

    def test_slight_visual_degradation_with_same_text_is_accepted(self):
        degraded = self.mutate_pdf(
            lambda document: document[0].draw_rect(
                fitz.Rect(5, 5, 15, 15), color=(0.9, 0.9, 0.9), fill=(0.9, 0.9, 0.9)
            )
        )
        response = self.verify(pdf_bytes=degraded)
        self.assertEqual(response.context['result'], 'verified')

    def test_image_only_poor_scan_requires_manual_review(self):
        # No readable text, but each page's QR is present and intact.
        document = fitz.open()
        for index, token in enumerate(self.tokens):
            qr_path = Path(self.media_directory.name) / f'poor_scan_{index}.png'
            generate_qr_code(token, str(qr_path))
            document.new_page().insert_image(self.QR_RECT, filename=str(qr_path))
        poor_scan = document.tobytes()
        document.close()
        response = self.verify(pdf_bytes=poor_scan)
        self.assertEqual(response.context['result'], 'manual_review')

    def test_initial_enrollment_creates_signed_manifest_and_page_tokens(self):
        self.client.force_login(self.user)
        upload = SimpleUploadedFile('issued.pdf', self.pdf_bytes, content_type='application/pdf')
        response = self.client.post(reverse('upload_contract'), {'title': 'Issued', 'file': upload})
        self.assertEqual(response.status_code, 302)
        issued = Contract.objects.get(title='Issued')
        manifest = issued.versions.get(version_number=1).physical_manifest
        self.assertTrue(verify_manifest_signature(
            manifest.manifest, manifest.signature, self.public_key_path
        ))
        self.assertEqual(manifest.manifest['total_pages'], 3)

    def test_signed_scan_gets_new_manifest_and_new_encryption(self):
        self.client.force_login(self.user)
        old_manifest_id = self.version.physical_manifest.manifest_id
        upload = SimpleUploadedFile('signed.pdf', self.pdf_bytes, content_type='application/pdf')
        response = self.client.post(
            reverse('upload_signed_scan', args=[self.contract.id]), {'scanned_file': upload}
        )
        self.assertEqual(response.status_code, 302)
        new_version = self.contract.versions.get(version_number=2)
        self.contract.refresh_from_db()
        self.assertEqual(new_version.source, 'physical_scan')
        self.assertNotEqual(new_version.physical_manifest.manifest_id, old_manifest_id)
        self.assertTrue(new_version.encrypted_cf)
        self.assertEqual(self.contract.file.name, new_version.file.name)
