from unittest.mock import Mock, patch

from django.apps import apps
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from .integrity import INTEGRITY_SCAN_CACHE_KEY, INTEGRITY_SCAN_LOCK_KEY
from .integrity_scheduler import DAILY_SECONDS, launch_scan, scheduler_loop
from .models import AuditLog


class SchedulerTests(SimpleTestCase):
    @patch('contracts.integrity_scheduler.start_scheduler')
    @patch('contracts.apps.sys.argv', ['manage.py', 'runserver'])
    @patch.dict('os.environ', {'RUN_MAIN': 'true', 'SEALGUARD_RUN_STARTUP_INTEGRITY': '1'})
    def test_serving_process_starts_scheduler(self, start):
        apps.get_app_config('contracts').ready()
        start.assert_called_once()

    @patch('contracts.integrity_scheduler.start_scheduler')
    @patch('contracts.apps.sys.argv', ['manage.py', 'migrate'])
    def test_management_commands_do_not_start_scheduler(self, start):
        apps.get_app_config('contracts').ready()
        start.assert_not_called()

    @patch('contracts.integrity_scheduler.launch_scan')
    def test_startup_then_daily_without_waiting_in_test(self, launch):
        stop = Mock()
        stop.wait.side_effect = [False, True]
        scheduler_loop(stop)
        self.assertEqual([c.args[0] for c in launch.call_args_list],
                         ['Startup Integrity Scan', 'Daily Integrity Scan'])
        stop.wait.assert_called_with(DAILY_SECONDS)

    @patch('contracts.integrity_scheduler.threading.Thread')
    @patch('contracts.integrity_scheduler.cache.get', return_value=True)
    def test_active_scan_skips_trigger(self, get, thread):
        self.assertFalse(launch_scan('Daily Integrity Scan'))
        thread.assert_not_called()


class FailedScanTests(TestCase):
    def tearDown(self):
        cache.clear()

    @patch('contracts.management.commands.verify_integrity.Contract.objects')
    def test_error_finishes_status_and_releases_lock(self, objects):
        cache.clear()
        objects.prefetch_related.side_effect = RuntimeError('scan unavailable')
        with self.assertRaises(RuntimeError):
            call_command('verify_integrity', quiet_success=True)
        scan = AuditLog.objects.filter(action='integrity_scan').latest('id')
        self.assertEqual(scan.verification_result, 'Interrupted')
        self.assertIsNone(cache.get(INTEGRITY_SCAN_LOCK_KEY))
        self.assertIsNone(cache.get(INTEGRITY_SCAN_CACHE_KEY))
