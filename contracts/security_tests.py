"""Authorized, non-destructive application security regression tests.

These tests exercise the Django application boundary with harmless payloads.
They deliberately do not attempt destructive SQL, external scanning, or
browser-only DOM execution.  Run this module explicitly with the command in
the security test report.
"""

from io import BytesIO

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from .forms import sanitize_tutorial_html
from .models import Contract


class ApplicationSecurityMatrixTests(TestCase):
    """Safe local matrices for the previously incomplete security checks."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="security-admin", email="security@example.test", password="Security-pass-123!"
        )
        cls.staff = User.objects.create_user(
            username="security-staff", password="Security-pass-123!", is_staff=True
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.staff)

    @staticmethod
    def _assert_safe_response(test_case, response):
        test_case.assertLess(response.status_code, 500)
        body = response.content.decode("utf-8", errors="replace").lower()
        for marker in ("traceback", "django.db.utils", "syntax error", "no such table"):
            test_case.assertNotIn(marker, body)

    @staticmethod
    def _assert_not_executable(test_case, body, payload):
        """Only reject an unescaped HTML payload; plain text can be reflected safely."""
        if "<" in payload:
            test_case.assertNotIn(payload.lower(), body.lower())

    def test_sec01_sql_injection_matrix_100_safe_requests(self):
        """Twenty database-related inputs x five non-destructive SQL payloads."""
        payloads = (
            "' OR '1'='1' --",
            "1' UNION SELECT NULL --",
            "'/**/OR/**/1=1--",
            ";SELECT 1;--",
            "admin'--",
        )
        parameter_targets = (
            ("contract_list", "q"), ("contract_list", "status"),
            ("contract_list", "folder"), ("contract_list", "page"),
            ("contract_list", "per_page"), ("dashboard", "document"),
            ("dashboard", "user"), ("dashboard", "status"),
            ("dashboard", "page"), ("dashboard", "per_page"),
            ("dashboard", "date_from"), ("dashboard", "date_to"),
            ("dashboard", "sort"), ("dashboard", "activity"),
            ("public_verify", "page"), ("public_verify", "q"),
            ("contract_list", "order"), ("dashboard", "filter"),
            ("public_verify", "search"), ("contract_list", "title"),
        )
        for name, parameter in parameter_targets:
            for payload in payloads:
                with self.subTest(url=name, parameter=parameter, payload=payload):
                    response = self.client.get(reverse(name), {parameter: payload})
                    self._assert_safe_response(self, response)

    def test_sec02_xss_matrix_60_inputs(self):
        """Twenty payloads are checked in reflected, stored, and client data paths."""
        payloads = (
            "<script>alert(1)</script>", "<img src=x onerror=alert(1)>",
            "<svg/onload=alert(1)>", "javascript:alert(1)",
            "</textarea><script>alert(1)</script>", "<iframe src=javascript:alert(1)>",
            "<body onload=alert(1)>", "<a href='javascript:alert(1)'>x</a>",
            "<div onclick='alert(1)'>x</div>", "<style>@import url(javascript:alert(1))</style>",
            "&#60;script&#62;alert(1)&#60;/script&#62;", "<math><mi//xlink:href='data:x'>",
            "<input autofocus onfocus=alert(1)>", "<object data='javascript:alert(1)'>",
            "<form action='javascript:alert(1)'>x</form>", "<details open ontoggle=alert(1)>",
            "<video><source onerror=alert(1)>", "<marquee onstart=alert(1)>",
            "<select autofocus onfocus=alert(1)>", "<svg><script>alert(1)</script></svg>",
        )
        for payload in payloads:
            with self.subTest(context="reflected", payload=payload):
                response = self.client.get(reverse("contract_list"), {"q": payload})
                self._assert_safe_response(self, response)
                self._assert_not_executable(
                    self, response.content.decode("utf-8", errors="replace"), payload
                )

            with self.subTest(context="stored", payload=payload):
                cleaned = sanitize_tutorial_html(payload)
                if "<" in payload:
                    self.assertNotIn(payload.lower(), cleaned.lower())
                if "<" in payload:
                    self.assertNotIn("javascript:", cleaned.lower())

            with self.subTest(context="client-data", payload=payload):
                response = self.client.get(reverse("dashboard"), {"document": payload})
                self._assert_safe_response(self, response)
                self._assert_not_executable(
                    self, response.content.decode("utf-8", errors="replace"), payload
                )

    def test_sec05_public_object_access_matrix_40_requests(self):
        """Ten private contracts x four file/object actions without authentication.

        SealGuard intentionally makes documents visible to authenticated staff;
        this matrix therefore tests the public boundary, which must not expose
        private objects.
        """
        owner = User.objects.create_user("security-owner", password="owner-pass-123!")
        contracts = [
            Contract.objects.create(
                title=f"Security private {number}",
                recipient=owner,
                file=SimpleUploadedFile(f"private-{number}.pdf", b"%PDF-1.4 security fixture"),
                is_public=False,
            )
            for number in range(10)
        ]
        public_client = Client()
        for contract in contracts:
            urls = (
                reverse("contract_version_history", args=[contract.id]),
                reverse("preview_contract", args=[contract.id]),
                reverse("download_contract", args=[contract.id]),
                reverse("mark_contract_viewed", args=[contract.id]),
            )
            for url in urls:
                with self.subTest(contract=contract.id, url=url):
                    response = public_client.get(url) if "mark-viewed" not in url else public_client.post(url)
                    self.assertIn(response.status_code, {302, 401, 403})

    def test_sec06_http_upload_abuse_matrix_30_requests(self):
        """Thirty invalid uploads are rejected before a contract is created."""
        cases = []
        cases.extend((f"wrong-{i}.txt", b"not a pdf", "text/plain") for i in range(5))
        cases.extend((f"empty-{i}.pdf", b"", "application/pdf") for i in range(5))
        cases.extend((f"malformed-{i}.pdf", b"%PDF-not-a-real-document", "application/pdf") for i in range(5))
        cases.extend((f"renamed-{i}.pdf", b"plain text renamed as pdf", "application/pdf") for i in range(5))
        oversized = b"%PDF-1.4 " + (b"x" * (settings.MAX_UPLOAD_SIZE + 1))
        cases.extend((f"oversized-{i}.pdf", oversized, "application/pdf") for i in range(5))
        cases.extend((f"encrypted-{i}.pdf", b"%PDF-1.4 /Encrypt unsupported", "application/pdf") for i in range(5))

        before = Contract.objects.count()
        for filename, data, content_type in cases:
            with self.subTest(filename=filename):
                upload = SimpleUploadedFile(filename, data, content_type=content_type)
                response = self.client.post(reverse("upload_contract"), {"title": filename, "file": upload})
                self.assertIn(response.status_code, {200, 400})
                self._assert_safe_response(self, response)
        self.assertEqual(Contract.objects.count(), before)
