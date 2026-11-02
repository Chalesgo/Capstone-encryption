# Copy of contracts/tests.py, class DocumentManagementTests (lines 993-1506) plus the file's imports.
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


class DocumentManagementTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_directory = tempfile.TemporaryDirectory()
        cls.media_override = override_settings(MEDIA_ROOT=cls.media_directory.name)
        cls.media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.media_override.disable()
        cls.media_directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.admin = User.objects.create_superuser(
            'document_admin', 'document-admin@example.com', 'Document-password-2026!'
        )
        self.staff = User.objects.create_user(
            'document_staff', password='Document-password-2026!', is_staff=True
        )
        self.client.force_login(self.admin)

    @staticmethod
    def make_contract(number, user=None, trashed=False):
        owner = user or User.objects.first()
        contract = Contract.objects.create(
            title=f'Document Management {number}',
            recipient=owner, uploaded_by=owner,
            file=SimpleUploadedFile(
                f'document-management-{number}.pdf',
                b'%PDF-1.7\nDocument management test fixture',
                content_type='application/pdf',
            ),
            is_trashed=trashed,
        )
        return contract

    def test_doc03_metadata_storage_matrix(self):
        contracts = [self.make_contract(number, self.staff) for number in range(20)]
        for contract in contracts:
            with self.subTest(contract=contract.id):
                contract.refresh_from_db()
                self.assertTrue(contract.title)
                self.assertEqual(contract.recipient, self.staff)
                self.assertIsNotNone(contract.uploaded_at)
                self.assertEqual(contract.status, 'pending')
                self.assertFalse(contract.is_trashed)
                self.assertTrue(contract.file.name)

    def test_doc01_valid_pdf_upload_matrix(self):
        document = fitz.open()
        document.new_page().insert_text((72, 72), 'Valid upload fixture')
        base_pdf = document.tobytes()
        document.close()
        valid_files = []
        for number in range(10):
            valid_files.append((f'below-{number}.pdf', base_pdf))
        for number in range(10):
            valid_files.append((f'medium-{number}.pdf', base_pdf + (b'0' * (1024 * 1024))))
        for number in range(5):
            valid_files.append((f'near-limit-{number}.pdf', base_pdf + (b'0' * (14 * 1024 * 1024))))
        for number in range(5):
            document = fitz.open()
            for page in range(4):
                document.new_page().insert_text((72, 72), f'Mixed-content page {page}')
            pdf_bytes = document.tobytes()
            document.close()
            valid_files.append((f'multi-page-{number}.pdf', pdf_bytes))

        accepted = 0
        for filename, contents in valid_files:
            form = ContractForm(data={'title': filename}, files={
                'file': SimpleUploadedFile(filename, contents, content_type='application/pdf')
            })
            with self.subTest(filename=filename):
                self.assertTrue(form.is_valid(), form.errors)
                accepted += 1
        self.assertEqual(accepted, 30)

    def test_upload_can_store_a_pdf_without_encryption(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), 'Staged document without encryption')
        pdf_bytes = document.tobytes()
        document.close()

        response = self.client.post(reverse('upload_contract'), {
            'title': 'staged-document',
            'skip_encryption': '1',
            'file': SimpleUploadedFile('staged-document.pdf', pdf_bytes, content_type='application/pdf'),
        })

        self.assertEqual(response.status_code, 302)
        contract = Contract.objects.get(title='staged-document')
        version = contract.versions.get(version_number=1)
        self.assertTrue(contract.file)
        self.assertTrue(contract.original_fingerprint)
        self.assertEqual(contract.fingerprint, contract.original_fingerprint)
        self.assertFalse(contract.encrypted_cf)
        self.assertEqual(version.source, 'upload-unencrypted')
        self.assertFalse(version.encrypted_cf)
        self.assertTrue(Path(contract.file.path).is_file())

    def test_doc02_invalid_upload_rejection_matrix(self):
        invalid_files = []
        invalid_files.extend(
            (f'not-pdf-{number}.txt', b'plain text') for number in range(5)
        )
        invalid_files.extend(
            (f'zero-{number}.pdf', b'') for number in range(5)
        )
        invalid_files.extend(
            (f'malformed-{number}.pdf', b'%PDF-not-a-real-document') for number in range(5)
        )
        invalid_files.extend(
            (f'renamed-{number}.pdf', b'harmless file content') for number in range(5)
        )
        invalid_files.extend(
            (f'oversized-{number}.pdf', b'%PDF-' + (b'0' * (15 * 1024 * 1024 + 1)))
            for number in range(5)
        )
        for number in range(5):
            document = fitz.open()
            document.new_page().insert_text((72, 72), 'Password protected fixture')
            document.save(
                Path(self.media_directory.name) / f'protected-{number}.pdf',
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw='owner-password',
                user_pw='user-password',
                permissions=0,
            )
            document.close()
            invalid_files.append(
                (f'protected-{number}.pdf', (Path(self.media_directory.name) / f'protected-{number}.pdf').read_bytes())
            )

        rejected = 0
        for filename, contents in invalid_files:
            form = ContractForm(data={'title': filename}, files={
                'file': SimpleUploadedFile(filename, contents, content_type='application/pdf')
            })
            with self.subTest(filename=filename):
                self.assertFalse(form.is_valid())
                self.assertTrue(form.errors.get('file'))
                rejected += 1
        self.assertEqual(rejected, 30)

    def test_doc04_authorized_view_and_download_matrix(self):
        contracts = [self.make_contract(number, self.staff) for number in range(20)]
        view_count_before = AuditLog.objects.filter(action='viewed').count()
        download_count_before = AuditLog.objects.filter(action='downloaded').count()
        for contract in contracts:
            with self.subTest(contract=contract.id):
                viewed = self.client.post(reverse('mark_contract_viewed', args=[contract.id]))
                downloaded = self.client.get(reverse('download_contract', args=[contract.id]))
                self.assertEqual(viewed.status_code, 200)
                self.assertEqual(downloaded.status_code, 200)
                downloaded.close()
        self.assertEqual(
            AuditLog.objects.filter(action='viewed').count(),
            view_count_before + 20,
        )
        self.assertEqual(
            AuditLog.objects.filter(action='downloaded').count(),
            download_count_before + 20,
        )

    def test_download_names_use_system_title_and_version_number(self):
        contract = self.make_contract(6, self.staff)
        contract.title = '06_blank_page_contract'
        contract.save(update_fields=['title'])
        version = ContractVersion.objects.create(
            contract=contract,
            version_number=2,
            source='revision',
            file=contract.file.name,
            created_by=self.staff,
        )

        current = self.client.get(reverse('download_contract', args=[contract.id]))
        selected = self.client.get(reverse('download_contract_version', args=[version.id]))

        self.assertIn('06_blank_page_contract_v2.pdf', current['Content-Disposition'])
        self.assertIn('06_blank_page_contract_v2.pdf', selected['Content-Disposition'])
        current.close()
        selected.close()

    def test_doc05_unauthorized_view_and_download_matrix(self):
        contracts = [self.make_contract(number, self.staff) for number in range(20)]
        public_client = Client()
        for contract in contracts:
            with self.subTest(contract=contract.id):
                viewed = public_client.post(reverse('mark_contract_viewed', args=[contract.id]))
                downloaded = public_client.get(reverse('download_contract', args=[contract.id]))
                self.assertEqual(viewed.status_code, 302)
                self.assertEqual(downloaded.status_code, 302)

    def test_doc06_archive_lifecycle_matrix(self):
        for number in range(10):
            contract = self.make_contract(number, self.staff)
            with self.subTest(contract=contract.id):
                archived = self.client.post(reverse('delete_contract', args=[contract.id]), {'reason': 'duplicate'})
                self.assertEqual(archived.status_code, 302)
                contract.refresh_from_db()
                self.assertTrue(contract.is_trashed)

                restored = self.client.post(reverse('restore_contract', args=[contract.id]))
                self.assertEqual(restored.status_code, 200)
                contract.refresh_from_db()
                self.assertFalse(contract.is_trashed)

                archived_again = self.client.post(reverse('delete_contract', args=[contract.id]), {'reason': 'duplicate'})
                self.assertEqual(archived_again.status_code, 302)
                contract.refresh_from_db()
                self.assertTrue(contract.is_trashed)

    def test_doc07_version_control_matrix(self):
        for number in range(10):
            contract = self.make_contract(number, self.staff)
            previous = ''
            for version_number in range(1, 4):
                fingerprint = f'{contract.id:020d}{version_number:044d}'
                version = ContractVersion.objects.create(
                    contract=contract,
                    version_number=version_number,
                    source='upload' if version_number == 1 else 'revision',
                    file=SimpleUploadedFile(
                        f'contract-{contract.id}-v{version_number}.pdf',
                        b'%PDF-version-test',
                    ),
                    fingerprint=fingerprint,
                    previous_fingerprint=previous,
                    created_by=self.staff,
                )
                with self.subTest(contract=contract.id, version=version_number):
                    self.assertEqual(version.version_number, version_number)
                    self.assertEqual(version.previous_fingerprint, previous)
                    self.assertIsNotNone(version.created_at)
                previous = fingerprint

            self.assertEqual(
                list(contract.versions.order_by('version_number').values_list('version_number', flat=True)),
                [1, 2, 3],
            )

    def test_doc09_search_matrix(self):
        titles = [
            'Barangay Alpha Agreement', 'Barangay Beta Agreement',
            'Community Gamma Contract', 'Community Delta Contract',
        ]
        for number in range(20):
            self.make_contract(number, self.staff).title = titles[number % len(titles)]
            Contract.objects.filter(pk=Contract.objects.latest('id').id).update(
                title=titles[number % len(titles)]
            )

        queries = (
            [(title, 5) for title in titles[:2] for _ in range(5)]
            + [('Agreement', 10)] * 5
            + [('agreement', 10)] * 5
            + [('no-such-document', 0)] * 10
        )
        for query, expected_count in queries:
            with self.subTest(query=query):
                response = self.client.get(reverse('contract_list'), {'q': query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['page_obj'].paginator.count, expected_count)

    def test_doc10_filter_and_pagination_matrix(self):
        for number in range(50):
            self.make_contract(number, self.staff)
        for per_page in (10, 25, 50):
            for status in ('pending', 'sent', 'approved', 'all', 'unknown'):
                response = self.client.get(reverse('contract_list'), {
                    'status': status,
                    'per_page': per_page,
                })
                self.assertEqual(response.context['page_obj'].paginator.per_page, per_page)

    def test_doc11_duplicate_upload_detection_matrix(self):
        pdf_path = Path(self.media_directory.name) / 'duplicate-source.pdf'
        document = fitz.open()
        document.new_page().insert_text((72, 72), 'Duplicate detection fixture')
        document.save(pdf_path)
        document.close()
        pdf_bytes = pdf_path.read_bytes()
        fingerprint = generate_canonical_fingerprint(pdf_path)
        existing = Contract.objects.create(
            title='Existing duplicate.pdf',
            recipient=self.staff,
            original_fingerprint=fingerprint,
            file=SimpleUploadedFile('existing.pdf', pdf_bytes),
        )

        for attempt in range(20):
            with self.subTest(attempt=attempt + 1):
                response = self.client.post(
                    reverse('check_duplicate_upload'),
                    {'file': SimpleUploadedFile('incoming.pdf', pdf_bytes, content_type='application/pdf')},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['duplicates'][0]['id'], existing.id)
                self.assertEqual(response.json()['duplicates'][0]['match_type'], 'same_content')
        self.assertEqual(Contract.objects.count(), 1)

    def make_named_contract(self, number, name):
        contract = self.make_contract(number, self.staff)
        contract.title = name
        contract.base_filename = name
        contract.save(update_fields=['title', 'base_filename'])
        ContractVersion.objects.create(
            contract=contract, version_number=1, source='upload',
            file=contract.file.name, created_by=self.staff,
        )
        return contract

    @staticmethod
    def pdf_with_text(text):
        document = fitz.open()
        document.new_page().insert_text((72, 72), text)
        data = document.tobytes()
        document.close()
        return data

    def test_same_name_is_only_a_suggestion_and_never_creates_a_revision(self):
        existing = self.make_named_contract(60, '06_blank_page_contract')
        changed_pdf = self.pdf_with_text('Changed revision contents')

        duplicate_check = self.client.post(reverse('check_duplicate_upload'), {
            'title': '06_blank_page_contract',
            'file': SimpleUploadedFile('06_blank_page_contract.pdf', changed_pdf, content_type='application/pdf'),
        })
        match = duplicate_check.json()['duplicates'][0]
        self.assertEqual((match['id'], match['match_type']), (existing.id, 'same_name'))
        self.assertEqual((match['current_version'], match['next_version']), (1, 2))
        self.assertEqual(match['current_preview_url'],
                         reverse('preview_contract_version', args=[existing.versions.get().pk]))

        with patch('contracts.views.add_revision') as revision:
            response = self.client.post(reverse('upload_contract'), {
                'title': '06_blank_page_contract',
                'file': SimpleUploadedFile('06_blank_page_contract.pdf', changed_pdf, content_type='application/pdf'),
            })
        revision.assert_not_called()
        self.assertContains(response, 'This file matches')
        self.assertEqual(Contract.objects.count(), 1)
        self.assertEqual(existing.versions.count(), 1)

        upload_page = self.client.get(reverse('upload_contract'))
        for marker in ('id="duplicate-current-canvas"', 'id="duplicate-incoming-canvas"',
                       'id="duplicate-new-btn"', 'id="duplicate-revision-btn"', 'contracts/pdfjs/pdf.mjs'):
            self.assertContains(upload_page, marker)

    def test_identical_content_is_reported_before_a_shared_name(self):
        pdf_bytes = self.pdf_with_text('Identical content fixture')
        pdf_path = Path(self.media_directory.name) / 'identical.pdf'
        pdf_path.write_bytes(pdf_bytes)
        same_content = self.make_named_contract(61, 'Supply agreement')
        same_content.original_fingerprint = generate_canonical_fingerprint(pdf_path)
        same_content.save(update_fields=['original_fingerprint'])
        same_name = self.make_named_contract(62, 'renamed_copy')

        response = self.client.post(reverse('check_duplicate_upload'), {
            'title': 'renamed_copy',
            'file': SimpleUploadedFile('renamed_copy.pdf', pdf_bytes, content_type='application/pdf'),
        })
        matches = response.json()['duplicates']
        self.assertEqual([(m['id'], m['match_type']) for m in matches],
                         [(same_content.id, 'same_content'), (same_name.id, 'same_name')])

    def test_revision_page_compares_against_the_current_version(self):
        existing = self.make_named_contract(63, 'Road repair contract')
        page = self.client.get(reverse('add_revision', args=[existing.id]))
        self.assertContains(page, 'id="revision-target"')
        self.assertContains(page, reverse('preview_contract_version', args=[existing.versions.get().pk]))
        self.assertContains(page, 'Confirm the new version')

    def test_doc12_report_filter_and_export_matrix(self):
        contract = self.make_contract(1, self.staff)
        AuditLog.objects.create(
            contract=contract,
            user=self.staff,
            action='added',
            document_title=contract.title,
        )
        AuditLog.objects.create(
            contract=contract,
            user=self.admin,
            action='deleted',
            document_title=contract.title,
        )
        combinations = [
            {'activity': 'added'},
            {'activity': 'deleted'},
            {'activity': 'added', 'sort': 'oldest'},
            {'activity': 'deleted', 'sort': 'newest'},
            {'user': str(self.staff.id)},
            {'user': str(self.admin.id)},
            {'status': 'pending'},
            {'document': 'Document Management'},
            {'date_from': timezone.now().date().isoformat()},
            {'date_to': timezone.now().date().isoformat()},
        ] * 2
        for query in combinations:
            with self.subTest(query=query):
                response = self.client.get(reverse('dashboard'), query)
                self.assertEqual(response.status_code, 200)
        export = self.client.get(reverse('export_dashboard_report'), {'activity': 'added'})
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export['Content-Type'], 'text/csv')
        self.assertIn(b'Document,User,Action,Timestamp,IP address,Details', export.content)

    def test_bulk_staff_actions_move_status_download_and_encrypt(self):
        first = self.make_contract(201, self.staff)
        second = self.make_contract(202, self.staff)
        second.encrypted_cf = 'already-encrypted'
        second.save(update_fields=['encrypted_cf'])
        folder = Folder.objects.create(name='Bulk destination', owner=self.admin)
        self.client.force_login(self.staff)

        move = self.client.post(reverse('bulk_assign_folder'), {
            'ids': [first.id, second.id], 'folder_id': folder.id,
        })
        self.assertEqual(move.status_code, 200)
        self.assertEqual(move.json()['updated'], 2)
        self.assertEqual(
            set(Contract.objects.filter(pk__in=[first.id, second.id]).values_list('folder_id', flat=True)),
            {folder.id},
        )

        status = self.client.post(reverse('bulk_update_status'), {
            'ids': [first.id, second.id], 'status': 'approved',
        })
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()['updated'], 2)
        self.assertEqual(
            set(Contract.objects.filter(pk__in=[first.id, second.id]).values_list('status', flat=True)),
            {'approved'},
        )

        download = self.client.post(reverse('bulk_download_contracts'), {
            'ids': [first.id, second.id],
        })
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            self.assertEqual(len(archive.namelist()), 2)

        with patch('contracts.views.encrypt_contract', return_value=HttpResponse(status=302)) as encrypt:
            encrypted = self.client.post(reverse('bulk_encrypt_contracts'), {
                'ids': [first.id, second.id],
            })
        self.assertEqual(encrypted.status_code, 200)
        self.assertEqual(encrypted.json()['encrypted'], 1)
        self.assertEqual(encrypted.json()['already_encrypted'], 1)
        encrypt.assert_called_once()

    def assert_publicly_listed(self, contract, listed):
        public_page = Client().get(reverse('public_verify'))
        public_ids = {item['contract'].id for item in public_page.context['public_contracts']}
        self.assertEqual(contract.id in public_ids, listed)

    def test_returning_final_copy_to_draft_removes_it_from_public_view(self):
        contract = self.make_contract(301, self.admin)
        self.client.post(reverse('update_status', args=[contract.id]), {'status': 'final'})
        contract.refresh_from_db()
        self.assertTrue(contract.is_public)
        self.assert_publicly_listed(contract, True)

        self.client.post(reverse('update_status', args=[contract.id]), {'status': 'pending'})
        contract.refresh_from_db()
        self.assertEqual((contract.status, contract.is_public), ('pending', False))
        self.assert_publicly_listed(contract, False)
        self.assertTrue(AuditLog.objects.filter(
            contract=contract, note__contains='removed from public verified documents',
        ).exists())

    def test_bulk_return_to_draft_removes_documents_from_public_view(self):
        first = self.make_contract(302, self.admin)
        second = self.make_contract(303, self.admin)
        ids = [first.id, second.id]
        self.client.post(reverse('bulk_update_status'), {'ids': ids, 'status': 'final'})
        response = self.client.post(reverse('bulk_update_status'), {'ids': ids, 'status': 'pending'})
        self.assertEqual(response.json()['updated'], 2)
        self.assertFalse(Contract.objects.filter(pk__in=ids, is_public=True).exists())
        self.assert_publicly_listed(first, False)

    def test_draft_cannot_be_made_public_directly(self):
        contract = self.make_contract(304, self.admin)
        response = self.client.post(
            reverse('publish_contract', args=[contract.id]),
            data=json.dumps({'is_public': True}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'final_copy_required')
        contract.refresh_from_db()
        self.assertFalse(contract.is_public)

    def test_bulk_toolbar_allows_document_administrators(self):
        self.make_contract(203, self.staff)
        self.client.force_login(self.staff)

        page = self.client.get(reverse('contract_list'))

        self.assertContains(page, 'id="bulk-move-folder"')
        self.assertContains(page, 'id="bulk-change-status"')
        self.assertContains(page, 'id="bulk-download-contracts"')
        self.assertContains(page, 'id="bulk-encrypt-contracts"')
        self.assertContains(page, 'id="bulk-delete-contracts"')
        response = self.client.post(reverse('bulk_delete_contracts'), {'ids': []})
        self.assertEqual(response.status_code, 200)


