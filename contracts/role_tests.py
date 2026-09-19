from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
import tempfile
from django.urls import reverse

from .models import Contract
from .models import AuditLog


class SealGuardRoleTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        media_override = override_settings(MEDIA_ROOT=directory.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.admin = User.objects.create_superuser('role-admin', password='pass')
        self.staff = User.objects.create_user('role-staff', password='pass', is_staff=True)
        self.user = User.objects.create_user('role-user', password='pass')
        self.contract = Contract.objects.create(
            title='Assigned read-only PDF', uploaded_by=self.user, recipient=self.user,
            file=SimpleUploadedFile('assigned.pdf', b'%PDF-1.4 test', content_type='application/pdf'),
        )

    def test_final_copy_publishes_document(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('update_status', args=[self.contract.pk]), {'status': 'final'})
        self.assertEqual(response.status_code, 302)
        self.contract.refresh_from_db()
        self.assertTrue(self.contract.is_public)
        self.assertEqual(self.contract.status, 'final')
        self.client.logout()
        self.assertContains(self.client.get('/verify/'), self.contract.title)

    def test_bulk_final_copy_publishes_existing_private_final(self):
        self.contract.status = 'final'
        self.contract.save()
        self.client.force_login(self.admin)
        response = self.client.post(reverse('bulk_update_status'), {'ids': [self.contract.pk], 'status': 'final'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['updated'], 1)
        self.contract.refresh_from_db()
        self.assertTrue(self.contract.is_public)

    def test_staff_cannot_publish_by_finalizing(self):
        self.contract.uploaded_by = self.staff
        self.contract.save()
        self.client.force_login(self.staff)
        self.client.post(reverse('update_status', args=[self.contract.pk]), {'status': 'final'})
        response = self.client.post(reverse('bulk_update_status'), {'ids': [self.contract.pk], 'status': 'final'})
        self.assertEqual(response.status_code, 403)
        self.contract.refresh_from_db()
        self.assertFalse(self.contract.is_public)
        self.assertNotEqual(self.contract.status, 'final')

    def test_user_can_view_assigned_pdf_but_not_mutate_or_download(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('contract_list')).status_code, 200)
        self.assertContains(self.client.get(reverse('contract_list')), 'Assigned read-only PDF')
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        preview = self.client.get(reverse('preview_contract', args=[self.contract.pk]))
        self.assertEqual(preview.status_code, 200)
        preview.close()
        self.assertIn(self.client.get(reverse('download_contract', args=[self.contract.pk])).status_code, {302, 404})
        self.assertEqual(self.client.post(
            reverse('rename_contract', args=[self.contract.pk]),
            data='{"title":"Blocked"}', content_type='application/json',
        ).status_code, 404)

    def test_staff_has_workspace_but_not_django_admin(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('contract_list')).status_code, 200)
        self.assertContains(self.client.get(reverse('contract_list')), 'Assigned read-only PDF')
        preview = self.client.get(reverse('preview_contract', args=[self.contract.pk]))
        self.assertEqual(preview.status_code, 200)
        preview.close()
        history = self.client.get(reverse('contract_version_history', args=[self.contract.pk]))
        self.assertEqual(history.status_code, 200)
        self.assertIn('versions', history.json())
        self.assertEqual(self.client.post(
            reverse('rename_contract', args=[self.contract.pk]),
            data='{"title":"Blocked"}', content_type='application/json',
        ).status_code, 404)
        self.assertEqual(self.client.get('/admin/').status_code, 302)

    def test_staff_sees_integrity_scan_status_without_admin_controls(self):
        AuditLog.objects.create(
            action='integrity_scan', document_title='Integrity Scan',
            verification_source='Scheduled Integrity Scan',
            verification_result='Running', integrity_check='In Progress',
        )
        self.client.force_login(self.staff)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Integrity scan in progress')
        self.assertNotContains(response, 'Run integrity scan')
        self.assertNotContains(response, 'Cancel scan')

    def test_superuser_has_full_access_and_admin(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(self.client.post(
            reverse('rename_contract', args=[self.contract.pk]),
            data='{"title":"Admin edit"}', content_type='application/json',
        ).status_code, 200)

    def test_role_group_follows_staff_flag(self):
        self.assertTrue(self.staff.groups.filter(name='SealGuard Staff').exists())
        self.staff.is_staff = False
        self.staff.save(update_fields=['is_staff'])
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.groups.filter(name='SealGuard User').exists())
        self.assertFalse(self.staff.groups.filter(name='SealGuard Staff').exists())
        self.assertFalse(self.staff.has_perm('contracts.upload_contract'))
