from django.apps import AppConfig
import os
import sys
import threading


class ContractsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contracts'

    def ready(self):
        from . import signals  # noqa: F401
        # Run once in runserver's autoreloader child, or with --noreload.
        if (
            'runserver' in sys.argv
            and (os.environ.get('RUN_MAIN') == 'true' or '--noreload' in sys.argv)
            and os.environ.get('SEALGUARD_SKIP_STARTUP_INTEGRITY') != '1'
        ):
            threading.Thread(
                target=self._run_startup_integrity_scan,
                name='sealguard-integrity-startup', daemon=True,
            ).start()

    @staticmethod
    def _run_startup_integrity_scan():
        from django.core.management import call_command
        try:
            call_command('verify_integrity', quiet_success=True)
        except Exception:
            import logging
            logging.getLogger('contracts.integrity').exception(
                'Startup integrity scan failed to complete'
            )
