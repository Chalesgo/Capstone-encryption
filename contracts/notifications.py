from django.contrib.auth.decorators import login_required
import json
from django.http import JsonResponse
from datetime import timedelta

from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from urllib.parse import urlencode

from .access import is_admin, is_staff_user
from .models import Contract, DocumentAccessRequest, NotificationClearState, PasswordResetRequest, StaffInvitation


@login_required
@require_http_methods(['GET', 'POST'])
@never_cache
def notification_list(request):
    """Pending review work, scoped to the same roles as the review pages."""
    if not (is_admin(request.user) or is_staff_user(request.user)):
        return JsonResponse({'count': 0, 'notifications': []})
    now = timezone.now()
    if request.method == 'POST':
        try:
            payload = json.loads(request.body or '{}')
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)
        if payload.get('action') != 'clear_all':
            return JsonResponse({'error': 'Unsupported notification action.'}, status=400)
        NotificationClearState.objects.update_or_create(
            user=request.user, defaults={'cleared_at': now},
        )
        return JsonResponse({'count': 0, 'notifications': []})
    clear_state = NotificationClearState.objects.filter(user=request.user).first()
    cleared_at = clear_state.cleared_at if clear_state else None
    documents = DocumentAccessRequest.objects.filter(
        status='pending',
        link__contract__is_trashed=False,
    ).select_related('link__contract').order_by('-created_at')
    count = documents.count()
    items = [{
        'id': f'document-{entry.pk}',
        'title': 'Aging PDF access request' if entry.created_at <= now - timedelta(hours=24) else 'PDF access request',
        'message': (
            f'{entry.name} ({entry.email}) requested access to {entry.link.contract.title}. '
            'This request has been waiting over 24 hours.'
            if entry.created_at <= now - timedelta(hours=24)
            else f'{entry.name} ({entry.email}) requested access to {entry.link.contract.title}.'
        ),
        'created_at': entry.created_at.isoformat(),
        'url': reverse('document_approval_queue') + f'?request_id={entry.pk}',
    } for entry in documents[:50]]
    expiring_access = DocumentAccessRequest.objects.filter(
        status='approved',
        expires_at__gt=now,
        expires_at__lte=now + timedelta(hours=1),
        link__contract__is_trashed=False,
    ).select_related('link__contract').order_by('expires_at')
    count += expiring_access.count()
    items.extend({
        'id': f'access-expiring-{entry.pk}',
        'title': 'Temporary access expiring soon',
        'message': f'Access for {entry.name} to {entry.link.contract.title} expires at {timezone.localtime(entry.expires_at).strftime("%I:%M %p")}.',
        'created_at': entry.created_at.isoformat(),
        'url': reverse('document_approval_queue') + f'?request_id={entry.pk}',
    } for entry in expiring_access[:50])
    if is_admin(request.user):
        resets = PasswordResetRequest.objects.filter(status='pending').select_related('user').order_by('-created_at')
        count += resets.count()
        items.extend({
            'id': f'password-{entry.pk}',
            'title': 'Password reset request',
            'message': f'{entry.user.username} ({entry.email}) requested a password reset.',
            'created_at': entry.created_at.isoformat(),
            'url': reverse('admin:contracts_passwordresetrequest_change', args=[entry.pk]),
        } for entry in resets[:50])
        invitations = StaffInvitation.objects.filter(
            used_at__isnull=True,
            expires_at__gt=now,
            expires_at__lte=now + timedelta(hours=24),
        ).order_by('expires_at')
        count += invitations.count()
        items.extend({
            'id': f'invitation-{entry.pk}',
            'title': 'Staff invitation expiring soon',
            'message': f'An unused staff invitation expires at {timezone.localtime(entry.expires_at).strftime("%b %d, %I:%M %p")}.',
            'created_at': entry.created_at.isoformat(),
            'url': reverse('admin:contracts_staffinvitation_changelist'),
        } for entry in invitations[:50])
        stale_cutoff = now - timedelta(days=7)
        stale_drafts = Contract.objects.filter(
            status='pending',
            is_trashed=False,
            modified_at__lt=stale_cutoff,
        ).order_by('modified_at')
        count += stale_drafts.count()
        items.extend({
            'id': f'draft-{entry.pk}',
            'title': 'Draft document needs review',
            'message': f'{entry.title} has remained a Draft for more than 7 days.',
            'created_at': entry.modified_at.isoformat(),
            'url': reverse('contract_list') + '?' + urlencode({'q': entry.title, 'status': 'pending'}),
        } for entry in stale_drafts[:50])
    items.sort(key=lambda item: item['created_at'], reverse=True)
    if cleared_at:
        items = [
            item for item in items
            if (item_time := parse_datetime(item['created_at'])) and item_time > cleared_at
        ]
    count = len(items)
    if request.GET.get('summary') == '1':
        return JsonResponse({'count': count})
    return JsonResponse({'count': count, 'notifications': items[:50]})
