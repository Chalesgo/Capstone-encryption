from django.contrib.auth.backends import ModelBackend

from .signals import is_account_locked


class SealGuardModelBackend(ModelBackend):
    """Apply SealGuard account lockout before checking a password."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        submitted_username = username or kwargs.get('email') or ''
        if is_account_locked(submitted_username):
            return None
        return super().authenticate(
            request,
            username=username,
            password=password,
            **kwargs,
        )
