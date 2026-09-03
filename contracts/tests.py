from datetime import timedelta
import base64
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import fitz
from Crypto.PublicKey import RSA
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
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
from .forms import sanitize_tutorial_html
from .utils import (
    encrypt_cf,
    embed_data_in_image,
    generate_canonical_fingerprint,
    log_activity,
    stamp_seal_on_pdf,
    verify_version_chain,
)


class AuditLogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin', 'admin@example.com', 'password')
        self.contract = Contract.objects.create(title='Senior Assistance Form', file='contracts/test.pdf')

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
        self.assertContains(response, 'onchange="this.form.requestSubmit()"', count=2)
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
        self.assertContains(response, 'id="contracts-doc-panel"')
        self.assertContains(response, 'id="mobile-contract-action-sheet"')
        self.assertContains(response, 'function handleMobileContractTap(event, contractId)')
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
            self.assertTrue(audit_log.evidence_file)
            self.assertTrue(audit_log.evidence_file.storage.exists(audit_log.evidence_file.name))

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
        default_seal_path = media_root / 'default-seal.png'
        seal_path = media_root / 'seal.png'
        sealed_path = media_root / 'sealed.pdf'

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
        self.encrypted_marker = encrypted

        stored_path = media_root / 'contracts' / 'enrolled.pdf'
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_bytes(self.sealed_bytes)
        self.contract = Contract.objects.create(
            title='Cryptographic verification fixture',
            file='contracts/enrolled.pdf',
            fingerprint=self.sealed_fingerprint,
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

        self.assertEqual(result, 'authentic')
        self.assertIn('[PASS] Metadata marker belongs to the matched contract', debug_log)
        self.assertIn('[PASS] Original fingerprint decrypted with AES-256-CBC and HMAC-SHA256 verified', debug_log)
        self.assertIn('[PASS] Version history chain validated', debug_log)
        self.assertEqual(AuditLog.objects.get(action='viewed').contract, self.contract)

    def test_modified_text_is_rejected(self):
        modified = self.mutate_pdf(
            lambda document: document[0].insert_text((72, 110), 'ALTERED AMOUNT: PHP 900,000.00')
        )

        result, _debug_log = self.verify(modified)

        self.assertEqual(result, 'tampered')

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
        self.user = User.objects.create_user('physical_staff', password='password')
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
            title=title, file=f'contracts/{filename}', fingerprint=fingerprint,
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
        return contract, version, tokens, path.read_bytes()

    def verify(self, tokens=None, pdf_bytes=None):
        upload = SimpleUploadedFile(
            'physical.pdf', pdf_bytes or self.pdf_bytes, content_type='application/pdf'
        )
        return self.client.post(reverse('verify_physical'), {
            'physical_file': upload,
            'page_tokens': json.dumps(tokens or self.tokens),
        })

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
