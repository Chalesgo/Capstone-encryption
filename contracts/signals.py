import hashlib
import time

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
from django.db.backends.signals import connection_created
from django.contrib.auth.models import User
from django.core.cache import cache
from django.dispatch import receiver


SEALGUARD_ROLE_GROUPS = ('SealGuard Admin', 'SealGuard Staff', 'SealGuard User')


def sync_sealguard_role_group(instance):
    """Mirror the account role flags into named groups for administrator clarity."""
    from django.contrib.auth.models import Group
    groups = {group.name: group for group in Group.objects.filter(name__in=SEALGUARD_ROLE_GROUPS)}
    role_name = 'SealGuard Admin' if instance.is_superuser else ('SealGuard Staff' if instance.is_staff else 'SealGuard User')
    role_group = groups.get(role_name)
    if role_group is None:
        return
    role_permissions = []
    for group in groups.values():
        role_permissions.extend(group.permissions.all())
    if role_permissions and instance.pk:
        # Role permissions are managed through Groups, never left as stale
        # direct User permissions after a role change.
        instance.user_permissions.remove(*role_permissions)
    instance.groups.remove(*[group for name, group in groups.items() if name != role_name])
    instance.groups.add(role_group)


@receiver(post_save, sender=User)
def sync_sealguard_role_group_on_save(sender, instance, **kwargs):
    sync_sealguard_role_group(instance)


@receiver(connection_created)
def configure_sqlite_connection(sender, connection, **kwargs):
    """Make local SQLite tolerate concurrent web/admin activity."""
    if connection.vendor != 'sqlite':
        return
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA busy_timeout=120000')
        cursor.execute('PRAGMA synchronous=NORMAL')
        cursor.execute('PRAGMA journal_mode=WAL')

from .models import AuditLog


def _client_ip(request):
    if not request:
        return None
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _username_key(username):
    return str(username or '').strip().casefold()


def _attempt_key(username):
    return f'sealguard:login-attempts:{_username_digest(username)}'


def _lockout_key(username):
    return f'sealguard:login-lockout:{_username_digest(username)}'


def _lockout_until_key(username):
    return f'sealguard:login-lockout-until:{_username_digest(username)}'


def _username_digest(username):
    return hashlib.sha256(_username_key(username).encode('utf-8')).hexdigest()


def is_account_locked(username):
    return bool(_username_key(username) and cache.get(_lockout_key(username), False))


def lockout_remaining_seconds(username):
    if not is_account_locked(username):
        return 0
    return max(0, int(cache.get(_lockout_until_key(username), 0) - time.time() + 0.999))


def failed_login_count(username):
    if not _username_key(username):
        return 0
    return int(cache.get(_attempt_key(username), 0) or 0)


def clear_login_failures(username):
    if _username_key(username):
        cache.delete(_attempt_key(username))
        cache.delete(_lockout_key(username))
        cache.delete(_lockout_until_key(username))


def record_lockout(request, username):
    user = User.objects.filter(username__iexact=username).first()
    AuditLog.objects.create(
        user=user,
        action='locked_out',
        ip_address=_client_ip(request),
        note=f'Account locked for {settings.LOGIN_LOCKOUT_SECONDS} seconds after {settings.LOGIN_FAILURE_THRESHOLD} failed attempts',
    )


def record_failed_login(request, username=''):
    if request is not None:
        request._sealguard_login_failed_recorded = True
    AuditLog.objects.create(
        action='failed_login',
        ip_address=_client_ip(request),
        note=f'Failed authentication attempt for username: {username[:150]}',
    )
    normalized_username = _username_key(username)
    if normalized_username and User.objects.filter(username__iexact=username).exists():
        if is_account_locked(username):
            return
        attempt_key = _attempt_key(username)
        failures = (cache.get(attempt_key, 0) or 0) + 1
        cache.set(attempt_key, failures, settings.LOGIN_LOCKOUT_SECONDS)
        if failures >= settings.LOGIN_FAILURE_THRESHOLD:
            cache.set(_lockout_key(username), True, settings.LOGIN_LOCKOUT_SECONDS)
            cache.set(
                _lockout_until_key(username),
                time.time() + settings.LOGIN_LOCKOUT_SECONDS,
                settings.LOGIN_LOCKOUT_SECONDS,
            )
            if failures == settings.LOGIN_FAILURE_THRESHOLD:
                record_lockout(request, username)


@receiver(user_logged_in)
def record_successful_login(sender, request, user, **kwargs):
    clear_login_failures(user.username)
    AuditLog.objects.create(
        user=user,
        action='login',
        ip_address=_client_ip(request),
        note='Successful authentication',
    )


@receiver(user_logged_out)
def record_logout(sender, request, user, **kwargs):
    AuditLog.objects.create(
        user=user,
        action='logout',
        ip_address=_client_ip(request),
        note='User logged out',
    )


@receiver(user_login_failed)
def record_failed_login_signal(sender, credentials, request, **kwargs):
    username = credentials.get('username') or credentials.get('email') or ''
    record_failed_login(request, username)
