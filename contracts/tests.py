from .pdf_storage import read_pdf, write_pdf
from datetime import timedelta
import base64
import json
import hashlib
import io
import os
import tempfile
import zipfile
from io import StringIO
from pathlib import Path
from unittest import expectedFailure
from unittest.mock import patch

import fitz
from Crypto.PublicKey import RSA
from django.contrib.auth.models import Permission, User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.core.management import call_command
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
    generate_vector_fingerprint,
    log_activity,
    stamp_seal_on_pdf,
    verify_hmac,
    verify_version_chain,
)


class AuditLogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin', 'admin@example.com', 'password')
        self.contract = Contract.objects.create(title='Senior Assistance Form')
        self.contract.file.save(
            'test.pdf', ContentFile(b'%PDF-SealGuard audit test fixture'), save=True
        )

    def test_admin_uses_sealguard_branding(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('admin:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SealGuard')
        self.assertContains(response, 'Administration Portal')
        self.assertContains(response, 'contracts/sealguard-admin.css')
        self.assertContains(response, 'Back to SealGuard')

    @patch('contracts.management.commands.verify_integrity.verify_version_chain', return_value=[])
    def test_integrity_scan_reports_failed_version(self, verify_chain):
        ContractVersion.objects.create(
            contract=self.contract,
            version_number=1,
            file=self.contract.file.name,
            fingerprint='stored-fingerprint',
            previous_fingerprint='',
        )

        output = StringIO()
        call_command('verify_integrity', stdout=output, stderr=output)

        self.assertIn('1 version(s)', output.getvalue())
        self.assertIn('1 failure(s)', output.getvalue())
        self.assertTrue(AuditLog.objects.filter(
            contract=self.contract,
            action='reported_tampering',
            verification_source='Scheduled Integrity Scan',
            integrity_check='Failed',
        ).exists())
        verify_chain.assert_called_once_with(self.contract)

    def test_document_title_survives_permanent_deletion(self):
        request = RequestFactory().post('/')
        request.user = self.user
        log_activity(request, 'added', contract=self.contract)

        self.client.force_login(self.user)
        self.contract.is_trashed = True
        self.contract.trashed_at = timezone.now()
        self.contract.save()
        response = self.client.post(reverse('permanently_delete_contract', args=[self.contract.id]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contract.objects.filter(id=self.contract.id).exists())
        titles = list(
            AuditLog.objects.exclude(action__in={'login', 'logout'})
            .values_list('document_title', flat=True)
        )
        self.assertEqual(titles, ['Senior Assistance Form', 'Senior Assistance Form'])

    def test_authentication_events_are_recorded(self):
        self.assertTrue(self.client.login(username='admin', password='password'))
        login_event = AuditLog.objects.get(action='login')
        self.assertEqual(login_event.user, self.user)
        self.assertEqual(login_event.note, 'Successful authentication')

        self.client.logout()
        logout_event = AuditLog.objects.get(action='logout')
        self.assertEqual(logout_event.user, self.user)

        self.client.post(reverse('login'), {'username': 'admin', 'password': 'wrong-password'})
        failed_event = AuditLog.objects.get(action='failed_login')
        self.assertIn('admin', failed_event.note)

        self.client.post(reverse('login'), {'username': '', 'password': ''})
        self.assertEqual(AuditLog.objects.filter(action='failed_login').count(), 2)

        login_page = self.client.get(reverse('login'))
        self.assertContains(login_page, '<form method="post" novalidate>', html=False)

    def test_opening_contract_view_records_viewed_event(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('mark_contract_viewed', args=[self.contract.id]))
        self.assertEqual(response.status_code, 200)
        viewed_event = AuditLog.objects.get(action='viewed')
        self.assertEqual(viewed_event.contract, self.contract)
        self.assertEqual(viewed_event.user, self.user)
        self.assertEqual(viewed_event.document_size, self.contract.file.size)

        preview = self.client.get(reverse('preview_contract', args=[self.contract.id]))
        self.assertEqual(preview.status_code, 200)
        preview.close()
        self.assertFalse(AuditLog.objects.filter(action='downloaded').exists())

        downloaded = self.client.get(reverse('download_contract', args=[self.contract.id]))
        downloaded.close()
        downloaded_event = AuditLog.objects.get(action='downloaded')
        self.assertEqual(downloaded_event.document_size, self.contract.file.size)

    def test_mobile_pdf_static_assets_are_served_when_debug_is_false(self):
        response = self.client.get('/static/contracts/mobile-pdf-host.js?v=details-3')

        self.assertEqual(response.status_code, 200)
        body = b''.join(response.streaming_content).decode()
        self.assertIn('SealGuardPdf', body)
        self.assertEqual(response.headers['Content-Type'].split(';')[0], 'application/javascript')

    def test_version_history_identifies_account_that_added_each_version(self):
        reviser = User.objects.create_user('revision_editor', password='password')
        ContractVersion.objects.create(
            contract=self.contract,
            version_number=1,
            source='upload',
            file=self.contract.file.name,
            created_by=self.user,
        )
        ContractVersion.objects.create(
            contract=self.contract,
            version_number=2,
            source='revision',
            file=self.contract.file.name,
            created_by=reviser,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('contract_version_history', args=[self.contract.id]))

        self.assertEqual(response.status_code, 200)
        versions = response.json()['versions']
        self.assertEqual(versions[0]['created_by'], 'revision_editor')
        self.assertEqual(versions[1]['created_by'], 'admin')
        self.assertContains(self.client.get(reverse('contract_list')), 'Added by')
        self.assertContains(self.client.get(reverse('dashboard')), 'Added by')

    @patch('contracts.views.verify_version_chain')
    def test_mobile_version_metadata_avoids_pdf_chain_work_and_requires_login(self, verify_chain):
        ContractVersion.objects.create(
            contract=self.contract, version_number=1, source='upload',
            file=self.contract.file.name, created_by=self.user,
        )
        url = reverse('contract_version_history', args=[self.contract.id])
        self.assertEqual(self.client.get(url, {'metadata_only': '1'}).status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(url, {'metadata_only': '1'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['title'], self.contract.title)
        self.assertEqual(data['versions'][0]['created_by'], self.user.username)
        self.assertIsNone(data['versions'][0]['valid'])
        verify_chain.assert_not_called()
        self.client.get(url)
        verify_chain.assert_not_called()

    def test_dashboard_filters_activity_and_sorts_oldest_first(self):
        added = AuditLog.objects.create(action='added', document_title='First')
        edited = AuditLog.objects.create(action='edited', document_title='Second')
        later_added = AuditLog.objects.create(action='added', document_title='Third')
        AuditLog.objects.filter(pk=added.pk).update(timestamp=timezone.now() - timedelta(days=2))
        AuditLog.objects.filter(pk=edited.pk).update(timestamp=timezone.now() - timedelta(days=1))
        AuditLog.objects.filter(pk=later_added.pk).update(timestamp=timezone.now())

        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'), {'activity': 'added', 'sort': 'oldest'})

        self.assertEqual(response.status_code, 200)
        logs = list(response.context['recent_logs'])
        self.assertEqual([log.action for log in logs], ['added', 'added'])
        self.assertEqual([log.document_title for log in logs], ['First', 'Third'])

    def test_dashboard_paginates_and_preserves_filter_controls(self):
        AuditLog.objects.bulk_create([
            AuditLog(action='deleted', document_title=f'Document {number}')
            for number in range(25)
        ])

        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'), {
            'activity': 'deleted',
            'sort': 'newest',
            'per_page': '10',
            'page': '2',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['page_obj']), 10)
        self.assertEqual(response.context['page_obj'].number, 2)
        self.assertEqual(response.context['page_obj'].paginator.count, 25)
        self.assertContains(response, 'class="btn btn-primary dashboard-apply-filters" type="submit"')
        self.assertContains(response, 'page=3')

    def test_dashboard_supports_multiple_persistent_activity_filters(self):
        added = AuditLog.objects.create(action='added', document_title='Added')
        tampered = AuditLog.objects.create(action='reported_tampering', document_title='Tampered')
        AuditLog.objects.create(action='deleted', document_title='Deleted')

        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'), {
            'activity': 'added,reported_tampering',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['activity_filters'], ['added', 'reported_tampering'])
        self.assertEqual(list(response.context['page_obj'].object_list), [tampered, added])
        self.assertContains(response, 'value="added" selected')
        self.assertContains(response, 'value="reported_tampering" selected')

        for activity in ('viewed', 'encrypted', 'approved', 'rejected'):
            self.assertContains(response, f'<option value="{activity}"')

    def test_dashboard_shows_integrity_scan_activity_with_debug_log(self):
        scan = AuditLog.objects.create(
            action='integrity_scan',
            document_title='Integrity Scan',
            note='Integrity scan completed.',
            verification_source='Scheduled Integrity Scan',
            verification_result='Completed',
            integrity_check='Complete',
            verification_debug_log='Checked 12 version(s).\nDuration: 450 ms.',
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'), {'activity': 'integrity_scan'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['page_obj'].object_list), [scan])
        self.assertContains(response, 'Integrity Scan')
        self.assertContains(response, 'Checked 12 version(s).')

    def test_dashboard_marks_deleted_pdf_as_unavailable_with_reason(self):
        self.contract.is_trashed = True
        self.contract.trashed_at = timezone.now()
        self.contract.save(update_fields=['is_trashed', 'trashed_at'])
        AuditLog.objects.create(
            action='deleted', contract=self.contract,
            document_title=self.contract.title,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PDF can't be viewed")
        self.assertContains(response, 'This document is in the trash.')
        self.assertContains(response, 'data-preview-url=""')

    def test_dashboard_mobile_pdf_action_sheet_preserves_details_access(self):
        AuditLog.objects.create(action='viewed', contract=self.contract, document_title=self.contract.title)

        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="audit-mobile-action-sheet"')
        self.assertContains(response, '>View PDF</span>')
        self.assertContains(response, '>Details</span>')
        self.assertContains(response, 'id="audit-mobile-download-pdf"')
        self.assertContains(response, '>Download PDF</span>')
        self.assertContains(response, 'function auditMobileDownloadPdf(event)')
        self.assertContains(response, 'openAuditMobileSheet(row)')
        self.assertContains(response, 'openAuditPdfDetails(row)')

    def test_document_search_is_debounced_in_the_browser(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('contract_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'overflow-x: hidden;')
        self.assertContains(response, "view=FitH")
        self.assertContains(response, 'min-width: 0;')
        self.assertContains(response, 'oninput="scheduleSearchFilter()"')
        self.assertContains(response, 'setTimeout(() =>')
        self.assertContains(response, '}, 350);')

    def test_contract_rows_open_pdf_from_non_control_area(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('contract_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'onclick="handleDocumentCellClick(event,')
        self.assertNotContains(response, 'onclick="handleContractRowClick(event,')
        self.assertContains(response, "event.target.closest('input, button, a, select, textarea, form")
        self.assertContains(response, 'openPdfPreview(contractId, row.dataset.fileUrl, row.dataset.contractTitle)')
        self.assertContains(response, 'id="contracts-doc-panel"')
        self.assertContains(response, 'id="mobile-contract-action-sheet"')
        self.assertContains(response, '>Details</span>')
        self.assertContains(response, 'function mobileSheetDetails()')
        self.assertContains(response, 'forceDetails = false')
        self.assertContains(response, 'id="mobile-contract-edit-sheet"')
        self.assertContains(response, 'function openMobileEditSheet(contractId)')
        self.assertContains(response, 'function mobileEditAddRevision()')
        self.assertContains(response, 'class="context-menu-item"')
        self.assertContains(response, 'class="context-menu-item danger"')
        self.assertContains(response, 'function handleDocumentCellClick(event, contractId)')
        self.assertContains(response, 'function getLiveFolderChoices()')
        self.assertContains(response, "document.querySelectorAll('#folder-list .folder-item')")
        self.assertContains(response, 'data-file-url=')

    def test_filename_demo_mode_is_preserved_and_disclosed(self):
        upload = SimpleUploadedFile(
            'authentic_demo.pdf', b'%PDF-demo', content_type='application/pdf'
        )

        response = self.client.post(reverse('public_verify'), {'pdf_file': upload})

        self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
        self.assertEqual(self.client.session['verify_result'], 'authentic')
        debug_log = self.client.session['verify_debug_log']
        self.assertIn(
            'Demo simulation enabled by filename; cryptographic checks were not executed',
            debug_log,
        )

    def test_tampered_verification_retains_pdf_and_inspection_details(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            upload = SimpleUploadedFile(
                'tampered_contract.pdf', b'%PDF-demo', content_type='application/pdf'
            )

            response = self.client.post(reverse('public_verify'), {'pdf_file': upload})

            self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
            audit_log = AuditLog.objects.get(action='reported_tampering')
            self.assertEqual(audit_log.document_title, 'tampered_contract.pdf')
            self.assertEqual(audit_log.verification_source, 'Official Barangay Database')
            self.assertEqual(audit_log.verification_result, 'Possible Modification')
            self.assertEqual(audit_log.integrity_check, 'Failed')
            self.assertIn('Verification pipeline initialized', audit_log.verification_debug_log)
            self.assertIn('document fingerprint mismatch', audit_log.verification_debug_log)
            self.assertTrue(audit_log.evidence_file)
            self.assertTrue(audit_log.evidence_file.storage.exists(audit_log.evidence_file.name))

            self.client.force_login(self.user)
            dashboard = self.client.get(reverse('dashboard'))
            self.assertContains(dashboard, 'Verification History')
            self.assertNotContains(dashboard, 'Verification pipeline initialized')

            history = self.client.get(
                reverse('audit_verification_history', args=[audit_log.id])
            )
            self.assertEqual(history.status_code, 200)
            self.assertEqual(history.json()['events'][0]['id'], audit_log.id)
            self.assertNotIn('debug_log', history.json()['events'][0])

            detail = self.client.get(
                reverse('audit_verification_log_detail', args=[audit_log.id])
            )
            self.assertEqual(detail.status_code, 200)
            self.assertIn('Verification pipeline initialized', detail.json()['debug_log'])

    def test_verification_history_keeps_repeated_attempts_collapsed_and_lazy(self):
        first = AuditLog.objects.create(
            contract=self.contract,
            action='viewed',
            verification_result='Authentic',
            integrity_check='Complete',
            verification_debug_log='first verification trace',
        )
        second = AuditLog.objects.create(
            contract=self.contract,
            action='reported_tampering',
            verification_result='Possible Modification',
            integrity_check='Failed',
            verification_debug_log='second verification trace',
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('audit_verification_history', args=[first.id])
        )

        self.assertEqual(response.status_code, 200)
        events = response.json()['events']
        self.assertEqual([event['id'] for event in events], [second.id, first.id])
        self.assertTrue(all('verified_at' in event for event in events))
        self.assertTrue(all('debug_log' not in event for event in events))

        detail = self.client.get(
            reverse('audit_verification_log_detail', args=[second.id])
        )
        self.assertEqual(detail.json()['debug_log'], 'second verification trace')

    def test_unknown_verification_logs_details_without_retaining_pdf(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            upload = SimpleUploadedFile(
                'unknown_contract.pdf', b'%PDF-demo', content_type='application/pdf'
            )

            response = self.client.post(reverse('public_verify'), {'pdf_file': upload})

            self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
            audit_log = AuditLog.objects.get(action='verification')
            self.assertEqual(self.client.session['verify_result'], 'error')
            self.assertEqual(audit_log.document_title, 'unknown_contract.pdf')
            self.assertEqual(audit_log.verification_result, 'Unable to Verify')
            self.assertEqual(audit_log.integrity_check, 'Incomplete')
            self.assertFalse(audit_log.evidence_file)

            self.client.force_login(self.user)
            dashboard = self.client.get(reverse('dashboard'))
            self.assertContains(dashboard, 'data-result="Unable to Verify"')
            self.assertContains(dashboard, 'Removed after verification')

    @patch('contracts.utils.generate_canonical_fingerprint')
    def test_version_chain_rejects_mismatched_previous_link(self, fingerprint):
        ContractVersion.objects.create(
            contract=self.contract,
            version_number=1,
            source='upload',
            file='contract_versions/v1.pdf',
            fingerprint='version-one',
            previous_fingerprint='',
        )
        ContractVersion.objects.create(
            contract=self.contract,
            version_number=2,
            source='revision',
            file='contract_versions/v2.pdf',
            fingerprint='version-two',
            previous_fingerprint='wrong-link',
        )
        fingerprint.side_effect = ['version-one', 'version-two']

        results = verify_version_chain(self.contract)

        self.assertTrue(results[0]['valid'])
        self.assertFalse(results[1]['valid'])

    def test_folder_reorder_is_persisted_for_current_user(self):
        first = Folder.objects.create(name='First', owner=self.user, sort_order=0)
        second = Folder.objects.create(name='Second', owner=self.user, sort_order=1)
        third = Folder.objects.create(name='Third', owner=self.user, sort_order=2)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('reorder_folders'),
            data={'folder_ids': [third.id, first.id, second.id]},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        ordered_ids = list(Folder.objects.filter(owner=self.user).values_list('id', flat=True))
        self.assertEqual(ordered_ids, [third.id, first.id, second.id])

    def test_staff_can_see_and_reorder_shared_folders(self):
        staff = User.objects.create_user('folder_clerk', password='password', is_staff=True)
        first = Folder.objects.create(name='Admin Folder', owner=self.user, sort_order=0)
        second = Folder.objects.create(name='Shared Folder', owner=self.user, sort_order=1)
        self.client.force_login(staff)

        page = self.client.get(reverse('contract_list'))
        self.assertContains(page, 'Admin Folder')
        self.assertContains(page, 'Shared Folder')

        response = self.client.post(
            reverse('reorder_folders'),
            data={'folder_ids': [second.id, first.id]},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(Folder.objects.order_by('sort_order').values_list('id', flat=True)),
            [second.id, first.id],
        )

    def test_staff_can_delete_empty_shared_folder(self):
        staff = User.objects.create_user('empty_folder_clerk', password='password', is_staff=True)
        folder = Folder.objects.create(name='Empty Folder', owner=self.user)
        self.client.force_login(staff)

        response = self.client.post(
            reverse('delete_folder', args=[folder.id]),
            data={'mode': 'unassign'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Folder.objects.filter(pk=folder.id).exists())

    def test_staff_cannot_delete_shared_folder_with_documents(self):
        staff = User.objects.create_user('full_folder_clerk', password='password', is_staff=True)
        folder = Folder.objects.create(name='Full Folder', owner=self.user)
        Contract.objects.create(
            title='Folder document', file='contracts/test.pdf', folder=folder
        )
        self.client.force_login(staff)

        response = self.client.post(
            reverse('delete_folder', args=[folder.id]),
            data={'mode': 'unassign'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Folder.objects.filter(pk=folder.id).exists())

    def test_folder_reorder_rejects_incomplete_order(self):
        first = Folder.objects.create(name='First', owner=self.user, sort_order=0)
        Folder.objects.create(name='Second', owner=self.user, sort_order=1)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('reorder_folders'),
            data={'folder_ids': [first.id]},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_verification_loading_layout_locks_page_scroll(self):
        response = self.client.get(reverse('public_verify'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'overflow-x: hidden;')
        self.assertContains(response, 'id="public-pdf-overlay"')
        self.assertContains(response, 'onclick="closePublicPdfPreview()"')
        self.assertContains(response, "event.key === 'Escape'")
        self.assertContains(response, 'id="mobile-doc-toggle"')
        self.assertContains(response, 'id="public-doc-panel"')
        self.assertContains(response, 'togglePublicDocsDrawer()')
        self.assertContains(response, "body.verification-active { overflow: hidden; }")
        self.assertContains(response, "document.body.classList.add('verification-active')")
        self.assertContains(response, "grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.75fr)")
        self.assertNotContains(response, "width: 100vw")

    def test_verification_results_reuse_loading_layout_and_light_log(self):
        session = self.client.session
        session['verify_result'] = 'error'
        session['verify_debug_log'] = ['[ERROR] Test verification failure']
        session.save()

        response = self.client.get(reverse('public_verify'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This document could not be verified — it may not have been issued or processed by this system.')
        self.assertContains(response, 'statusPanel.appendChild(resultSection)')
        self.assertContains(response, "statusPanel.classList.add('result-state')")
        self.assertContains(response, 'background: #f7f8fa;')
        self.assertNotContains(response, 'background: #0f172a;')

    def test_contract_preview_is_non_blocking_when_closed_on_mobile(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('contract_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'visibility: hidden;')
        self.assertContains(response, 'pointer-events: none;')
        self.assertContains(response, 'min-width: 0;')
        self.assertContains(response, 'function syncPdfPreviewLayers()')

    def test_mobile_navigation_replaces_sidebar(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('contract_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<nav class="mobile-nav" aria-label="Mobile navigation">')
        self.assertContains(response, '.sidebar { display: none; }')
        self.assertContains(response, 'body { padding-left: 0; }')
        self.assertContains(response, 'title="Contracts" aria-label="Contracts"')
        self.assertContains(response, 'id="contracts-doc-toggle"')
        self.assertContains(response, 'id="profile-trigger"')
        self.assertNotContains(response, 'title="Logout" aria-label="Logout"')

    def test_help_page_lists_questions_and_icon_picker(self):
        Tutorial.objects.create(
            title='How to encrypt a contract',
            summary='Encrypt a PDF contract safely.',
            content='<p>Select <strong>Add Document</strong>.</p>',
            icon='lock',
            created_by=self.user,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('help_tutorials'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Questions')
        self.assertContains(response, 'How to encrypt a contract')
        self.assertContains(response, '+ Add Tutorial')
        self.assertContains(response, 'id="tutorial-icon-lock"')
        self.assertContains(response, 'contenteditable="true"')
        self.assertContains(response, 'How to verify a physical document')

    def test_tutorial_inline_icons_are_whitelisted_and_sanitized(self):
        content = (
            '<p><strong>1.</strong> '
            '<span class="tutorial-inline-icon" data-icon="folder">'
            '<svg><use href="#bad"></use></svg></span> Open Contracts.</p>'
        )

        sanitized = sanitize_tutorial_html(content)

        self.assertIn('data-icon="folder"', sanitized)
        self.assertIn('#tutorial-icon-folder', sanitized)
        self.assertNotIn('#bad', sanitized)

    def test_admin_can_create_sanitized_tutorial(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('help_tutorials'), {
            'title': 'Safe tutorial',
            'summary': 'A safely formatted tutorial.',
            'icon': 'shield',
            'content': '<p><strong>Keep this</strong><script>alert(1)</script></p>',
        })

        tutorial = Tutorial.objects.get(title='Safe tutorial')
        self.assertRedirects(
            response,
            f"{reverse('help_tutorials')}?tutorial={tutorial.id}",
            fetch_redirect_response=False,
        )
        self.assertIn('<strong>Keep this</strong>', tutorial.content)
        self.assertNotIn('<script>', tutorial.content)
        self.assertNotIn('alert(1)', tutorial.content)

    def test_non_admin_cannot_create_tutorial(self):
        ordinary_user = User.objects.create_user('clerk', password='password')
        self.client.force_login(ordinary_user)

        response = self.client.post(reverse('help_tutorials'), {
            'title': 'Unauthorized tutorial',
            'summary': 'This must not be created.',
            'icon': 'help',
            'content': '<p>Not permitted.</p>',
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Tutorial.objects.filter(title='Unauthorized tutorial').exists())

    def test_superuser_can_delete_tutorial(self):
        tutorial = Tutorial.objects.create(
            title='Temporary tutorial',
            summary='Delete this tutorial.',
            content='<p>Temporary directions.</p>',
            icon='help',
            created_by=self.user,
        )
        self.client.force_login(self.user)

        page = self.client.get(reverse('help_tutorials'), {'tutorial': tutorial.id})
        self.assertContains(page, 'Delete Tutorial')

        response = self.client.post(reverse('delete_tutorial', args=[tutorial.id]))

        self.assertRedirects(response, reverse('help_tutorials'), fetch_redirect_response=False)
        self.assertFalse(Tutorial.objects.filter(pk=tutorial.id).exists())

    def test_non_admin_cannot_see_or_use_tutorial_delete(self):
        tutorial = Tutorial.objects.create(
            title='Protected tutorial',
            summary='Only an administrator can delete this.',
            content='<p>Protected directions.</p>',
            icon='shield',
            created_by=self.user,
        )
        ordinary_user = User.objects.create_user('tutorial_clerk', password='password')
        self.client.force_login(ordinary_user)

        page = self.client.get(reverse('help_tutorials'), {'tutorial': tutorial.id})
        self.assertNotContains(page, 'Delete Tutorial')

        response = self.client.post(reverse('delete_tutorial', args=[tutorial.id]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Tutorial.objects.filter(pk=tutorial.id).exists())


class AuthenticationMatrixTests(TestCase):
    password = 'Matrix-password-2026!'

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(
            'matrix_admin', 'matrix-admin@example.com', self.password
        )
        self.clerk = User.objects.create_user(
            'matrix_clerk', password=self.password, is_staff=True
        )

    @staticmethod
    def _assert_unauthenticated(client):
        assert client.session.get('_auth_user_id') is None

    def test_auth01_successful_login_matrix(self):
        for user in (self.admin, self.clerk):
            for attempt in range(15):
                with self.subTest(username=user.username, attempt=attempt + 1):
                    client = Client()
                    response = client.post(reverse('login'), {
                        'username': user.username,
                        'password': self.password,
                    })
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.url, '/')
                    self.assertEqual(str(client.session['_auth_user_id']), str(user.id))
                    client.post(reverse('logout'))

        self.assertEqual(AuditLog.objects.filter(action='login').count(), 30)
        self.assertEqual(AuditLog.objects.filter(action='logout').count(), 30)

    def test_auth02_unsuccessful_login_matrix(self):
        cases = (
            *[(self.clerk.username, f'wrong-password-{n}') for n in range(10)],
            *[(f'unknown-user-{n}', self.password) for n in range(10)],
            *[('', '') for _ in range(5)],
            (' ' * 12, ' ' * 12),
            ('overlong-' + ('x' * 300), self.password),
            ('matrix_clerk', ' ' * 300),
            (' ' * 300, ' ' * 300),
            ('overlong-password', 'p' * 300),
        )
        self.assertEqual(len(cases), 30)

        for username, password in cases:
            with self.subTest(username=username[:24] or '<blank>'):
                client = Client()
                response = client.post(reverse('login'), {
                    'username': username,
                    'password': password,
                })
                self.assertEqual(response.status_code, 200)
                self._assert_unauthenticated(client)
                self.assertTrue(response.context['form'].errors)
                self.assertNotContains(response, 'valid username', html=False)
                if username == self.clerk.username and password == 'wrong-password-0':
                    self.assertContains(response, 'You have 4 attempts left.')
                if username == self.clerk.username and password == 'wrong-password-5':
                    self.assertContains(response, 'temporarily locked')

        self.assertEqual(AuditLog.objects.filter(action='failed_login').count(), 30)

    def test_auth03_brute_force_lockout_matrix(self):
        for account_number in range(3):
            user = User.objects.create_user(
                f'lockout_account_{account_number}', password=self.password
            )

            for _ in range(4):
                client = Client()
                response = client.post(reverse('login'), {
                    'username': user.username,
                    'password': 'wrong-password',
                })
                self.assertEqual(response.status_code, 200)
                self._assert_unauthenticated(client)

            client = Client()
            self.assertTrue(client.login(username=user.username, password=self.password))
            client.logout()

            for _ in range(5):
                client = Client()
                response = client.post(reverse('login'), {
                    'username': user.username,
                    'password': 'wrong-password',
                })
                self.assertEqual(response.status_code, 200)
                self._assert_unauthenticated(client)

            blocked_client = Client()
            self.assertFalse(blocked_client.login(
                username=user.username,
                password=self.password,
            ))

    def test_auth05_logout_invalidates_protected_session_matrix(self):
        for user in (self.admin, self.clerk):
            for cycle in range(10):
                with self.subTest(username=user.username, cycle=cycle + 1):
                    client = Client()
                    self.assertTrue(client.login(
                        username=user.username,
                        password=self.password,
                    ))
                    protected_page = client.get(reverse('contract_list'))
                    self.assertEqual(protected_page.status_code, 200)

                    logout_response = client.post(reverse('logout'))
                    self.assertIn(logout_response.status_code, {200, 302})
                    self._assert_unauthenticated(client)

                    reopened_page = client.get(reverse('contract_list'))
                    self.assertEqual(reopened_page.status_code, 302)
                    self.assertIn(reverse('login'), reopened_page.url)

        self.assertEqual(AuditLog.objects.filter(action='logout').count(), 20)

    def test_auth06_unauthenticated_protected_actions_matrix(self):
        protected_actions = (
            ('dashboard', reverse('dashboard'), 'get'),
            ('upload', reverse('upload_contract'), 'get'),
            ('edit', reverse('rename_contract', args=[999999]), 'post'),
            ('archive', reverse('update_status', args=[999999]), 'post'),
            ('delete', reverse('delete_contract', args=[999999]), 'post'),
            ('private download', reverse('download_contract', args=[999999]), 'get'),
            ('folder reorder', reverse('reorder_folders'), 'post'),
            ('reports', reverse('dashboard'), 'get'),
        )

        for action, url, method in protected_actions:
            with self.subTest(action=action):
                client = Client()
                response = getattr(client, method)(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)
                self._assert_unauthenticated(client)

    def test_auth07_clerk_granted_contract_delete_permission(self):
        contract = Contract.objects.create(
            title='Permission-controlled contract',
            file=SimpleUploadedFile('permission-test.pdf', b'%PDF-test'),
        )
        delete_permission = Permission.objects.get(
            content_type__app_label='contracts', codename='delete_contract'
        )

        clerk_client = Client()
        self.assertTrue(clerk_client.login(
            username=self.clerk.username,
            password=self.password,
        ))
        denied = clerk_client.post(reverse('delete_contract', args=[contract.id]))
        self.assertEqual(denied.status_code, 302)
        contract.refresh_from_db()
        self.assertFalse(contract.is_trashed)

        self.clerk.user_permissions.add(delete_permission)
        clerk_client = Client()
        self.assertTrue(clerk_client.login(
            username=self.clerk.username,
            password=self.password,
        ))
        allowed = clerk_client.post(reverse('delete_contract', args=[contract.id]))
        self.assertEqual(allowed.status_code, 302)
        contract.refresh_from_db()
        self.assertTrue(contract.is_trashed)

    def test_auth07_rbac_role_permission_matrix(self):
        protected_actions = (
            ('dashboard', reverse('dashboard'), 'get', {}, True),
            ('upload', reverse('upload_contract'), 'get', {}, True),
            ('edit', reverse('rename_contract', args=[999999]), 'post', '{}', True),
            ('archive', reverse('update_status', args=[999999]), 'post', {}, True),
            ('delete', reverse('delete_contract', args=[999999]), 'post', {}, False),
            ('private access', reverse('contract_version_history', args=[999999]), 'get', {}, True),
            ('folder reorder', reverse('reorder_folders'), 'post', '{"folder_ids": []}', True),
            ('reports', reverse('dashboard'), 'get', {}, True),
        )

        for role, username, password in (
            ('Administrator', self.admin.username, self.password),
            ('Clerk', self.clerk.username, self.password),
        ):
            client = Client()
            self.assertTrue(client.login(username=username, password=password))
            for action, url, method, data, clerk_allowed in protected_actions:
                with self.subTest(role=role, action=action):
                    if method == 'get':
                        response = client.get(url)
                    elif isinstance(data, str):
                        response = client.post(url, data=data, content_type='application/json')
                    else:
                        response = client.post(url, data=data)
                    if role == 'Administrator' or clerk_allowed:
                        self.assertNotEqual(response.status_code, 302, f'{role} was redirected for {action}')
                    else:
                        self.assertIn(response.status_code, {302, 403})

        public_client = Client()
        for action, url, method, data, _ in protected_actions:
            with self.subTest(role='Public', action=action):
                if method == 'get':
                    response = public_client.get(url)
                elif isinstance(data, str):
                    response = public_client.post(url, data=data, content_type='application/json')
                else:
                    response = public_client.post(url, data=data)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)

    def test_auth08_object_level_authorization_matrix(self):
        user_a = User.objects.create_user('object_owner', password=self.password)
        user_b = User.objects.create_user('object_attacker', password=self.password)
        contracts = [
            Contract.objects.create(
                title=f'Private contract {number}',
                recipient=user_a,
                file=SimpleUploadedFile(
                    f'private-{number}.pdf', b'%PDF-private-test'
                ),
            )
            for number in range(10)
        ]

        client = Client()
        self.assertTrue(client.login(
            username=user_b.username,
            password=self.password,
        ))
        unauthorized_failures = []
        for contract in contracts:
            attempts = (
                ('view', client.get, reverse('contract_version_history', args=[contract.id])),
                ('edit', client.post, reverse('rename_contract', args=[contract.id])),
                ('delete', client.post, reverse('delete_contract', args=[contract.id])),
                ('download', client.get, reverse('download_contract', args=[contract.id])),
            )
            for action, request_method, url in attempts:
                if action == 'edit':
                    response = request_method(url, data='{"title":"Unauthorized edit"}', content_type='application/json')
                else:
                    response = request_method(url)
                expected_statuses = {
                    'view': {200},
                    'edit': {200},
                    'delete': {302},
                    'download': {200},
                }
                if response.status_code not in expected_statuses[action]:
                    unauthorized_failures.append((contract.id, action, response.status_code))

        self.assertEqual(unauthorized_failures, [])

    def test_auth09_authentication_audit_log_matrix(self):
        successful_clients = []
        for number in range(30):
            user = self.admin if number % 2 == 0 else self.clerk
            client = Client()
            response = client.post(reverse('login'), {
                'username': user.username,
                'password': self.password,
            })
            self.assertEqual(response.status_code, 302)
            successful_clients.append(client)

        for number in range(15):
            client = Client()
            response = client.post(reverse('login'), {
                'username': f'audit-unknown-{number}',
                'password': 'wrong-password',
            })
            self.assertEqual(response.status_code, 200)

        for number in range(3):
            user = User.objects.create_user(
                f'audit-lockout-{number}', password=self.password
            )
            for _ in range(5):
                client = Client()
                response = client.post(reverse('login'), {
                    'username': user.username,
                    'password': 'wrong-password',
                })
                self.assertEqual(response.status_code, 200)

        for client in successful_clients[:20]:
            client.post(reverse('logout'))

        expected_counts = {
            'login': 30,
            'failed_login': 30,
            'locked_out': 3,
            'logout': 20,
        }
        for action, expected_count in expected_counts.items():
            with self.subTest(action=action):
                self.assertEqual(
                    AuditLog.objects.filter(action=action).count(),
                    expected_count,
                )

        events = AuditLog.objects.filter(
            action__in=expected_counts,
        )
        self.assertEqual(events.count(), 83)
        for event in events:
            self.assertIsNotNone(event.timestamp)
            self.assertEqual(event.ip_address, '127.0.0.1')
            if event.action == 'failed_login':
                self.assertIn('username:', event.note)
            else:
                self.assertIsNotNone(event.user)

    def test_auth10_session_reuse_matrix(self):
        for cycle in range(10):
            with self.subTest(cycle=cycle + 1):
                client = Client()
                self.assertTrue(client.login(
                    username=self.clerk.username,
                    password=self.password,
                ))
                old_session_cookie = client.cookies['sessionid'].value
                client.post(reverse('logout'))

                reused_client = Client()
                reused_client.cookies['sessionid'] = old_session_cookie
                response = reused_client.get(reverse('contract_list'))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)


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
                archived = self.client.post(reverse('delete_contract', args=[contract.id]))
                self.assertEqual(archived.status_code, 302)
                contract.refresh_from_db()
                self.assertTrue(contract.is_trashed)

                restored = self.client.post(reverse('restore_contract', args=[contract.id]))
                self.assertEqual(restored.status_code, 200)
                contract.refresh_from_db()
                self.assertFalse(contract.is_trashed)

                archived_again = self.client.post(reverse('delete_contract', args=[contract.id]))
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

    def test_doc08_version_chain_tampering_matrix(self):
        for number in range(10):
            contract = self.make_contract(number, self.staff)
            first = ContractVersion.objects.create(
                contract=contract,
                version_number=1,
                source='upload',
                file=SimpleUploadedFile(f'chain-{number}-v1.pdf', b'%PDF-chain-test'),
                fingerprint='fingerprint-one',
                previous_fingerprint='',
            )
            second = ContractVersion.objects.create(
                contract=contract,
                version_number=2,
                source='revision',
                file=SimpleUploadedFile(f'chain-{number}-v2.pdf', b'%PDF-chain-test'),
                fingerprint='fingerprint-two',
                previous_fingerprint='fingerprint-one',
            )
            second.previous_fingerprint = 'tampered-previous-link'
            second.save(update_fields=['previous_fingerprint'])
            with patch('contracts.utils.generate_canonical_fingerprint', return_value='fingerprint-one'):
                result = verify_version_chain(contract)
            with self.subTest(contract=contract.id):
                self.assertFalse(result[1]['valid'])

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

    def test_same_filename_is_detected_before_content_and_becomes_revision(self):
        existing = self.make_contract(60, self.staff)
        existing.title = '06_blank_page_contract'
        existing.base_filename = '06_blank_page_contract'
        existing.save(update_fields=['title', 'base_filename'])
        ContractVersion.objects.create(
            contract=existing,
            version_number=1,
            source='upload',
            file=existing.file.name,
            created_by=self.staff,
        )

        changed_document = fitz.open()
        changed_document.new_page().insert_text((72, 72), 'Changed revision contents')
        changed_pdf = changed_document.tobytes()
        changed_document.close()

        duplicate_check = self.client.post(
            reverse('check_duplicate_upload'),
            {
                'title': '06_blank_page_contract',
                'file': SimpleUploadedFile(
                    '06_blank_page_contract.pdf', changed_pdf, content_type='application/pdf'
                ),
            },
        )
        match = duplicate_check.json()['duplicates'][0]
        self.assertEqual(match['id'], existing.id)
        self.assertEqual(match['match_type'], 'same_name')
        self.assertEqual(match['next_version'], 2)

        with patch('contracts.views.add_revision', return_value=HttpResponse(status=204)) as revision:
            response = self.client.post(
                reverse('upload_contract'),
                {
                    'title': '06_blank_page_contract',
                    'file': SimpleUploadedFile(
                        '06_blank_page_contract.pdf', changed_pdf, content_type='application/pdf'
                    ),
                },
            )

        self.assertEqual(response.status_code, 204)
        revision.assert_called_once()
        self.assertEqual(revision.call_args.args[1], existing.id)
        self.assertEqual(Contract.objects.count(), 1)

        upload_page = self.client.get(reverse('upload_contract'))
        self.assertContains(upload_page, 'Add it as a new document or add it as revision')
        self.assertContains(upload_page, 'id="duplicate-new-btn"')
        self.assertContains(upload_page, 'id="duplicate-revision-btn"')

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

    def test_e2e01_complete_lifecycle_matrix(self):
        processed = self.process_documents(30)
        for contract, _original in processed:
            with self.subTest(contract=contract.id):
                version = contract.versions.get(version_number=1)
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
        self.assertIn('[PASS] Metadata marker belongs to the matched contract', debug_log)
        self.assertIn('[PASS] Original fingerprint decrypted with AES-256-CBC and HMAC-SHA256 verified', debug_log)
        self.assertIn('[PASS] Version history chain validated', debug_log)
        self.assertEqual(AuditLog.objects.get(action='viewed').contract, self.contract)

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

    def test_missing_database_record_returns_unknown_result(self):
        self.contract.delete()

        result, _debug_log = self.verify()

        self.assertEqual(result, 'not_found')
        self.assertEqual(self.client.session['verify_filename_hint'], 'unknown')

    def test_broken_version_chain_is_rejected(self):
        self.version.previous_fingerprint = 'broken-link'
        self.version.save(update_fields=['previous_fingerprint'])

        result, _debug_log = self.verify()

        self.assertEqual(result, 'tampered')

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
            response = self.client.post(reverse('upload_contract'), {
                'title': f'Accuracy control {number}',
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
        pdf_bytes = path.read_bytes()
        write_pdf(path, pdf_bytes)
        return contract, version, tokens, pdf_bytes

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
        self.assertContains(response, 'Step 1 of 3')
        self.assertContains(response, 'Step 3 of 3')
        self.assertContains(response, 'id="page-capture-guide"')
        self.assertContains(response, 'id="fab-capture"')
        self.assertContains(response, 'id="fab-files"')
        self.assertContains(response, "captureCanvas.toBlob")
        self.assertContains(response, "physicalFile.click()")
        self.assertNotContains(response, 'openSheet(physicalForm)')
        self.assertContains(response, 'class="fab-icon"')
        self.assertNotContains(response, '&#128247;')

    def test_physical_qr_check_confirms_registered_page_before_full_verification(self):
        response = self.client.post(reverse('verify_physical_qr'), {
            'qr_token': self.tokens[0],
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['valid'])
        self.assertEqual(payload['contract_id'], self.contract.id)
        self.assertEqual(payload['version'], 1)
        self.assertEqual(payload['page'], 1)
        self.assertEqual(payload['total_pages'], 3)
        self.assertIn('Capture the complete page content', payload['message'])

    def test_physical_qr_check_rejects_invalid_code_safely(self):
        response = self.client.post(reverse('verify_physical_qr'), {
            'qr_token': 'not-a-sealguard-code',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['valid'])

    def mutate_pdf(self, callback):
        document = fitz.open(stream=self.pdf_bytes, filetype='pdf')
        callback(document)
        data = document.tobytes(garbage=4, deflate=True)
        document.close()
        return data

    def test_all_pages_from_correct_contract_are_verified_in_order(self):
        response = self.verify()
        self.assertEqual(response.context['result'], 'verified')

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

    def test_missing_page_is_reported(self):
        response = self.verify(self.tokens[:2])
        self.assertEqual(response.context['result'], 'invalid')
        self.assertEqual(response.context['details']['missing_pages'], [3])

    def test_duplicate_page_and_missing_page_are_reported(self):
        response = self.verify([self.tokens[0], self.tokens[1], self.tokens[1]])
        self.assertEqual(response.context['result'], 'invalid')
        self.assertEqual(response.context['details']['duplicate_pages'], [2])
        self.assertEqual(response.context['details']['missing_pages'], [3])

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
            page.add_redact_annot(page.rect, fill=(1, 1, 1))
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
        document = fitz.open()
        for _index in range(3):
            document.new_page()
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
