from django.apps import AppConfig
import os
import sys
import threading


class ContractsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contracts'

    def ready(self):
        from . import signals  # noqa: F401
        # Integrity scans are expensive and write progress/audit rows. They
        # must not start automatically with the web server because they can
        # monopolize SQLite's single writer and block admin account creation.
        # Run them explicitly with `python manage.py verify_integrity`, or set
        # SEALGUARD_RUN_STARTUP_INTEGRITY=1 for a deliberately isolated demo.
        if (
            'runserver' in sys.argv
            and (os.environ.get('RUN_MAIN') == 'true' or '--noreload' in sys.argv)
            and os.environ.get('SEALGUARD_RUN_STARTUP_INTEGRITY') == '1'
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
