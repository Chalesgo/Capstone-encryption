"""Memory guard and short-lived state for per-document integrity checks."""

from .process_memory import process_memory_mb


# Keep about 112 MiB below Render's 512 MiB service limit. Container overhead
# is higher than the Gunicorn worker RSS, so both readings have separate caps.
INTEGRITY_SCAN_PROCESS_LIMIT_MB = 250
INTEGRITY_SCAN_CONTAINER_LIMIT_MB = 400


class IntegrityMemoryLimitExceeded(Exception):
    """Raised to pause a scan before the service approaches its memory limit."""


def guard_integrity_scan_memory():
    memory = process_memory_mb()
    process_rss = memory.get('vmrss', 0)
    container_memory = memory.get('cgroup_current_mb', 0)
    if process_rss >= INTEGRITY_SCAN_PROCESS_LIMIT_MB:
        raise IntegrityMemoryLimitExceeded(
            f'Integrity check paused: app process is using {process_rss:.1f} MiB '
            f'(limit {INTEGRITY_SCAN_PROCESS_LIMIT_MB} MiB).'
        )
    if container_memory >= INTEGRITY_SCAN_CONTAINER_LIMIT_MB:
        raise IntegrityMemoryLimitExceeded(
            f'Integrity check paused: service container is using {container_memory:.1f} MiB '
            f'(limit {INTEGRITY_SCAN_CONTAINER_LIMIT_MB} MiB).'
        )
    return memory

INTEGRITY_SCAN_CACHE_KEY = 'sealguard:integrity-scan:control'
INTEGRITY_SCAN_LOCK_KEY = 'sealguard:integrity-scan:lock'
INTEGRITY_RESULT_CACHE_PREFIX = 'sealguard:integrity-scan:result:'
INTEGRITY_CACHE_SECONDS = 6 * 60 * 60
