"""Background startup and 24-hour scheduling for the local Django server."""
import logging
import threading

from django.core.cache import cache
from django.core.management import call_command
from django.db import close_old_connections

from .integrity import INTEGRITY_SCAN_LOCK_KEY

logger = logging.getLogger('contracts.integrity')
DAILY_SECONDS = 24 * 60 * 60
_scheduler = None
_start_lock = threading.Lock()


def launch_scan(source):
    # The command also acquires the scan lock, covering simultaneous triggers.
    if cache.get(INTEGRITY_SCAN_LOCK_KEY) or any(
        thread.name.startswith('sealguard-integrity-') and thread.is_alive()
        for thread in threading.enumerate()
    ):
        logger.info('%s skipped: another integrity scan is active.', source)
        return False

    def run():
        close_old_connections()
        try:
            call_command('verify_integrity', quiet_success=True, source=source)
        except Exception:
            logger.exception('%s failed', source)
        finally:
            close_old_connections()

    threading.Thread(target=run, name='sealguard-integrity-automatic', daemon=True).start()
    return True


def scheduler_loop(stop):
    launch_scan('Startup Integrity Scan')
    # Count daily intervals independently of scan duration. An overlapping
    # trigger is skipped, never queued to run just after cancellation.
    while not stop.wait(DAILY_SECONDS):
        launch_scan('Daily Integrity Scan')


def start_scheduler():
    global _scheduler
    with _start_lock:
        if _scheduler is not None and _scheduler.is_alive():
            return
        _scheduler = threading.Thread(
            target=scheduler_loop, args=(threading.Event(),),
            name='sealguard-scan-scheduler', daemon=True,
        )
        _scheduler.start()
