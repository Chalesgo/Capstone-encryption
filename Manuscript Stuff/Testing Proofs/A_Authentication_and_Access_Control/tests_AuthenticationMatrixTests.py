# Copy of contracts/tests.py, class AuthenticationMatrixTests (lines 659-992) plus the file's imports.
# The runnable original is contracts/tests.py.

from .pdf_storage import read_pdf, write_pdf
from datetime import timedelta
import base64
import json
import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path
from unittest import expectedFailure
from unittest.mock import patch

import fitz
from Crypto.PublicKey import RSA
from django.contrib.auth.models import Permission, User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.http import HttpResponse
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
from .forms import ContractForm, sanitize_tutorial_html
from .utils import (
    decrypt_cf,
    encrypt_cf,
    embed_data_in_image,
    extract_data_from_image,
    extract_lsb_marker_from_pdf,
    generate_canonical_fingerprint,
    generate_file_hash,
    generate_hmac,
    generate_qr_code,
    generate_vector_fingerprint,
    log_activity,
    stamp_seal_on_pdf,
    verify_hmac,
)


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
                if username == self.clerk.username and password == 'wrong-password-0':
                    self.assertContains(response, 'You have 4 attempts left.')
                if username == self.clerk.username and password == 'wrong-password-5':
                    self.assertContains(response, 'temporarily locked')

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

    def test_auth05_logout_invalidates_protected_session_matrix(self):
        for user in (self.admin, self.clerk):
            for cycle in range(10):
                with self.subTest(username=user.username, cycle=cycle + 1):
                    client = Client()
                    self.assertTrue(client.login(
                        username=user.username,
                        password=self.password,
                    ))
                    protected_page = client.get(reverse('contract_list'))
                    self.assertEqual(protected_page.status_code, 200)

                    logout_response = client.post(reverse('logout'))
                    self.assertIn(logout_response.status_code, {200, 302})
                    self._assert_unauthenticated(client)

                    reopened_page = client.get(reverse('contract_list'))
                    self.assertEqual(reopened_page.status_code, 302)
                    self.assertIn(reverse('login'), reopened_page.url)

        self.assertEqual(AuditLog.objects.filter(action='logout').count(), 20)

    def test_auth06_unauthenticated_protected_actions_matrix(self):
        protected_actions = (
            ('dashboard', reverse('dashboard'), 'get'),
            ('upload', reverse('upload_contract'), 'get'),
            ('edit', reverse('rename_contract', args=[999999]), 'post'),
            ('archive', reverse('update_status', args=[999999]), 'post'),
            ('delete', reverse('delete_contract', args=[999999]), 'post'),
            ('private download', reverse('download_contract', args=[999999]), 'get'),
            ('folder reorder', reverse('reorder_folders'), 'post'),
            ('reports', reverse('dashboard'), 'get'),
        )

        for action, url, method in protected_actions:
            with self.subTest(action=action):
                client = Client()
                response = getattr(client, method)(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)
                self._assert_unauthenticated(client)

    def test_auth07_clerk_granted_contract_delete_permission(self):
        contract = Contract.objects.create(
            title='Permission-controlled contract',
            file=SimpleUploadedFile('permission-test.pdf', b'%PDF-test'),
        )
        delete_permission = Permission.objects.get(
            content_type__app_label='contracts', codename='delete_contract'
        )

        clerk_client = Client()
        self.assertTrue(clerk_client.login(
            username=self.clerk.username,
            password=self.password,
        ))
        denied = clerk_client.post(reverse('delete_contract', args=[contract.id]), {'reason': 'duplicate'})
        self.assertEqual(denied.status_code, 302)
        contract.refresh_from_db()
        self.assertFalse(contract.is_trashed)

        self.clerk.user_permissions.add(delete_permission)
        clerk_client = Client()
        self.assertTrue(clerk_client.login(
            username=self.clerk.username,
            password=self.password,
        ))
        allowed = clerk_client.post(reverse('delete_contract', args=[contract.id]), {'reason': 'duplicate'})
        self.assertEqual(allowed.status_code, 302)
        contract.refresh_from_db()
        self.assertTrue(contract.is_trashed)

    def test_auth07_rbac_role_permission_matrix(self):
        protected_actions = (
            ('dashboard', reverse('dashboard'), 'get', {}, True),
            ('upload', reverse('upload_contract'), 'get', {}, True),
            ('edit', reverse('rename_contract', args=[999999]), 'post', '{}', True),
            ('archive', reverse('update_status', args=[999999]), 'post', {}, True),
            ('delete', reverse('delete_contract', args=[999999]), 'post', {}, False),
            ('private access', reverse('contract_version_history', args=[999999]), 'get', {}, True),
            ('folder reorder', reverse('reorder_folders'), 'post', '{"folder_ids": []}', True),
            ('reports', reverse('dashboard'), 'get', {}, True),
        )

        for role, username, password in (
            ('Administrator', self.admin.username, self.password),
            ('Clerk', self.clerk.username, self.password),
        ):
            client = Client()
            self.assertTrue(client.login(username=username, password=password))
            for action, url, method, data, clerk_allowed in protected_actions:
                with self.subTest(role=role, action=action):
                    if method == 'get':
                        response = client.get(url)
                    elif isinstance(data, str):
                        response = client.post(url, data=data, content_type='application/json')
                    else:
                        response = client.post(url, data=data)
                    if role == 'Administrator' or clerk_allowed:
                        self.assertNotEqual(response.status_code, 302, f'{role} was redirected for {action}')
                    else:
                        self.assertIn(response.status_code, {302, 403})

        public_client = Client()
        for action, url, method, data, _ in protected_actions:
            with self.subTest(role='Public', action=action):
                if method == 'get':
                    response = public_client.get(url)
                elif isinstance(data, str):
                    response = public_client.post(url, data=data, content_type='application/json')
                else:
                    response = public_client.post(url, data=data)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)

    def test_auth08_object_level_authorization_matrix(self):
        user_a = User.objects.create_user('object_owner', password=self.password)
        user_b = User.objects.create_user('object_attacker', password=self.password)
        contracts = [
            Contract.objects.create(
                title=f'Private contract {number}',
                recipient=user_a,
                file=SimpleUploadedFile(
                    f'private-{number}.pdf', b'%PDF-private-test'
                ),
            )
            for number in range(10)
        ]

        client = Client()
        self.assertTrue(client.login(
            username=user_b.username,
            password=self.password,
        ))
        unauthorized_failures = []
        for contract in contracts:
            attempts = (
                ('view', client.get, reverse('contract_version_history', args=[contract.id])),
                ('edit', client.post, reverse('rename_contract', args=[contract.id])),
                ('delete', client.post, reverse('delete_contract', args=[contract.id])),
                ('download', client.get, reverse('download_contract', args=[contract.id])),
            )
            for action, request_method, url in attempts:
                if action == 'edit':
                    response = request_method(url, data='{"title":"Unauthorized edit"}', content_type='application/json')
                else:
                    response = request_method(url)
                expected_statuses = {
                    'view': {200},
                    'edit': {200},
                    'delete': {302},
                    'download': {200},
                }
                if response.status_code not in expected_statuses[action]:
                    unauthorized_failures.append((contract.id, action, response.status_code))

        self.assertEqual(unauthorized_failures, [])

    def test_auth09_authentication_audit_log_matrix(self):
        successful_clients = []
        for number in range(30):
            user = self.admin if number % 2 == 0 else self.clerk
            client = Client()
            response = client.post(reverse('login'), {
                'username': user.username,
                'password': self.password,
            })
            self.assertEqual(response.status_code, 302)
            successful_clients.append(client)

        for number in range(15):
            client = Client()
            response = client.post(reverse('login'), {
                'username': f'audit-unknown-{number}',
                'password': 'wrong-password',
            })
            self.assertEqual(response.status_code, 200)

        for number in range(3):
            user = User.objects.create_user(
                f'audit-lockout-{number}', password=self.password
            )
            for _ in range(5):
                client = Client()
                response = client.post(reverse('login'), {
                    'username': user.username,
                    'password': 'wrong-password',
                })
                self.assertEqual(response.status_code, 200)

        for client in successful_clients[:20]:
            client.post(reverse('logout'))

        expected_counts = {
            'login': 30,
            'failed_login': 30,
            'locked_out': 3,
            'logout': 20,
        }
        for action, expected_count in expected_counts.items():
            with self.subTest(action=action):
                self.assertEqual(
                    AuditLog.objects.filter(action=action).count(),
                    expected_count,
                )

        events = AuditLog.objects.filter(
            action__in=expected_counts,
        )
        self.assertEqual(events.count(), 83)
        for event in events:
            self.assertIsNotNone(event.timestamp)
            self.assertEqual(event.ip_address, '127.0.0.1')
            if event.action == 'failed_login':
                self.assertIn('username:', event.note)
            else:
                self.assertIsNotNone(event.user)

    def test_auth10_session_reuse_matrix(self):
        for cycle in range(10):
            with self.subTest(cycle=cycle + 1):
                client = Client()
                self.assertTrue(client.login(
                    username=self.clerk.username,
                    password=self.password,
                ))
                old_session_cookie = client.cookies['sessionid'].value
                client.post(reverse('logout'))

                reused_client = Client()
                reused_client.cookies['sessionid'] = old_session_cookie
                response = reused_client.get(reverse('contract_list'))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)


