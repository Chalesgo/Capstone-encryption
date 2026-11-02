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

    def test_admin_pdf_download_option_for_current_and_historical_versions(self):
        from .encrypted_documents import PACKAGE_MAGIC
        routes = [('preview_contract', 'download_contract', self.contract.pk, b'%PDF-1.4 private content'),
                  ('preview_contract_version', 'download_contract_version', self.version.pk, b'%PDF-1.4 previous content')]
        for user in (None, self.owner, self.editor, self.other, self.admin):
            self.client.logout()
            if user:
                self.login(user)
            for preview, download, pk, expected in routes:
                with self.subTest(user=user, route=download):
                    url = reverse(download, args=[pk]) + '?format=pdf'
                    response = self.client.get(url)
                    if user == self.admin:
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response['Content-Type'], 'application/pdf')
                        self.assertEqual(b''.join(response.streaming_content), expected)
                        self.assertIn('attachment', response['Content-Disposition'])
                        default = self.client.get(reverse(download, args=[pk]))
                        self.assertTrue(b''.join(default.streaming_content).startswith(PACKAGE_MAGIC))
                    else:
                        self.assertEqual(response.status_code, 404)
                    metadata = self.client.get(reverse(preview, args=[pk]))
                    if user == self.admin:
                        self.assertEqual(metadata['X-SealGuard-PDF-Download'], url)
                    else:
                        self.assertNotIn('X-SealGuard-PDF-Download', metadata)
                    metadata.close()

    def test_list_uses_shared_viewer_with_admin_download_metadata(self):
        self.login(self.admin)
        response = self.client.get(reverse('contract_list'))
        self.assertContains(response, 'id="pdf-frame"')
        self.assertContains(response, 'mobile-pdf.html')
        self.client.logout()
        response = self.client.get(reverse('contract_list'))
        self.assertNotEqual(response.status_code, 200)

    def test_public_viewer_metadata_is_available_without_login(self):
        self.contract.is_public = True
        self.contract.save()
        response = self.client.get(reverse('contract_version_history', args=[self.contract.pk]), {'metadata_only': '1'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['created'])
        self.assertTrue(data['encrypted'])
        self.assertEqual(data['title'], self.contract.title)
        self.assertEqual(data['versions'][0]['created_by'], self.owner.username)
        self.assertEqual(data['versions'][0]['version_number'], 1)
        self.assertIn('no-store', response['Cache-Control'])

    def test_anonymous_viewer_cannot_read_private_or_trashed_metadata(self):
        url = reverse('contract_version_history', args=[self.contract.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.contract.is_public = True
        self.contract.is_trashed = True
        self.contract.save()
        self.assertEqual(self.client.get(url).status_code, 404)

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

    def test_published_download_needs_sign_in_and_is_a_regular_pdf_for_residents(self):
        from django.contrib.auth.models import Group
        from .access import RESIDENT_GROUP
        self.contract.status = 'final'
        self.contract.is_public = True
        self.contract.save()
        url = reverse('download_contract', args=[self.contract.pk])
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={reverse('public_verify')}",
                             fetch_redirect_response=False)
        resident = User.objects.create_user('resident@example.com', 'resident@example.com')
        resident.groups.add(Group.objects.get_or_create(name=RESIDENT_GROUP)[0])
        self.client.force_login(resident)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertNotIn('.sgpdf', response['Content-Disposition'])
        self.assertIn(b'%PDF', b''.join(response.streaming_content))

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
        self.assertEqual(self.client.post(reverse('delete_contract', args=[self.contract.pk]), {'reason': 'duplicate'}).status_code, 302)
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


class ContractListFilterTests(TestCase):
    setUp = DocumentAccessTests.setUp
    login = DocumentAccessTests.login

    def test_sort_by_date_created_both_directions(self):
        newer = Contract.objects.create(title='Newer document', uploaded_by=self.owner)
        newer.file.save('newer.pdf', ContentFile(b'%PDF-1.4 newer'))
        Contract.objects.filter(pk=self.contract.pk).update(uploaded_at=newer.uploaded_at.replace(year=2020))
        self.login(self.admin)
        newest = [c.pk for c in self.client.get(reverse('contract_list'), {'sort': 'newest'}).context['contracts']]
        oldest = [c.pk for c in self.client.get(reverse('contract_list'), {'sort': 'oldest'}).context['contracts']]
        self.assertEqual(newest, [newer.pk, self.contract.pk])
        self.assertEqual(oldest, [self.contract.pk, newer.pk])

    def test_uploader_filter_and_pagination_keep_filters(self):
        other_doc = Contract.objects.create(title='Editor upload', uploaded_by=self.editor)
        other_doc.file.save('editor.pdf', ContentFile(b'%PDF-1.4 editor'))
        self.login(self.admin)
        response = self.client.get(reverse('contract_list'), {'uploader': self.editor.pk, 'sort': 'oldest', 'per_page': 10})
        self.assertEqual([c.pk for c in response.context['contracts']], [other_doc.pk])
        self.assertIn(f'uploader={self.editor.pk}', response.context['filter_query'])
        self.assertIn('sort=oldest', response.context['filter_query'])
        self.assertContains(response, 'id="uploader-filter"')

    def test_scope_menu_filters_my_uploads_and_shared_with_me(self):
        own = Contract.objects.create(title='Editor own upload', uploaded_by=self.editor)
        own.file.save('own.pdf', ContentFile(b'%PDF-1.4 own'))
        self.login(self.editor)
        mine = self.client.get(reverse('contract_list'), {'scope': 'mine'})
        shared = self.client.get(reverse('contract_list'), {'scope': 'shared'})
        self.assertEqual([c.pk for c in mine.context['contracts']], [own.pk])
        self.assertEqual([c.pk for c in shared.context['contracts']], [self.contract.pk])
        self.assertContains(shared, 'Shared with me')
        self.assertIn('scope=shared', shared.context['filter_query'])

    def test_sort_by_name_a_to_z(self):
        for title in ('beta document', 'Alpha document'):
            doc = Contract.objects.create(title=title, uploaded_by=self.owner)
            doc.file.save('doc.pdf', ContentFile(b'%PDF-1.4 doc'))
        self.login(self.admin)
        response = self.client.get(reverse('contract_list'), {'sort': 'name'})
        self.assertEqual([c.title for c in response.context['contracts']],
                         ['Alpha document', 'beta document', 'Private document'])
        self.assertContains(response, 'Name A–Z')

    def test_status_and_modified_sorts(self):
        final = Contract.objects.create(title='Final document', uploaded_by=self.owner, status='final')
        final.file.save('final.pdf', ContentFile(b'%PDF-1.4 final'))
        self.login(self.admin)
        def order(sort):
            return [c.pk for c in self.client.get(reverse('contract_list'), {'sort': sort}).context['contracts']]
        self.assertEqual(order('status_draft'), [self.contract.pk, final.pk])
        self.assertEqual(order('status_final'), [final.pk, self.contract.pk])
        self.assertEqual(order('modified_new'), [final.pk, self.contract.pk])
        self.assertEqual(order('modified_old'), [self.contract.pk, final.pk])
        self.assertContains(self.client.get(reverse('contract_list'), {'sort': 'status_final'}), 'Final first')

    def test_version_history_reports_document_owner_as_fallback(self):
        self.login(self.admin)
        history = self.client.get(reverse('contract_version_history', args=[self.contract.pk])).json()
        self.assertEqual(history['uploaded_by'], 'uploader')
        self.assertFalse(history['uploaded_by_deleted'])

    def test_deleted_uploaders_are_listed_and_filterable(self):
        other_doc = Contract.objects.create(title='Editor upload', uploaded_by=self.editor)
        other_doc.file.save('editor.pdf', ContentFile(b'%PDF-1.4 editor'))
        self.owner.delete()
        self.login(self.admin)
        response = self.client.get(reverse('contract_list'))
        self.assertEqual(response.context['deleted_uploaders'], ['uploader'])
        self.assertContains(response, 'Deleted accounts')
        self.assertContains(response, '<span class="deleted-account">uploader</span>', html=False)
        filtered = self.client.get(reverse('contract_list'), {'uploader': 'deleted:uploader'})
        self.assertEqual([c.pk for c in filtered.context['contracts']], [self.contract.pk])
        self.assertTrue(filtered.context['uploader_label_deleted'])
        self.assertIn('uploader=deleted%3Auploader', filtered.context['filter_query'])
        unknown = self.client.get(reverse('contract_list'), {'uploader': 'deleted:nobody'})
        self.assertEqual(unknown.context['uploader_filter'], '')

    def test_revision_badge_shows_version_count(self):
        ContractVersion.objects.create(contract=self.contract, version_number=2, created_by=self.owner)
        self.login(self.admin)
        response = self.client.get(reverse('contract_list'))
        self.assertContains(response, 'This PDF has 2 revisions')

    def test_deleted_uploader_name_is_preserved(self):
        self.owner.delete()
        self.contract.refresh_from_db()
        self.version.refresh_from_db()
        self.assertEqual(self.contract.uploaded_by_display, 'uploader')
        self.assertTrue(self.contract.uploaded_by_deleted)
        self.assertEqual(self.version.created_by_name, 'uploader')
        self.login(self.admin)
        self.assertContains(self.client.get(reverse('contract_list')), 'title="This account has been deleted">uploader</span>')
        history = self.client.get(reverse('contract_version_history', args=[self.contract.pk])).json()
        self.assertEqual(history['versions'][0]['created_by'], 'uploader')
        self.assertTrue(history['versions'][0]['created_by_deleted'])
