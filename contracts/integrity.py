"""Memory guard and short-lived state for per-document integrity checks."""

from .process_memory import process_memory_mb


# Keep at least 212 MiB of headroom below Render's 512 MiB service limit.
INTEGRITY_SCAN_MEMORY_LIMIT_MB = 300


class IntegrityMemoryLimitExceeded(Exception):
    """Raised to pause a scan before the service approaches its memory limit."""


def guard_integrity_scan_memory():
    memory = process_memory_mb()
    observed = max(memory.get('vmrss', 0), memory.get('cgroup_current_mb', 0))
    if observed and observed >= INTEGRITY_SCAN_MEMORY_LIMIT_MB:
        raise IntegrityMemoryLimitExceeded(
            f'Integrity check paused at {observed:.1f} MiB; '
            f'configured ceiling is {INTEGRITY_SCAN_MEMORY_LIMIT_MB} MiB.'
        )
    return observed

INTEGRITY_SCAN_CACHE_KEY = 'sealguard:integrity-scan:control'
INTEGRITY_SCAN_LOCK_KEY = 'sealguard:integrity-scan:lock'
INTEGRITY_RESULT_CACHE_PREFIX = 'sealguard:integrity-scan:result:'
INTEGRITY_CACHE_SECONDS = 6 * 60 * 60
