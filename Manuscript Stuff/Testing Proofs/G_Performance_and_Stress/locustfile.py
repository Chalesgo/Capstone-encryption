"""Locust workload definitions for the SealGuard performance baseline.

The harness deliberately reads credentials and fixture paths from environment
variables.  It does not create users or contracts and is intended to run
against a prepared staging/demo database, never the production database.

Required environment variables:
    SEALGUARD_USERNAME
    SEALGUARD_PASSWORD

Optional environment variables:
    SEALGUARD_BASE_URL (default: http://127.0.0.1:8000)
    SEALGUARD_PDF (default: output/pdf/01_basic_contract.pdf)
    SEALGUARD_AUTHENTIC_PDF (default: same as SEALGUARD_PDF)
    SEALGUARD_TAMPERED_PDF (default: output/pdf/08_tampered_basic_contract.pdf)
    SEALGUARD_SEARCH_TERM (default: contract)
    SEALGUARD_SCENARIO (mixed, view, verify, upload, login)
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from locust import HttpUser, between, task, tag


CSRF_RE = re.compile(r'name=["\']csrfmiddlewaretoken["\'][^>]*value=["\']([^"\']+)', re.I)


def env_path(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else Path(__file__).resolve().parent.parent / value


class SealGuardUser(HttpUser):
    """Authenticated SealGuard user for browser-like HTTP workloads."""

    wait_time = between(0.2, 1.0)
    abstract = True

    def on_start(self):
        self.username = os.getenv("SEALGUARD_USERNAME")
        self.password = os.getenv("SEALGUARD_PASSWORD")
        if not self.username or not self.password:
            raise RuntimeError(
                "Set SEALGUARD_USERNAME and SEALGUARD_PASSWORD before starting Locust."
            )

        self.pdf_path = env_path("SEALGUARD_PDF", "output/pdf/01_basic_contract.pdf")
        self.authentic_pdf_path = env_path(
            "SEALGUARD_AUTHENTIC_PDF", str(self.pdf_path)
        )
        self.tampered_pdf_path = env_path(
            "SEALGUARD_TAMPERED_PDF", "output/pdf/08_tampered_basic_contract.pdf"
        )
        self.search_term = os.getenv("SEALGUARD_SEARCH_TERM", "contract")
        self._login()

    def _csrf_token(self, response):
        match = CSRF_RE.search(response.text)
        return match.group(1) if match else None

    def _login(self):
        page = self.client.get("/accounts/login/", name="login page")
        token = self._csrf_token(page)
        data = {"username": self.username, "password": self.password}
        if token:
            data["csrfmiddlewaretoken"] = token
        response = self.client.post(
            "/accounts/login/",
            data=data,
            headers={"Referer": self.client.base_url + "/accounts/login/"},
            name="login",
            allow_redirects=True,
        )
        if response.status_code >= 500 or "/accounts/login/" in response.url:
            response.failure("SealGuard login did not complete")

    def _upload(self, path: Path, endpoint: str, field_name: str, name: str):
        if not path.is_file():
            raise FileNotFoundError(f"Performance PDF not found: {path}")
        page = self.client.get(endpoint, name=f"{name} page")
        token = self._csrf_token(page)
        # Give concurrent upload requests independent document identities.
        # Reusing one title would intentionally drive the duplicate/revision
        # workflow and make the performance workload measure revision races
        # instead of normal independent uploads.
        data = {"title": f"Performance {path.stem} {uuid.uuid4().hex[:10]}"}
        if token:
            data["csrfmiddlewaretoken"] = token
        upload_name = path.name
        if endpoint == "/verify/":
            upload_name = os.getenv("SEALGUARD_AUTHENTIC_FILENAME", "authentic_performance.pdf")
        else:
            upload_name = f"{path.stem}-{uuid.uuid4().hex[:10]}.pdf"
        with path.open("rb") as pdf:
            self.client.post(
                endpoint,
                files={field_name: (upload_name, pdf, "application/pdf")},
                name=name,
                data=data,
                headers={"Referer": self.client.base_url + endpoint},
            )


class MixedWorkloadUser(SealGuardUser):
    """PERF-06 mixed workload: view/search, verify, login, upload, dashboard."""

    @tag("view", "search")
    @task(40)
    def view_and_search(self):
        self.client.get("/contracts/", name="contract list")
        self.client.get(
            "/contracts/", params={"q": self.search_term}, name="contract search"
        )

    @tag("verify")
    @task(25)
    def verify(self):
        self._upload(
            self.authentic_pdf_path, "/verify/", "pdf_file", "public verification"
        )

    @tag("login")
    @task(15)
    def login(self):
        self._login()

    @tag("upload")
    @task(10)
    def upload(self):
        self._upload(
            self.pdf_path, "/upload/", "file", "contract upload"
        )

    @tag("dashboard")
    @task(10)
    def dashboard(self):
        self.client.get("/dashboard/", name="dashboard")


class ViewSearchUser(SealGuardUser):
    @tag("view", "search")
    @task
    def view_and_search(self):
        self.client.get("/contracts/", name="contract list")
        self.client.get(
            "/contracts/", params={"q": self.search_term}, name="contract search"
        )


class VerificationUser(SealGuardUser):
    @tag("verify")
    @task
    def verify(self):
        self._upload(
            self.authentic_pdf_path, "/verify/", "pdf_file", "public verification"
        )


class UploadUser(SealGuardUser):
    @tag("upload")
    @task
    def upload(self):
        self._upload(self.pdf_path, "/upload/", "file", "contract upload")


class FixtureUploadUser(SealGuardUser):
    """Upload exactly one controlled-size fixture per Locust user."""

    abstract = True
    fixture_band = "1mb"
    wait_time = between(0.2, 0.5)

    def on_start(self):
        super().on_start()
        fixture_dir = Path(__file__).resolve().parent / "fixtures"
        fixtures = sorted(fixture_dir.glob(f"perf_{self.fixture_band}_*.pdf"))
        if not fixtures:
            raise FileNotFoundError(f"No fixture PDFs found for band: {self.fixture_band}")
        path = fixtures[self.environment.runner.user_count % len(fixtures)]
        self._upload(path, "/upload/", "file", f"fixture upload {self.fixture_band}")

    @task
    def hold_after_upload(self):
        # Keep the user alive until the short measurement window ends without
        # creating additional uploads.
        pass


class Fixture1MBUser(FixtureUploadUser):
    fixture_band = "1mb"


class Fixture5MBUser(FixtureUploadUser):
    fixture_band = "5mb"


class FixtureNearLimitUser(FixtureUploadUser):
    fixture_band = "near_limit"


class LoginUser(SealGuardUser):
    """A login-focused user; Locust still performs the initial login on start."""

    wait_time = between(0.5, 1.0)

    @tag("login")
    @task
    def repeated_login(self):
        self._login()
