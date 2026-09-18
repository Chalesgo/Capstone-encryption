from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Contract, DocumentAccessLink, DocumentAccessRequest, PasswordResetRequest


class NotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('notifications-admin', password='test')
        self.staff = User.objects.create_user('notifications-staff', is_staff=True)
        self.user = User.objects.create_user('notifications-user')
        self.reset = PasswordResetRequest.objects.create(user=self.staff, email='staff@example.test')
        self.contract = Contract.objects.create(title='Notification PDF')
        link = DocumentAccessLink.objects.create(contract=self.contract)
        self.entry = DocumentAccessRequest.objects.create(
            link=link, name='Visitor', email='visitor@example.test', reason='Review',
            status='pending', email_verified_at=timezone.now(), otp_expires_at=timezone.now(),
        )

    def feed(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('notification_list')).json()

    def test_admin_receives_both_requests_and_correct_destinations(self):
        data = self.feed(self.admin)
        self.assertEqual(data['count'], 2)
        items = {item['id']: item for item in data['notifications']}
        password_url = items[f'password-{self.reset.pk}']['url']
        self.assertEqual(password_url, reverse('admin:contracts_passwordresetrequest_change', args=[self.reset.pk]))
        self.assertEqual(self.client.get(password_url).status_code, 200)

    def test_staff_only_receives_document_and_can_open_exact_request(self):
        data = self.feed(self.staff)
        self.assertEqual(data['count'], 1)
        item = data['notifications'][0]
        self.assertEqual(item['id'], f'document-{self.entry.pk}')
        response = self.client.get(item['url'])
        self.assertContains(response, 'visitor@example.test')
        self.assertEqual(list(response.context['entries']), [self.entry])

    def test_unverified_reviewed_and_trashed_requests_are_excluded(self):
        self.entry.email_verified_at = None
        self.entry.save()
        self.assertEqual(self.feed(self.staff)['count'], 0)
        self.entry.email_verified_at = timezone.now()
        self.entry.status = 'approved'
        self.entry.save()
        self.assertEqual(self.feed(self.staff)['count'], 0)
        self.entry.status = 'pending'
        self.entry.save()
        self.contract.is_trashed = True
        self.contract.save()
        self.assertEqual(self.feed(self.staff)['count'], 0)
        self.reset.status = 'used'
        self.reset.save()
        self.assertEqual(self.feed(self.admin)['count'], 0)

    def test_regular_user_and_public_cannot_read_requests(self):
        self.assertEqual(self.feed(self.user), {'count': 0, 'notifications': []})
        self.assertEqual(self.client.get(reverse('document_approval_queue') + f'?request_id={self.entry.pk}').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('notification_list')).status_code, 302)

    def test_notification_modal_and_mobile_bell_render(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('document_approval_queue'))
        self.assertContains(response, 'id="notification-dialog"')
        self.assertContains(response, 'id="notification-trigger"')
        self.assertContains(response, '#notification-trigger { display:inline-flex;')
