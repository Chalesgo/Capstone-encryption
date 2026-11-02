import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse


class DeploymentHealthTests(TestCase):
    def test_health_check_confirms_database_and_keys(self):
        response = self.client.get(reverse('health_check'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_health_check_fails_closed_when_keys_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / 'missing.pem')
            with override_settings(
                RSA_PRIVATE_KEY_PATH=missing,
                RSA_PUBLIC_KEY_PATH=missing,
            ):
                response = self.client.get(reverse('health_check'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})
