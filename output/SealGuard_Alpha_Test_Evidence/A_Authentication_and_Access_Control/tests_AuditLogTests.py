# Copy of contracts/tests.py, class AuditLogTests (lines 51-658) plus the file's imports.
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

    def test_mobile_version_metadata_requires_login_and_omits_scan_results(self):
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
        self.assertNotIn('valid', data['versions'][0])
        self.assertNotIn('integrity_checked_at', data['versions'][0])
        self.client.get(url)

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

    def test_filename_cannot_authenticate_an_invalid_pdf(self):
        upload = SimpleUploadedFile(
            'authentic_demo.pdf', b'%PDF-demo', content_type='application/pdf'
        )

        response = self.client.post(reverse('public_verify'), {'pdf_file': upload})

        self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
        self.assertEqual(self.client.session['verify_result'], 'error')
        debug_log = self.client.session['verify_debug_log']
        self.assertNotIn(
            'Demo simulation enabled by filename; cryptographic checks were not executed',
            debug_log,
        )

    def test_tampered_verification_retains_pdf_and_inspection_details(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            with fitz.open() as document:
                document.new_page().insert_text((72, 72), 'Unlinked authentication evidence')
                document.set_metadata({'keywords': 'SEALGUARD:unlinked-marker'})
                pdf_bytes = document.tobytes()
            upload = SimpleUploadedFile(
                'tampered_contract.pdf', pdf_bytes, content_type='application/pdf'
            )

            response = self.client.post(reverse('public_verify'), {'pdf_file': upload})

            self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
            audit_log = AuditLog.objects.get(action='reported_tampering')
            self.assertEqual(audit_log.document_title, 'tampered_contract.pdf')
            self.assertEqual(audit_log.verification_source, 'Official Barangay Database')
            self.assertEqual(audit_log.verification_result, 'Possible Modification')
            self.assertEqual(audit_log.integrity_check, 'Failed')
            self.assertIn('full cryptographic pipeline', audit_log.verification_debug_log)
            self.assertIn('no active official record', audit_log.verification_debug_log)
            self.assertTrue(audit_log.evidence_file)
            self.assertTrue(audit_log.evidence_file.storage.exists(audit_log.evidence_file.name))

            self.client.force_login(self.user)
            dashboard = self.client.get(reverse('dashboard'))
            self.assertContains(dashboard, 'Verification Logs')
            self.assertNotContains(dashboard, 'full cryptographic pipeline')

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
            self.assertIn('full cryptographic pipeline', detail.json()['debug_log'])

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
        self.assertContains(response, 'title="Procurement Documents" aria-label="Procurement Documents"')
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


