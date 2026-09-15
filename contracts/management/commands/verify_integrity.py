import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from contracts.models import AuditLog, Contract
from contracts.utils import verify_version_chain


logger = logging.getLogger('contracts.integrity')


class Command(BaseCommand):
    help = 'Check every stored contract version and report integrity failures.'

    def add_arguments(self, parser):
        parser.add_argument('--quiet-success', action='store_true')

    def handle(self, *args, **options):
        started_at = time.perf_counter()
        scan_started_at = timezone.now()
        close_old_connections()
        checked_contracts = 0
        checked_versions = 0
        failures = []
        failure_keys = set()
        debug_lines = ['Integrity scan started.']
        scan_log = AuditLog.objects.create(
            action='integrity_scan',
            document_title='Integrity Scan',
            note='Integrity scan is currently running.',
            verification_source='Scheduled Integrity Scan',
            verification_result='Running',
            integrity_check='In Progress',
            verification_debug_log='\n'.join(debug_lines),
        )

        def register_failure(contract, version, reason):
            failure_key = (contract.id, version, reason)
            if failure_key in failure_keys:
                return None
            failure_keys.add(failure_key)
            failure = {'contract': contract, 'version': version, 'reason': reason}
            failures.append(failure)
            title = contract.title or 'Untitled document'
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
                    verification_source='Scheduled Integrity Scan',
                    verification_result='Possible Modification',
                    integrity_check='Failed',
                )
            scan_log.verification_debug_log = '\n'.join(debug_lines)
            scan_log.save(update_fields=['verification_debug_log'])
            return failure

        for contract in Contract.objects.prefetch_related('versions').order_by('id'):
            checked_contracts += 1
            versions = list(contract.versions.order_by('version_number'))
            checked_versions += len(versions)
            title = contract.title or 'Untitled document'
            if versions:
                version_numbers = [version.version_number for version in versions]
                if len(version_numbers) == 1:
                    version_history = f'v{version_numbers[0]}'
                else:
                    version_history = (
                        f'v{version_numbers[0]}–v{version_numbers[-1]} '
                        f'({len(version_numbers)} stored versions)'
                    )
            else:
                version_history = 'no stored versions'
            debug_lines.append(
                f'Document "{title}": checking {version_history} by recomputing '
                'canonical PDF fingerprints, validating previous-fingerprint links, '
                'and validating recorded vector fingerprints.'
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
            if contract.file and latest and contract.file.name != latest.file.name:
                debug_lines.append(
                    f'Document "{title}": validating the current document file '
                    f'pointer against latest version v{latest.version_number}.'
                )
                register_failure(
                    contract, latest.version_number,
                    'Current contract file does not point to its latest version',
                )
            elif latest:
                debug_lines.append(
                    f'Document "{title}": current document file pointer matches '
                    f'latest version v{latest.version_number}.'
                )
            scan_log.note = (
                f'Integrity scan in progress: checked {checked_versions} version(s) '
                f'across {checked_contracts} contract(s).'
            )
            scan_log.verification_debug_log = '\n'.join(debug_lines)
            scan_log.save(update_fields=['note', 'verification_debug_log'])

        catch_up_contracts = Contract.objects.filter(
            versions__created_at__gt=scan_started_at,
        ).prefetch_related('versions').distinct().order_by('id')
        catch_up_contract_count = 0
        catch_up_version_count = 0
        for contract in catch_up_contracts:
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
            if contract.file and latest and contract.file.name != latest.file.name:
                register_failure(
                    contract, latest.version_number,
                    'Current contract file does not point to its latest version',
                )

        if catch_up_contract_count:
            debug_lines.append(
                f'Catch-up check completed for {catch_up_version_count} version(s) '
                f'across {catch_up_contract_count} contract(s) uploaded during the scan.'
            )
        else:
            debug_lines.append('Catch-up check: no versions were uploaded during the scan.')

        duration_ms = round((time.perf_counter() - started_at) * 1000)
        debug_lines.append(
            f'Checked {checked_versions} version(s) across {checked_contracts} contract(s).'
        )
        debug_lines.append(f'Duration: {duration_ms} ms.')
        if failures:
            debug_lines.append('Failures:')
            debug_lines.extend(
                f"- \"{failure['contract'].title or 'Untitled document'}\", version "
                f"v{failure['version'] if failure['version'] is not None else 'unknown'}: "
                f"{failure['reason']}"
                for failure in failures
            )
        else:
            debug_lines.append('Result: all checked files passed integrity validation.')
        summary = (
            f'Integrity scan checked {checked_versions} version(s) across '
            f'{checked_contracts} contract(s) in {duration_ms} ms; '
            f'{len(failures)} failure(s).'
        )
        scan_log.note = summary
        scan_log.verification_result = 'Possible Modification' if failures else 'Completed'
        scan_log.integrity_check = 'Failed' if failures else 'Complete'
        scan_log.verification_debug_log = '\n'.join(debug_lines)
        scan_log.save(update_fields=[
            'note', 'verification_result', 'integrity_check', 'verification_debug_log',
        ])
        if failures:
            self.stderr.write(self.style.ERROR(summary))
        else:
            self.stdout.write(summary if options['quiet_success'] else self.style.SUCCESS(summary))
        close_old_connections()
