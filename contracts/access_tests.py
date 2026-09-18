from .pdf_storage import write_pdf
import importlib
import tempfile
from pathlib import Path

from django.apps import apps
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.db import connection

from .models import Contract, ContractVersion, AuditLog, Folder


class DocumentAccessTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.owner = User.objects.create_user('uploader', is_staff=True)
        self.editor = User.objects.create_user('collaborator', is_staff=True)
        self.other = User.objects.create_user('unrelated')
        self.admin = User.objects.create_superuser('administrator')
        self.contract = Contract.objects.create(title='Private document', uploaded_by=self.owner, recipient=self.owner)
        self.contract.file.save('private.pdf', ContentFile(b'%PDF-1.4 private content'))
        self.version = ContractVersion.objects.create(contract=self.contract, version_number=1, created_by=self.owner)
        self.version.file.save('previous.pdf', ContentFile(b'%PDF-1.4 previous content'))
        self.contract.collaborators.add(self.editor)

    def login(self, user):
        self.client.force_login(user)

    def test_file_access_matrix_including_direct_media(self):
        urls = [reverse(name, args=[pk]) for name, pk in [
            ('preview_contract', self.contract.pk), ('download_contract', self.contract.pk),
            ('preview_contract_version', self.version.pk), ('download_contract_version', self.version.pk),
        ]] + [self.contract.file.url, self.version.file.url]
        for user in [None, self.other, self.owner, self.editor, self.admin]:
            self.client.logout()
            if user:
                self.login(user)
            for url in urls:
                with self.subTest(user=user, url=url):
                    response = self.client.get(url)
                    if user in [self.owner, self.editor, self.admin]:
                        self.assertEqual(response.status_code, 200)
                        self.assertIn('no-store', response['Cache-Control'])
                    else:
                        self.assertIn(response.status_code, [302, 404])
                    response.close()

    def test_public_read_does_not_grant_edit(self):
        self.contract.is_public = True
        self.contract.save()
        for url in [self.contract.file.url, reverse('preview_contract', args=[self.contract.pk])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()
        self.login(self.other)
        self.assertEqual(self.client.post(reverse('rename_contract', args=[self.contract.pk]),
                                         data={'title': 'stolen'}, content_type='application/json').status_code, 404)

    def test_unrelated_users_cannot_mutate_or_read_history(self):
        self.login(self.other)
        names = ['rename_contract', 'tag_contract', 'update_status', 'publish_contract',
                 'assign_folder', 'delete_contract', 'encrypt_contract', 'add_revision',
                 'upload_signed_scan', 'restore_contract', 'permanently_delete_contract',
                 'mark_contract_viewed', 'contract_version_history', 'contract_access']
        for name in names:
            with self.subTest(name=name):
                self.assertIn(self.client.post(reverse(name, args=[self.contract.pk])).status_code, {403, 404})

    def test_collaborator_can_edit_but_cannot_delegate_or_publish(self):
        self.login(self.editor)
        response = self.client.post(reverse('rename_contract', args=[self.contract.pk]),
                                    {'title': 'Edited'}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        for name in ['contract_access', 'publish_contract']:
            self.assertEqual(self.client.post(reverse(name, args=[self.contract.pk])).status_code, 404)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.uploaded_by, self.owner)

    def test_grant_revoke_and_audit(self):
        self.login(self.owner)
        url = reverse('contract_access', args=[self.contract.pk])
        self.assertEqual(self.client.post(url, {'action': 'grant', 'username': self.other.username}).status_code, 200)
        self.login(self.other)
        response = self.client.get(reverse('preview_contract', args=[self.contract.pk]))
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get(self.contract.file.url).status_code, 404)
        self.login(self.owner)
        self.assertEqual(self.client.post(url, {'action': 'revoke', 'username': self.other.username}).status_code, 200)
        self.login(self.other)
        self.assertEqual(self.client.get(self.contract.file.url).status_code, 404)
        self.assertEqual(AuditLog.objects.filter(contract=self.contract, note__startswith='Document access').count(), 2)

    def test_sharing_validates_users_and_keeps_owner(self):
        self.login(self.owner)
        url = reverse('contract_access', args=[self.contract.pk])
        for username in ['missing', self.owner.username, self.admin.username]:
            self.assertEqual(self.client.post(url, {'action': 'grant', 'username': username}).status_code, 400)
        self.assertEqual(self.client.get(url).json()['owner'], self.owner.username)

    def test_sharing_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(reverse('contract_access', args=[self.contract.pk]),
                                     {'action': 'grant', 'username': self.other.username}).status_code, 403)

    def test_bulk_actions_reject_mixed_unauthorized_selection(self):
        own = Contract.objects.create(title='Own', uploaded_by=self.other)
        self.login(self.other)
        for name in ['bulk_delete_contracts', 'bulk_assign_folder', 'bulk_update_status',
                     'bulk_encrypt_contracts', 'bulk_download_contracts', 'bulk_permanently_delete_contracts']:
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name), {'ids': [own.pk, self.contract.pk]}).status_code, 404)
        self.contract.refresh_from_db()
        self.assertFalse(self.contract.is_trashed)

    def test_list_dashboard_and_export_hide_unrelated_documents(self):
        AuditLog.objects.create(contract=self.contract, user=self.owner, action='added', note='Secret audit text')
        self.login(self.other)
        for name in ['contract_list', 'dashboard', 'export_dashboard_report']:
            response = self.client.get(reverse(name))
            if name == 'export_dashboard_report':
                self.assertEqual(response.status_code, 403)
            else:
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'Private document')
                self.assertNotContains(response, 'Secret audit text')
        self.login(self.owner)
        response = self.client.get(reverse('contract_list'))
        self.assertContains(response, 'Manage access')
        self.assertContains(response, 'data-can-share="1"')

    def test_media_denies_orphans_temporary_files_and_traversal(self):
        root = Path(self.directory.name)
        (root / 'temp').mkdir()
        (root / 'temp' / 'secret.pdf').write_bytes(b'secret')
        self.login(self.admin)
        for path in ['/media/temp/secret.pdf', '/media/../db.sqlite3', '/media/%2e%2e/db.sqlite3']:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_trashed_files_are_not_served_even_if_public(self):
        self.contract.is_public = True
        self.contract.is_trashed = True
        self.contract.save()
        self.login(self.owner)
        self.assertEqual(self.client.get(self.contract.file.url).status_code, 404)

    def test_evidence_and_seals_obey_document_access(self):
        self.contract.seal_image.save('seal.png', ContentFile(b'image'))
        log = AuditLog.objects.create(contract=self.contract, user=self.owner, action='reported_tampering')
        log.evidence_file.save('evidence.pdf', ContentFile(b'%PDF-1.4 evidence'))
        for user, expected in [(self.other, 404), (self.owner, 200)]:
            self.login(user)
            for url in [self.contract.seal_image.url, log.evidence_file.url, reverse('audit_evidence_preview', args=[log.pk])]:
                response = self.client.get(url)
                self.assertEqual(response.status_code, expected)
                response.close()

    def test_public_branding_and_private_trash_thumbnail(self):
        root = Path(self.directory.name) / 'seals'
        root.mkdir()
        (root / 'default_seal.png').write_bytes(b'public branding')
        response = self.client.get('/media/seals/default_seal.png')
        self.assertEqual(response.status_code, 200)
        response.close()
        self.contract.seal_image.save('private.png', ContentFile(b'private thumbnail'))
        self.contract.is_trashed = True
        self.contract.save()
        self.assertEqual(self.client.get(self.contract.seal_image.url).status_code, 404)
        self.login(self.owner)
        response = self.client.get(self.contract.seal_image.url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get(self.contract.file.url).status_code, 404)

    def test_folder_delete_cannot_bypass_access(self):
        folder = Folder.objects.create(name='Mixed', owner=self.other)
        self.contract.folder = folder
        self.contract.save()
        self.login(self.other)
        for mode in ['unassign', 'delete_items']:
            self.assertIn(self.client.post(reverse('delete_folder', args=[folder.pk]),
                             {'mode': mode}, content_type='application/json').status_code, {403, 404})

    def test_owner_backfill_uses_initial_uploader_then_recipient(self):
        self.contract.uploaded_by = None
        self.contract.recipient = self.other
        self.contract.save()
        fallback = Contract.objects.create(title='Legacy', recipient=self.editor)
        module = importlib.import_module('contracts.migrations.0038_backfill_uploaders')
        from types import SimpleNamespace
        module.backfill(apps, SimpleNamespace(connection=connection))
        self.contract.refresh_from_db()
        fallback.refresh_from_db()
        self.assertEqual(self.contract.uploaded_by, self.owner)
        self.assertEqual(fallback.uploaded_by, self.editor)

    def test_upload_records_uploader_and_ignores_forged_owner(self):
        import fitz
        from django.core.files.uploadedfile import SimpleUploadedFile
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'New private upload')
            contents = pdf.tobytes()
        self.login(self.owner)
        response = self.client.post(reverse('upload_contract'), {
            'title': 'New upload', 'skip_encryption': '1',
            'uploaded_by': self.other.pk,
            'file': SimpleUploadedFile('new.pdf', contents, content_type='application/pdf'),
        })
        self.assertEqual(response.status_code, 302)
        uploaded = Contract.objects.get(title='New upload')
        self.assertEqual(uploaded.uploaded_by, self.owner)
        self.assertFalse(uploaded.is_public)

    def test_collaborator_can_trash_and_restore_owned_document(self):
        self.login(self.editor)
        self.assertEqual(self.client.post(reverse('delete_contract', args=[self.contract.pk])).status_code, 302)
        self.contract.refresh_from_db()
        self.assertTrue(self.contract.is_trashed)
        self.assertEqual(self.client.post(reverse('restore_contract', args=[self.contract.pk])).status_code, 200)
        self.contract.refresh_from_db()
        self.assertFalse(self.contract.is_trashed)

    def test_admin_can_grant_access_to_another_uploaders_document(self):
        self.login(self.admin)
        self.assertEqual(self.client.post(reverse('contract_access', args=[self.contract.pk]),
                         {'action': 'grant', 'username': self.other.username}).status_code, 200)
        self.assertTrue(self.contract.collaborators.filter(pk=self.other.pk).exists())

    def test_verification_temporary_preview_is_session_bound(self):
        token = 'a' * 32
        root = Path(self.directory.name) / 'verification_previews'
        root.mkdir()
        write_pdf(root / f'{token}.pdf', b'%PDF-1.4 uploaded preview')
        urls = [f'/media/verification_previews/{token}.pdf', reverse('verification_preview', args=[token])]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404)
        session = self.client.session
        session['verify_preview_token'] = token
        session.save()
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()
