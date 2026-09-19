import io
import secrets
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password, check_password
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .access import can_share, can_view, is_admin, is_staff_user
from .emailing import send_sealguard_mail
from .models import Contract, DocumentAccessLink, DocumentAccessRequest
from .pdf_storage import pdf_storage, PDFDecryptionError
from .utils import log_activity

# Kept as a module-level seam for existing tests and deployments that replace
# the mail sender during a demo.
send_mail = send_sealguard_mail


class RequestForm(forms.Form):
    name = forms.CharField(label='Full name', max_length=120)
    email = forms.EmailField(label='Email address', max_length=254)
    organization = forms.CharField(label='Organization (optional)', max_length=160, required=False)
    reason = forms.CharField(label='Why do you need access?', max_length=1000, widget=forms.Textarea(attrs={'rows': 3}))


def _session_request(request, link):
    entry_id = request.session.get('document_access_requests', {}).get(str(link.pk))
    if not entry_id:
        return None
    return DocumentAccessRequest.objects.filter(pk=entry_id, link=link).first()


def _verified_in_session(request, entry):
    return str(entry.pk) in request.session.get('verified_document_requests', [])


def _approved(entry):
    return bool(entry and entry.status == 'approved' and entry.email_verified_at
                and entry.expires_at and entry.expires_at > timezone.now()
                and entry.approved_file and not entry.link.contract.is_trashed)


@never_cache
def request_access(request, token):
    link = get_object_or_404(DocumentAccessLink.objects.select_related('contract'), token=token, contract__is_trashed=False)
    if request.method not in ('GET', 'POST'):
        raise Http404
    entry = _session_request(request, link)
    if not link.is_active and not entry:
        response = render(request, 'access/obsolete.html', {'link': link})
        response['Referrer-Policy'] = 'no-referrer'
        return response
    form = RequestForm()
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'restart':
            mapping = request.session.get('document_access_requests', {}).copy()
            mapping.pop(str(link.pk), None)
            request.session['document_access_requests'] = mapping
            return redirect('request_document_access', token=token)
        if action == 'verify' and entry and entry.status == 'email_pending':
            with transaction.atomic():
                entry = DocumentAccessRequest.objects.select_for_update().get(pk=entry.pk)
                if entry.otp_attempts >= 5 or entry.otp_expires_at <= timezone.now():
                    error = 'This code has expired or has too many attempts. Start a new request.'
                elif entry.status != 'email_pending':
                    error = 'This code has already been used.'
                else:
                    entry.otp_attempts += 1
                    if check_password(request.POST.get('code', '')[:12], entry.otp_hash):
                        entry.status = 'pending'
                        entry.email_verified_at = timezone.now()
                        entry.otp_hash = ''
                        request.session.cycle_key()
                        verified = request.session.get('verified_document_requests', [])
                        request.session['verified_document_requests'] = (verified + [str(entry.pk)])[-20:]
                        log_activity(request, 'edited', contract=link.contract,
                                     note=f'PDF access requested; request={entry.pk}; email verified')
                    else:
                        error = 'The code is incorrect. Please try again.'
                    entry.save(update_fields=['otp_attempts', 'status', 'email_verified_at', 'otp_hash'])
            if not error:
                return redirect('request_document_access', token=token)
        elif action == 'request':
            form = RequestForm(request.POST)
            if not link.is_active:
                error = 'This QR code has already been used. Please request a fresh access sheet.'
            elif form.is_valid():
                now = timezone.now()
                email = form.cleaned_data['email'].lower()
                ip_hash = salted_hmac('pdf-request-ip', request.META.get('REMOTE_ADDR', '')).hexdigest()
                recent = DocumentAccessRequest.objects.filter(created_at__gt=now - timedelta(minutes=15))
                if recent.filter(Q(ip_hash=ip_hash) | Q(email__iexact=email)).count() >= 5:
                    error = 'Too many requests. Please wait 15 minutes before trying again.'
                else:
                    code = f'{secrets.randbelow(1000000):06d}'
                    entry = DocumentAccessRequest.objects.create(
                        link=link, name=form.cleaned_data['name'], email=email,
                        organization=form.cleaned_data['organization'], reason=form.cleaned_data['reason'],
                        otp_hash=make_password(code), otp_expires_at=now + timedelta(minutes=10), ip_hash=ip_hash,
                    )
                    try:
                        delivered = send_mail('SealGuard document access: verify your email',
                            f'Your verification code is {code}. It expires in 10 minutes. '
                            'This verifies your email only; staff approval is still required. '
                            'If you did not request access, ignore this message.', [email], fail_silently=False)
                        if not delivered:
                            raise RuntimeError('Email was not delivered')
                    except Exception:
                        error = 'We could not send the verification email. Please contact staff or try again later.'
                        entry = None
                    else:
                        with transaction.atomic():
                            current_link = DocumentAccessLink.objects.select_for_update().get(pk=link.pk)
                            if not current_link.is_active:
                                error = 'This QR code has already been used. Please request a fresh access sheet.'
                                entry.delete()
                                entry = None
                            else:
                                current_link.is_active = False
                                current_link.superseded_at = timezone.now()
                                current_link.save(update_fields=['is_active', 'superseded_at'])
                                DocumentAccessLink.objects.create(contract=current_link.contract)
                                mapping = request.session.get('document_access_requests', {}).copy()
                                mapping[str(current_link.pk)] = str(entry.pk)
                                request.session['document_access_requests'] = dict(list(mapping.items())[-20:])
                        if error:
                            pass
                        else:
                            return redirect('request_document_access', token=token)
        elif action != 'verify':
            error = 'Choose a valid action.'
    own_access = can_view(request.user, link.contract)
    response = render(request, 'access/request.html', {
        'link': link, 'entry': entry, 'form': form, 'error': error,
        'can_open': _approved(entry) and _verified_in_session(request, entry) if entry else False,
        'own_access': own_access,
        'email_delivery_local': settings.EMAIL_BACKEND.endswith('console.EmailBackend'),
    })
    response['Referrer-Policy'] = 'no-referrer'
    return response


