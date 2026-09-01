from datetime import timedelta
from unittest.mock import patch

import fitz
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AuditLog, Contract, ContractVersion, Folder, Tutorial
from .utils import log_activity, verify_version_chain


class AuditLogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin', 'admin@example.com', 'password')
        self.contract = Contract.objects.create(title='Senior Assistance Form', file='contracts/test.pdf')

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
        titles = list(AuditLog.objects.values_list('document_title', flat=True))
        self.assertEqual(titles, ['Senior Assistance Form', 'Senior Assistance Form'])

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
        self.assertContains(response, 'onchange="this.form.requestSubmit()"', count=3)
        self.assertContains(response, 'page=3')

    def test_document_search_is_debounced_in_the_browser(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('contract_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'oninput="scheduleSearchFilter()"')
        self.assertContains(response, 'setTimeout(() =>')
        self.assertContains(response, '}, 350);')

    @patch('contracts.views.generate_canonical_fingerprint', return_value='f' * 64)
    @patch('contracts.views.extract_cf_from_metadata', return_value='encrypted-marker')
    @patch('contracts.views.has_barangay_footer', return_value=True)
    def test_public_verification_uses_direct_fingerprint_lookup(
        self, _has_footer, _extract_marker, _fingerprint
    ):
        self.contract.fingerprint = 'f' * 64
        self.contract.save(update_fields=['fingerprint'])
        pdf = fitz.open()
        pdf.new_page().insert_text((72, 72), 'Verification test document')
        pdf_bytes = pdf.tobytes()
        pdf.close()
        upload = SimpleUploadedFile('document.pdf', pdf_bytes, content_type='application/pdf')

        response = self.client.post(reverse('public_verify'), {'pdf_file': upload})

        self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
        viewed = AuditLog.objects.get(action='viewed')
        self.assertEqual(viewed.contract, self.contract)

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
        self.assertContains(response, 'title="Logout" aria-label="Logout"')

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
