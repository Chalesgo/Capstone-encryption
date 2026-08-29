from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AuditLog, Contract, ContractVersion, Folder
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
        upload = SimpleUploadedFile('document.pdf', b'%PDF-test', content_type='application/pdf')

        response = self.client.post(reverse('public_verify'), {'pdf_file': upload})

        self.assertRedirects(response, reverse('public_verify'), fetch_redirect_response=False)
        viewed = AuditLog.objects.get(action='viewed')
        self.assertEqual(viewed.contract, self.contract)

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
