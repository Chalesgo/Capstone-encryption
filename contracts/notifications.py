from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .access import is_admin, is_staff_user
from .models import DocumentAccessRequest, PasswordResetRequest


@login_required
@require_GET
@never_cache
def notification_list(request):
    """Pending review work, scoped to the same roles as the review pages."""
    if not (is_admin(request.user) or is_staff_user(request.user)):
        return JsonResponse({'count': 0, 'notifications': []})
    documents = DocumentAccessRequest.objects.filter(
        status='pending', email_verified_at__isnull=False,
        link__contract__is_trashed=False,
    ).select_related('link__contract').order_by('-created_at')
    count = documents.count()
    items = [{
        'id': f'document-{entry.pk}',
        'title': 'PDF access request',
        'message': f'{entry.name} ({entry.email}) requested access to {entry.link.contract.title}.',
        'created_at': entry.created_at.isoformat(),
        'url': reverse('document_approval_queue') + f'?request_id={entry.pk}',
    } for entry in documents[:50]]
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
    items.sort(key=lambda item: item['created_at'], reverse=True)
    return JsonResponse({'count': count, 'notifications': items[:50]})
