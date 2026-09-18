"""Document authorization shared by HTML, API, and file endpoints."""
from functools import wraps

from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404

from .models import Contract, AuditLog


def is_admin(user):
    """SealGuard administrator: full document access and Django admin access."""
    return user.is_authenticated and user.is_active and user.is_superuser


def is_staff_user(user):
    """SealGuard staff workspace role; deliberately excludes superusers."""
    return user.is_authenticated and user.is_active and user.is_staff and not user.is_superuser


def is_read_only_user(user):
    return user.is_authenticated and user.is_active and not user.is_staff and not user.is_superuser


def managed_contracts(user):
    if user is None or not user.is_authenticated or not user.is_active:
        return Contract.objects.none()
    if is_admin(user):
        return Contract.objects.all()
    return Contract.objects.filter(Q(uploaded_by=user) | Q(collaborators=user)).distinct()


def viewable_contracts(user):
    """Documents visible in the workspace without granting mutation rights."""
    if user is None or not user.is_authenticated or not user.is_active:
        return Contract.objects.none()
    if is_admin(user) or is_staff_user(user):
        return Contract.objects.all()
    return managed_contracts(user)


def can_manage(user, contract):
    return not is_read_only_user(user) and managed_contracts(user).filter(pk=contract.pk).exists()


def can_download(user, contract):
    return not is_read_only_user(user) and can_view(user, contract)


def can_share(user, contract):
    return is_admin(user) or (
        not is_read_only_user(user)
        and user.is_authenticated and user.is_active
        and contract.uploaded_by_id == user.pk
    )


def can_view(user, contract):
    return not contract.is_trashed and (
        contract.is_public or viewable_contracts(user).filter(pk=contract.pk).exists()
    )


def visible_logs(user):
    if is_admin(user):
        return AuditLog.objects.all()
    system_integrity_logs = Q(action='integrity_scan') if is_staff_user(user) else Q()
    return AuditLog.objects.filter(
        system_integrity_logs
        | Q(contract__in=viewable_contracts(user))
        | Q(contract__isnull=True, user=user)
    )


def document_access(*, share=False):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            pk = kwargs.get('contract_id', kwargs.get('pk', args[0] if args else None))
            contract = get_object_or_404(Contract, pk=pk)
            allowed = can_share(request.user, contract) if share else can_manage(request.user, contract)
            if not allowed:
                raise Http404
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def view_document_access(view):
    """Allow read-only document metadata and history without granting edits."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        pk = kwargs.get('contract_id', kwargs.get('pk', args[0] if args else None))
        contract = get_object_or_404(Contract, pk=pk)
        if not can_view(request.user, contract):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


def workspace_access(view):
    """Allow authenticated users into the document workspace; user-role accounts are read-only."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect
            from django.urls import reverse
            return redirect(f"{reverse('login')}?next={request.path}")
        return view(request, *args, **kwargs)
    return wrapped


def workspace_mutation(view):
    """Block the User role from uploads, exports, and workspace mutations."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if is_read_only_user(request.user):
            return JsonResponse({'success': False, 'error': 'Read-only user role.'}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def bulk_document_access(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if is_read_only_user(request.user):
            raise Http404
        try:
            ids = {int(value) for value in request.POST.getlist('ids')}
        except (ValueError, TypeError):
            raise Http404
        if managed_contracts(request.user).filter(pk__in=ids).count() != len(ids):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped
