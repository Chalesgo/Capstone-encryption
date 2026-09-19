from django.apps import AppConfig
import os
import sys
import threading


class ContractsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contracts'

    def ready(self):
        from . import signals  # noqa: F401
        # The production start script enables the scheduler only after migrate
        # has completed. Render uses a single Gunicorn worker so exactly one
        # scheduler owns the startup and daily scans.
        production_server = os.environ.get('SEALGUARD_RUN_INTEGRITY_SCHEDULER') == '1'
        local_server = (
            'runserver' in sys.argv
            and (os.environ.get('RUN_MAIN') == 'true' or '--noreload' in sys.argv)
            and os.environ.get('SEALGUARD_RUN_STARTUP_INTEGRITY', '1') == '1'
        )
        if production_server or local_server:
            from .integrity_scheduler import start_scheduler
            start_scheduler()
