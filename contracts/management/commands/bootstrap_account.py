"""Create one explicitly configured deployment test account, if requested."""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the opt-in deployment bootstrap administrator if it does not exist."

    def handle(self, *args, **options):
        username = os.environ.get("SEALGUARD_BOOTSTRAP_USERNAME", "").strip()
        password = os.environ.get("SEALGUARD_BOOTSTRAP_PASSWORD", "")
        if not username and not password:
            return
        if not username or not password:
            self.stderr.write(
                "SEALGUARD_BOOTSTRAP_USERNAME and SEALGUARD_BOOTSTRAP_PASSWORD must both be set."
            )
            return

        user_model = get_user_model()
        user = user_model.objects.filter(username=username).first()
        if user is not None:
            self.stdout.write(f"Bootstrap account '{username}' already exists; leaving it unchanged.")
            return

        user_model.objects.create_superuser(username=username, password=password)
        self.stdout.write(f"Created bootstrap administrator '{username}'.")
