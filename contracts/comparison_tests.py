import tempfile
from pathlib import Path

import fitz
from Crypto.PublicKey import RSA
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Contract, ContractVersion


class RevisionComparisonTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        key = RSA.generate(2048)
        (root / 'private.pem').write_bytes(key.export_key())
        (root / 'public.pem').write_bytes(key.publickey().export_key())
        cls.config = override_settings(MEDIA_ROOT=str(root / 'media'),
            RSA_PRIVATE_KEY_PATH=str(root / 'private.pem'), RSA_PUBLIC_KEY_PATH=str(root / 'public.pem'))
        cls.config.enable()

    @classmethod
    def tearDownClass(cls):
        cls.config.disable()
        cls.directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.owner = User.objects.create_user('comparison-owner', is_staff=True)
        self.reader = User.objects.create_user('comparison-reader')
        self.contract = Contract.objects.create(title='Revision test', uploaded_by=self.owner)
        self.left = self.add_version(1, 'Payment is 100 pesos.')
        self.right = self.add_version(2, 'Payment is 200 pesos. <script>alert(1)</script>')
        self.url = reverse('compare_revisions', args=[self.contract.pk])
        self.client.force_login(self.owner)

    def add_version(self, number, text, contract=None):
        version = ContractVersion(contract=contract or self.contract, version_number=number,
                                  source='revision', created_by=self.owner)
        with fitz.open() as pdf:
            page = pdf.new_page()
            if text:
                page.insert_text((40, 60), text)
            version.file.save(f'version-{number}.pdf', ContentFile(pdf.tobytes()))
        return version

    def test_default_pair_shows_changes_metadata_and_internal_viewers(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['left_version'], self.left)
        self.assertEqual(response.context['right_version'], self.right)
        self.assertContains(response, 'comparison-owner')
        self.assertContains(response, 'Payment is 100 pesos.')
        self.assertContains(response, 'Payment is 200 pesos.')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, 'comparison=1', count=2)
        self.assertEqual(response.context['before']['pages'], 1)

    def test_any_saved_pair_can_be_selected_and_same_pair_is_identical(self):
        self.add_version(3, 'Third version')
        response = self.client.get(self.url, {'left': 1, 'right': 1})
        self.assertTrue(response.context['identical'])
        self.assertEqual(response.context['differences'], [])

    def test_permission_and_trashed_document_checks(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.contract.collaborators.add(self.reader)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.contract.collaborators.remove(self.reader)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.owner)
        self.contract.is_trashed = True
        self.contract.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_cannot_select_a_version_from_another_document(self):
        other = Contract.objects.create(title='Other document', uploaded_by=self.owner)
        self.add_version(99, 'Other document contents', other)
        self.assertEqual(self.client.get(self.url, {'right': 99}).status_code, 404)
        self.assertEqual(self.client.get(self.url, {'right': 'invalid'}).status_code, 404)

    def test_one_version_explains_next_step(self):
        self.right.delete()
        self.assertContains(self.client.get(self.url), 'at least two saved versions')

    def test_scanned_pages_do_not_claim_visual_equality(self):
        self.add_version(3, '')
        response = self.client.get(self.url)
        self.assertContains(response, 'no extractable text')
        self.assertContains(response, 'no OCR has been performed')

    def test_unreadable_file_has_safe_error(self):
        Path(self.right.file.path).write_bytes(b'corrupted')
        response = self.client.get(self.url)
        self.assertContains(response, 'could not be opened')
        self.assertNotContains(response, '<iframe')
