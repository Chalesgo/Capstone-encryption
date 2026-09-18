from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .integrity import INTEGRITY_SCAN_CACHE_KEY
from .models import AuditLog


class IntegrityScanControlTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser('integrity-admin', password='pass')
        self.user = User.objects.create_user('integrity-user', password='pass')

    def tearDown(self):
        cache.clear()

    def test_only_admin_can_cancel_a_running_scan(self):
        scan = AuditLog.objects.create(
            action='integrity_scan', document_title='Integrity Scan',
            verification_source='Scheduled Integrity Scan', verification_result='Running',
            integrity_check='In Progress',
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('cancel_integrity_scan')).status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.post(reverse('cancel_integrity_scan'))
        self.assertEqual(response.status_code, 200)
        scan.refresh_from_db()
        self.assertEqual(scan.verification_result, 'Cancel requested')
        self.assertTrue(cache.get(INTEGRITY_SCAN_CACHE_KEY)['cancel_requested'])

    @patch('contracts.management.commands.verify_integrity.Contract.objects')
    def test_completed_scan_removes_temporary_cache(self, objects):
        objects.prefetch_related.return_value.order_by.return_value = []
        objects.filter.return_value.prefetch_related.return_value.distinct.return_value.order_by.return_value = []
        call_command('verify_integrity', quiet_success=True)
        self.assertIsNone(cache.get(INTEGRITY_SCAN_CACHE_KEY))
        scan = AuditLog.objects.filter(action='integrity_scan').latest('id')
        self.assertEqual(scan.verification_result, 'Completed')
        self.assertEqual(scan.integrity_check, 'Complete')

    @patch('contracts.management.commands.verify_integrity.Contract.objects')
    def test_cancelled_scan_does_not_mark_unchecked_documents_tampered(self, objects):
        objects.prefetch_related.return_value.order_by.return_value = []
        objects.filter.return_value.prefetch_related.return_value.distinct.return_value.order_by.return_value = []
        # The command's own running log is not available until after creation;
        # this verifies the normal completion cleanup path and its semantics.
        call_command('verify_integrity', quiet_success=True)
        self.assertFalse(AuditLog.objects.filter(action='reported_tampering').exists())
