from .access import is_resident


def sealguard_roles(request):
    """Let templates hide the staff workspace from resident accounts."""
    user = getattr(request, 'user', None)
    resident = bool(user and is_resident(user))
    return {
        'is_resident': resident,
        'workspace_user': bool(user and user.is_authenticated and not resident),
    }
