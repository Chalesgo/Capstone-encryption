from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditLog


def _client_ip(request):
    if not request:
        return None
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def record_failed_login(request, username=''):
    if request is not None:
        request._sealguard_login_failed_recorded = True
    AuditLog.objects.create(
        action='failed_login',
        ip_address=_client_ip(request),
        note=f'Failed authentication attempt for username: {username[:150]}',
    )


@receiver(user_logged_in)
def record_successful_login(sender, request, user, **kwargs):
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
