from django.apps import AppConfig
import os
import sys
import threading


class ContractsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contracts'

    def ready(self):
        from . import signals  # noqa: F401
        # Start only in the serving process, never the autoreloader parent or
        # management commands such as migrate/test/shell.
        if (
            'runserver' in sys.argv
            and (os.environ.get('RUN_MAIN') == 'true' or '--noreload' in sys.argv)
            and os.environ.get('SEALGUARD_RUN_STARTUP_INTEGRITY', '1') == '1'
        ):
            from .integrity_scheduler import start_scheduler
            start_scheduler()
