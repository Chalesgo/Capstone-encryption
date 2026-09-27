import logging
import os
import time

from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.db import close_old_connections
from django.utils import timezone

from contracts.models import AuditLog, Contract, ContractVersion
from contracts.utils import verify_version_chain
from contracts.integrity import (
    INTEGRITY_CACHE_SECONDS, INTEGRITY_RESULT_CACHE_PREFIX,
    INTEGRITY_SCAN_CACHE_KEY, INTEGRITY_SCAN_LOCK_KEY,
)
from contracts.process_memory import process_memory_mb


logger = logging.getLogger('contracts.integrity')


class Command(BaseCommand):
    help = 'Check every stored contract version and report integrity failures.'

    def add_arguments(self, parser):
        parser.add_argument('--quiet-success', action='store_true')
        parser.add_argument('--source', default='Scheduled Integrity Scan')

    def handle(self, *args, **options):
        if not cache.add(INTEGRITY_SCAN_LOCK_KEY, {'started_at': timezone.now().isoformat()}, INTEGRITY_CACHE_SECONDS):
            self.stdout.write('Integrity scan skipped because another scan is already in progress.')
            return
        try:
            return self.run_scan(*args, **options)
        except Exception:
            control = cache.get(INTEGRITY_SCAN_CACHE_KEY) or {}
            scan_id = control.get('scan_log_id')
            if scan_id:
                AuditLog.objects.filter(pk=scan_id, verification_result__in=['Running', 'Cancel requested']).update(
                    verification_result='Interrupted', integrity_check='Incomplete',
                    note='Integrity scan stopped after an error. Review the server log and run a new scan.',
                )
            logger.exception(
                'integrity_scan_crashed scan_log_id=%s source=%s',
                scan_id, options.get('source'),
            )
            raise
        finally:
            cache.delete(INTEGRITY_SCAN_CACHE_KEY)
            cache.delete(INTEGRITY_SCAN_LOCK_KEY)
            close_old_connections()

    def run_scan(self, *args, **options):
        started_at = time.perf_counter()
        scan_started_at = timezone.now()
        close_old_connections()
        logger.info(
            'integrity_scan_started source=%s pid=%s memory_mb=%s',
            options['source'], os.getpid(), process_memory_mb(),
        )
        checked_contracts = 0
        checked_versions = 0
        failures = []
        failure_count = 0
        max_failure_details = 100
        failure_keys = set()
        debug_lines = ['Integrity scan started.']
        scan_log = AuditLog.objects.create(
            action='integrity_scan',
            document_title='Integrity Scan',
            note='Integrity scan is currently running.',
            verification_source=options['source'],
            verification_result='Running',
            integrity_check='In Progress',
            verification_debug_log='\n'.join(debug_lines),
        )
        cache.set(INTEGRITY_SCAN_CACHE_KEY, {
            'scan_log_id': scan_log.id,
            'started_at': scan_started_at.isoformat(),
            'cancel_requested': False,
        }, INTEGRITY_CACHE_SECONDS)
        result_cache_keys = []

        def cancel_requested():
            control = cache.get(INTEGRITY_SCAN_CACHE_KEY) or {}
            if control.get('scan_log_id') == scan_log.id and control.get('cancel_requested'):
                return True
            return AuditLog.objects.filter(
                pk=scan_log.id, verification_result='Cancel requested',
            ).exists()

        def finish_cancelled():
            scan_log.note = (
                f'Integrity scan cancelled after checking {checked_versions} version(s) '
                f'across {checked_contracts} contract(s). Existing results were preserved.'
            )
            scan_log.verification_result = 'Cancelled'
            scan_log.integrity_check = 'Cancelled'
            debug_lines.append('Scan cancelled by an administrator. Unchecked PDFs were not marked tampered.')
            scan_log.verification_debug_log = '\n'.join(debug_lines)
            scan_log.save(update_fields=['note', 'verification_result', 'integrity_check', 'verification_debug_log'])
            cache.delete(INTEGRITY_SCAN_CACHE_KEY)
            cache.delete(INTEGRITY_SCAN_LOCK_KEY)
            if result_cache_keys:
                cache.delete_many(result_cache_keys)

        def record_results(versions, chain_by_version, pointer_ok):
            """Persist each version's outcome for the revision-history icons."""
            checked_at = timezone.now()
            latest = versions[-1] if versions else None
            for version in versions:
                result = chain_by_version.get(version.version_number)
                passed = bool(result and result.get('valid'))
                if version is latest and not pointer_ok:
                    passed = False
                ContractVersion.objects.filter(pk=version.pk).update(
                    integrity_status='valid' if passed else 'invalid',
                    integrity_checked_at=checked_at,
                )

        def register_failure(contract, version, reason):
            nonlocal failure_count
            failure_key = (contract.id, version, reason)
            if failure_key in failure_keys:
                return None
            failure_keys.add(failure_key)
            failure_count += 1
            title = contract.title or 'Untitled document'
            if len(failures) < max_failure_details:
                failures.append({'title': title, 'version': version, 'reason': reason})
                debug_lines.append(
                    f'FAILURE: "{title}" — version '
                    f"v{version if version is not None else 'unknown'}: {reason}"
                )
            logger.warning(
                'integrity_scan_failure contract_id=%s version=%s reason=%s',
                contract.id, version, reason,
            )
            recent = AuditLog.objects.filter(
                contract=contract, action='reported_tampering',
                version_number=version, note=reason,
            ).order_by('-timestamp').first()
            if not recent or (time.time() - recent.timestamp.timestamp()) >= 86400:
                AuditLog.objects.create(
                    contract=contract, action='reported_tampering',
                    version_number=version, note=reason,
                    verification_source=options['source'],
                    verification_result='Possible Modification',
                    integrity_check='Failed',
                )
            scan_log.verification_debug_log = '\n'.join(debug_lines)
            scan_log.save(update_fields=['verification_debug_log'])
            return True

        # Stream contracts in bounded batches. Iterating a prefetched QuerySet
        # directly caches every contract and its versions for the full scan.
        contracts = Contract.objects.order_by('id').iterator(chunk_size=50)
        for contract in contracts:
            if cancel_requested():
                finish_cancelled()
                return
            checked_contracts += 1
            versions = list(contract.versions.order_by('version_number'))
            checked_versions += len(versions)
            try:
                chain = verify_version_chain(contract)
            except Exception as error:
                chain = []
                register_failure(
                    contract, None, f'Integrity scan error: {type(error).__name__}'
                )

            chain_by_version = {item['version_number']: item for item in chain}
            result_key = f'{INTEGRITY_RESULT_CACHE_PREFIX}{contract.id}'
            cache.set(result_key, {
                'contract_id': contract.id,
                'valid': all(item.get('valid') for item in chain),
                'checked_at': timezone.now().isoformat(),
            }, INTEGRITY_CACHE_SECONDS)
            result_cache_keys.append(result_key)
            for version in versions:
                result = chain_by_version.get(version.version_number)
                if not result or not result.get('valid'):
                    register_failure(
                        contract, version.version_number,
                        'Version fingerprint or previous-version link failed',
                    )

            latest = versions[-1] if versions else None
            pointer_ok = not (contract.file and latest and contract.file.name != latest.file.name)
            record_results(versions, chain_by_version, pointer_ok)
            if not pointer_ok:
                register_failure(
                    contract, latest.version_number,
                    'Current contract file does not point to its latest version',
                )
            if checked_contracts % 100 == 0:
                scan_log.note = (
                    f'Integrity scan in progress: checked {checked_versions} version(s) '
                    f'across {checked_contracts} contract(s).'
                )
                scan_log.save(update_fields=['note'])
                logger.info(
                    'integrity_scan_progress source=%s contracts=%s versions=%s '
                    'elapsed_ms=%s memory_mb=%s',
                    options['source'], checked_contracts, checked_versions,
                    round((time.perf_counter() - started_at) * 1000),
                    process_memory_mb(),
                )

        catch_up_contracts = Contract.objects.filter(
            versions__created_at__gt=scan_started_at,
        ).distinct().order_by('id').iterator(chunk_size=50)
        catch_up_contract_count = 0
        catch_up_version_count = 0
        max_catch_up_details = 100
        for contract in catch_up_contracts:
            if cancel_requested():
                finish_cancelled()
                return
            catch_up_contract_count += 1
            versions = list(contract.versions.order_by('version_number'))
            catch_up_version_count += len(versions)
            title = contract.title or 'Untitled document'
            new_versions = [
                version for version in versions
                if version.created_at > scan_started_at
            ]
            new_version_numbers = ', '.join(
                f'v{version.version_number}' for version in new_versions
            ) or 'the newly uploaded version'
            if catch_up_contract_count <= max_catch_up_details:
                debug_lines.append(
                    f'Catch-up check: document "{title}" received {new_version_numbers} '
                    'during this scan; rechecking its complete version chain.'
                )
            try:
                chain = verify_version_chain(contract)
            except Exception as error:
                chain = []
                register_failure(
                    contract, None, f'Integrity scan error: {type(error).__name__}'
                )
            chain_by_version = {item['version_number']: item for item in chain}
            for version in versions:
                result = chain_by_version.get(version.version_number)
                if not result or not result.get('valid'):
                    register_failure(
                        contract, version.version_number,
                        'Version fingerprint or previous-version link failed',
                    )
            latest = versions[-1] if versions else None
            pointer_ok = not (contract.file and latest and contract.file.name != latest.file.name)
            record_results(versions, chain_by_version, pointer_ok)
            if not pointer_ok:
                register_failure(
                    contract, latest.version_number,
                    'Current contract file does not point to its latest version',
                )

        if catch_up_contract_count:
            debug_lines.append(
                f'Catch-up check completed for {catch_up_version_count} version(s) '
                f'across {catch_up_contract_count} contract(s) uploaded during the scan.'
            )
            if catch_up_contract_count > max_catch_up_details:
                debug_lines.append(
                    f'Additional catch-up entries omitted: '
                    f'{catch_up_contract_count - max_catch_up_details}.'
                )
        else:
            debug_lines.append('Catch-up check: no versions were uploaded during the scan.')

        duration_ms = round((time.perf_counter() - started_at) * 1000)
        debug_lines.append(
            f'Checked {checked_versions} version(s) across {checked_contracts} contract(s).'
        )
        debug_lines.append(f'Duration: {duration_ms} ms.')
        if failure_count:
            debug_lines.append('Failures:')
            debug_lines.extend(
                f"- \"{failure['title']}\", version "
                f"v{failure['version'] if failure['version'] is not None else 'unknown'}: "
                f"{failure['reason']}"
                for failure in failures
            )
            if failure_count > len(failures):
                debug_lines.append(
                    f'Additional failure details omitted: {failure_count - len(failures)}.'
                )
        else:
            debug_lines.append('Result: all checked files passed integrity validation.')
        summary = (
            f'Integrity scan checked {checked_versions} version(s) across '
            f'{checked_contracts} contract(s) in {duration_ms} ms; '
            f'{failure_count} failure(s).'
        )
        scan_log.note = summary
        scan_log.verification_result = 'Possible Modification' if failure_count else 'Completed'
        scan_log.integrity_check = 'Failed' if failure_count else 'Complete'
        scan_log.verification_debug_log = '\n'.join(debug_lines)
        scan_log.save(update_fields=[
            'note', 'verification_result', 'integrity_check', 'verification_debug_log',
        ])
        logger.info(
            'integrity_scan_completed source=%s contracts=%s versions=%s failures=%s '
            'elapsed_ms=%s memory_mb=%s',
            options['source'], checked_contracts, checked_versions, failure_count,
            duration_ms, process_memory_mb(),
        )
        cache.delete(INTEGRITY_SCAN_CACHE_KEY)
        cache.delete(INTEGRITY_SCAN_LOCK_KEY)
        if result_cache_keys:
            cache.delete_many(result_cache_keys)
        if failure_count:
            self.stderr.write(self.style.ERROR(summary))
        else:
            self.stdout.write(summary if options['quiet_success'] else self.style.SUCCESS(summary))
        close_old_connections()
