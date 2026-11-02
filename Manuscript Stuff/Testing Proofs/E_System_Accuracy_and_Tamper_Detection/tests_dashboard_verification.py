from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import AuditLog, Contract


class DashboardVerificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('investigation-admin', password='test-password')
        self.staff = User.objects.create_user('investigation-staff', password='test-password', is_staff=True)
        self.reader = User.objects.create_user('investigation-reader', password='test-password')
        self.contract = Contract.objects.create(title='Investigation fixture', uploaded_by=self.admin)
        self.warning = AuditLog.objects.create(
            contract=self.contract, action='reported_tampering',
            verification_result='Possible Modification', integrity_check='Failed',
            verification_debug_log='[FAIL] Vector fingerprint differs from the official record',
        )
        self.authentic = AuditLog.objects.create(
            contract=self.contract, action='viewed', verification_result='Authentic',
            integrity_check='Complete', verification_debug_log='Authentic trace',
        )

    def detail_url(self, entry):
        return reverse('audit_verification_log_detail', args=[entry.id])

    def test_staff_can_investigate_visible_non_authentic_results(self):
        self.client.force_login(self.staff)
        for result in ['Possible Modification', 'Tampered', 'Not Found', 'Unable to Verify']:
            with self.subTest(result=result):
                self.warning.verification_result = result
                self.warning.save(update_fields=['verification_result'])
                response = self.client.get(self.detail_url(self.warning))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['result'], result)
                self.assertEqual(response.json()['debug_log'], self.warning.verification_debug_log)

    def test_staff_cannot_read_authentic_traces(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.detail_url(self.authentic)).status_code, 403)

    def test_staff_cannot_read_unrelated_unlinked_reports(self):
        hidden = AuditLog.objects.create(
            user=self.admin, action='verification', verification_result='Unable to Verify',
            verification_debug_log='Unrelated private trace',
        )
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.detail_url(hidden)).status_code, 404)

    def test_read_only_and_anonymous_users_cannot_read_traces(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(self.detail_url(self.warning)).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.detail_url(self.warning)).status_code, 302)

    def test_admin_keeps_access_to_authentic_and_warning_traces(self):
        self.client.force_login(self.admin)
        for entry in [self.warning, self.authentic]:
            self.assertEqual(self.client.get(self.detail_url(entry)).status_code, 200)

    def test_history_reports_selected_activity_result_without_inline_traces(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('audit_verification_history', args=[self.warning.id]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['selected_result'], 'Possible Modification')
        self.assertEqual(len(data['events']), 2)
        self.assertTrue(any(event['is_selected'] and event['id'] == self.warning.id for event in data['events']))
        self.assertTrue(all('debug_log' not in event for event in data['events']))

    def test_dashboard_renders_investigation_controls_but_contract_list_does_not(self):
        self.client.force_login(self.admin)
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, 'id="audit-pdf-verification-result"')
        self.assertContains(dashboard, 'id="audit-pdf-verification-logs" hidden')
        self.assertContains(dashboard, 'dashboard-verification.js')
        self.assertNotContains(dashboard, self.warning.verification_debug_log)
        listing = self.client.get(reverse('contract_list'))
        self.assertNotContains(listing, 'id="audit-pdf-verification-result"')
