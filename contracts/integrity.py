"""Short-lived demo coordination state for the integrity scanner."""

INTEGRITY_SCAN_CACHE_KEY = 'sealguard:integrity-scan:control'
INTEGRITY_SCAN_LOCK_KEY = 'sealguard:integrity-scan:lock'
INTEGRITY_RESULT_CACHE_PREFIX = 'sealguard:integrity-scan:result:'
INTEGRITY_CACHE_SECONDS = 6 * 60 * 60
