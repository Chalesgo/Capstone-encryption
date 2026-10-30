import tempfile

import fitz

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import ContractForm
from .models import AuditLog, Contract, Folder


class DocumentHandlingTests(TestCase):
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
        self.admin = User.objects.create_superuser('handling_admin', 'admin@example.com', 'Handling-pass-2026!')
        self.staff = User.objects.create_user('handling_staff', password='Handling-pass-2026!', is_staff=True)
        self.client.force_login(self.admin)

    def make_contract(self, title, status='pending', is_public=False, folder=None):
        return Contract.objects.create(
            title=title, status=status, is_public=is_public, folder=folder,
            uploaded_by=self.admin, recipient=self.admin,
            file=SimpleUploadedFile(f'{title}.pdf', b'%PDF-1.7\nHandling fixture', content_type='application/pdf'),
        )

    def test_trash_requires_a_reason_and_records_it(self):
        contract = self.make_contract('Mistaken upload')
        self.client.post(reverse('delete_contract', args=[contract.id]))
        contract.refresh_from_db()
        self.assertFalse(contract.is_trashed)

        self.client.post(reverse('delete_contract', args=[contract.id]),
                         {'reason': 'wrong_file', 'reason_details': 'Uploaded the 2024 copy'})
        contract.refresh_from_db()
        self.assertTrue(contract.is_trashed)
        self.assertEqual(contract.trash_reason, 'Wrong file uploaded: Uploaded the 2024 copy')
        self.assertTrue(AuditLog.objects.filter(contract=contract, action='deleted',
                                                note__contains='reason: Wrong file uploaded').exists())
        self.assertContains(self.client.get(reverse('trash_list')), 'Reason: Wrong file uploaded')

        self.client.post(reverse('restore_contract', args=[contract.id]))
        contract.refresh_from_db()
        self.assertEqual((contract.is_trashed, contract.trash_reason), (False, ''))

    def test_final_copies_cannot_be_trashed(self):
        final = self.make_contract('Official contract', status='final', is_public=True)
        self.client.post(reverse('delete_contract', args=[final.id]), {'reason': 'duplicate'})
        final.refresh_from_db()
        self.assertFalse(final.is_trashed)

        draft = self.make_contract('Practice upload')
        response = self.client.post(reverse('bulk_delete_contracts'), {'ids': [final.id, draft.id]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'reason_required')
        response = self.client.post(reverse('bulk_delete_contracts'),
                                    {'ids': [final.id, draft.id], 'reason': 'test_upload'})
        self.assertEqual(response.json(), {'success': True, 'moved': 1, 'skipped_public': 1})
        draft.refresh_from_db()
        self.assertEqual(draft.trash_reason, 'Test or practice upload')

    def test_each_role_lands_on_its_own_page(self):
        self.assertRedirects(self.client.get('/'), reverse('dashboard'), fetch_redirect_response=False)
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.get('/'), reverse('contract_list'), fetch_redirect_response=False)
        self.client.logout()
        self.assertRedirects(self.client.get('/'), reverse('public_verify'), fetch_redirect_response=False)

    def test_folder_is_chosen_at_upload(self):
        folder = Folder.objects.create(name='Purchase Orders 2026', owner=self.admin)
        page = self.client.get(reverse('upload_contract'))
        self.assertContains(page, 'Folder (optional)')
        self.assertContains(page, 'Purchase Orders 2026')
        self.assertContains(page, 'No folder (Unsorted)')
        document = fitz.open()
        document.new_page().insert_text((72, 72), 'Purchase order 14')
        pdf_bytes = document.tobytes()
        document.close()
        form = ContractForm({'title': 'PO 14', 'folder': folder.id}, {
            'file': SimpleUploadedFile('po.pdf', pdf_bytes, content_type='application/pdf'),
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).folder, folder)

    def test_document_list_shows_folder_and_add_or_move_wording(self):
        folder = Folder.objects.create(name='Infrastructure', owner=self.admin)
        self.make_contract('Filed contract', folder=folder)
        self.make_contract('Unsorted contract')
        page = self.client.get(reverse('contract_list'))
        self.assertContains(page, '<span class="folder-chip-name">Infrastructure</span>')
        self.assertContains(page, 'id="contract-folder-action-label"')
        self.assertContains(page, "row && row.dataset.folder ? 'Move to Folder' : 'Add to Folder'")
        self.assertContains(page, 'Register Signed Scan')

    def test_upload_title_is_not_replaced_once_typed(self):
        page = self.client.get(reverse('upload_contract'))
        self.assertContains(page, "titleInput.dataset.autofilled === titleInput.value")