@never_cache
def approved_pdf(request, token):
    link = get_object_or_404(DocumentAccessLink.objects.select_related('contract'), token=token, contract__is_trashed=False)
    entry = _session_request(request, link)
    if not _approved(entry) or not _verified_in_session(request, entry):
        raise Http404
    # Grants cover the approved version only, never future revisions or editing.
    if entry.approved_version_id and entry.approved_version.contract_id != link.contract_id:
        raise Http404
    if request.GET.get('download') == 'encrypted':
        from .encrypted_documents import package_response
        version = entry.approved_version
        file = version.file if version else link.contract.file
        if file.name != entry.approved_file:
            raise Http404
        log_activity(request, 'downloaded', contract=link.contract,
                     note=f'Downloaded encrypted approved document; request={entry.pk}')
        return package_response(link.contract, file, 'approved-document.sgpdf', version)
    try:
        document = pdf_storage.open(entry.approved_file, 'rb')
    except (OSError, PDFDecryptionError):
        raise Http404
    log_activity(request, 'viewed', contract=link.contract,
                 version_number=entry.approved_version.version_number if entry.approved_version_id else None,
                 note=f'Viewed with approved PDF access; request={entry.pk}')
    response = FileResponse(document, content_type='application/pdf', filename='approved-document.pdf')
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _require_reviewer(request):
    if not (is_admin(request.user) or is_staff_user(request.user)):
        raise Http404


@login_required
@never_cache
def approval_queue(request):
    _require_reviewer(request)
    from django.core.paginator import Paginator
    status = request.GET.get('status', 'pending')
    if status not in {'pending', 'approved', 'rejected', 'revoked'}:
        status = 'pending'
    entries = DocumentAccessRequest.objects.filter(status=status).select_related('link__contract', 'reviewed_by', 'approved_version')
    if request.GET.get('request_id'):
        from uuid import UUID
        try:
            entry_id = UUID(request.GET['request_id'])
        except ValueError:
            raise Http404
        entry = get_object_or_404(DocumentAccessRequest, pk=entry_id)
        status = entry.status
        entries = DocumentAccessRequest.objects.filter(pk=entry.pk).select_related('link__contract', 'reviewed_by', 'approved_version')
    return render(request, 'access/queue.html', {'entries': Paginator(entries, 25).get_page(request.GET.get('page')), 'status': status})


@login_required
@require_POST
def review_request(request, request_id):
    _require_reviewer(request)
    with transaction.atomic():
        entry = get_object_or_404(DocumentAccessRequest.objects.select_for_update().select_related('link__contract'), pk=request_id)
        action = request.POST.get('action')
        contract = entry.link.contract
        if not entry.email_verified_at or contract.is_trashed:
            raise Http404
        if action in ('approve', 'reject') and entry.status == 'pending':
            if action == 'approve':
                try:
                    hours = int(request.POST.get('hours', '24'))
                except ValueError:
                    raise Http404
                if hours not in (1, 24, 72):
                    raise Http404
                version = contract.versions.order_by('-version_number').first()
                if not contract.file or (version and not version.file):
                    raise Http404
                entry.approved_version = version
                entry.approved_file = version.file.name if version else contract.file.name
                entry.expires_at = timezone.now() + timedelta(hours=hours)
                entry.status = 'approved'
            else:
                entry.status = 'rejected'
        elif action == 'revoke' and entry.status == 'approved':
            entry.status = 'revoked'
            entry.expires_at = timezone.now()
        else:
            raise Http404
        entry.reviewed_by = request.user
        entry.reviewed_at = timezone.now()
        entry.review_note = request.POST.get('note', '')[:500]
        entry.save()
        log_activity(request, 'edited', contract=contract,
                     note=f'PDF access {entry.status}; request={entry.pk}; expires={entry.expires_at or "none"}')
    return redirect('document_approval_queue')


@login_required
@never_cache
def access_sheet(request, contract_id):
    contract = get_object_or_404(Contract, pk=contract_id, is_trashed=False)
    if not can_share(request.user, contract):
        raise Http404
    link = DocumentAccessLink.objects.filter(contract=contract, is_active=True).order_by('-created_at').first()
    if link is None:
        link = DocumentAccessLink.objects.create(contract=contract)
    path = reverse('request_document_access', args=[link.token])
    base = getattr(settings, 'PUBLIC_BASE_URL', '').rstrip('/')
    url = base + path if base else request.build_absolute_uri(path)
    from .qr_sheet import make_access_sheet
    response = FileResponse(io.BytesIO(make_access_sheet(url, str(link.token)[:8])),
                            content_type='application/pdf', as_attachment=True, filename='SealGuard-access-request.pdf')
    log_activity(request, 'downloaded', contract=contract, note='Downloaded QR access sheet (no document contents)')
    return response
