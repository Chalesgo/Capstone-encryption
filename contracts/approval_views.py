import io
import hashlib
from pathlib import Path
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction, OperationalError
from django.db.models import Q, F
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .access import can_share, is_admin, is_staff_user
from .emailing import send_sealguard_mail
from .models import Contract, DocumentAccessLink, DocumentAccessRequest
from .forms import PhoneField
from .pdf_storage import pdf_storage, PDFDecryptionError
from .utils import log_activity, get_client_ip

# Kept as a module-level seam for existing tests and deployments that replace
# the mail sender during a demo.
send_mail = send_sealguard_mail


class RequestForm(forms.Form):
    name = forms.CharField(label='Full name', max_length=120)
    contact_channel = forms.ChoiceField(choices=[('email', 'Email'), ('sms', 'SMS')], label='Preferred contact method', required=False)
    email = forms.EmailField(label='Email address (for email)', max_length=254, required=False)
    phone_number = PhoneField(label='Mobile number (for SMS)', required=False)

    def clean(self):
        data = super().clean()
        field = 'phone_number' if data.get('contact_channel') == 'sms' else 'email'
        if not data.get(field):
            self.add_error(field, 'Enter a contact for your selected method.')
        return data

    organization = forms.CharField(label='Organization (optional)', max_length=160, required=False)
    reason = forms.CharField(label='Why do you need access?', max_length=1000, widget=forms.Textarea(attrs={'rows': 3}))


def _session_request(request, link):
    entry_id = request.session.get('document_access_requests', {}).get(str(link.pk))
    if not entry_id:
        return None
    return DocumentAccessRequest.objects.filter(pk=entry_id, link=link).first()


def _verified_in_session(request, entry):
    return request.session.get('document_access_requests', {}).get(str(entry.link_id)) == str(entry.pk)


def _approved(entry):
    return bool(entry and entry.status == 'approved'
                and entry.expires_at and entry.expires_at > timezone.now()
                and entry.approved_file and not entry.link.contract.is_trashed)


def _requested_file(link):
    """Resolve and verify the exact ciphertext named in the uploaded package."""
    if link.category != 'sgpdf':
        return link.contract.file
    file = link.requested_version.file if link.requested_version_id else link.contract.file
    if (link.requested_version_id and link.requested_version.contract_id != link.contract_id
            or not file or file.name != link.requested_file):
        raise Http404
    try:
        if hashlib.sha256(Path(file.path).read_bytes()).hexdigest() != link.ciphertext_sha256:
            raise Http404
    except OSError:
        raise Http404
    return file


@never_cache
def request_access(request, token):
    link = get_object_or_404(DocumentAccessLink.objects.select_related('contract'), token=token, contract__is_trashed=False)
    if request.method not in ('GET', 'POST'):
        raise Http404
    entry = _session_request(request, link)
    if not link.is_active and not entry and not link.contract.is_public:
        response = render(request, 'access/obsolete.html', {'link': link})
        response['Referrer-Policy'] = 'no-referrer'
        return response
    log_activity(request, 'viewed', contract=link.contract,
                 note=f'PDF request page/status viewed; link={link.pk}; request={entry.pk if entry else "not submitted"}')
    form = RequestForm()
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'restart':
            log_activity(request, 'edited', contract=link.contract,
                         note=f'PDF request restarted; link={link.pk}; request={entry.pk if entry else "none"}')
            if link.category == 'sgpdf':
                return redirect('open_encrypted_document')
            mapping = request.session.get('document_access_requests', {}).copy()
            mapping.pop(str(link.pk), None)
            request.session['document_access_requests'] = mapping
            return redirect('request_document_access', token=token)
        if action == 'submit_existing' and entry and entry.status == 'email_pending':
            DocumentAccessRequest.objects.filter(pk=entry.pk, status='email_pending').update(
                status='pending', otp_hash='',
            )
            log_activity(request, 'edited', contract=link.contract,
                         note=f'Existing PDF request submitted for review; request={entry.pk}')
            return redirect('request_document_access', token=token)
        elif action == 'request':
            form = RequestForm(request.POST)
            if not link.is_active:
                error = 'This QR code has already been used. Please request a fresh access sheet.'
            elif form.is_valid():
                _requested_file(link)
                now = timezone.now()
                email = form.cleaned_data['email'].lower()
                ip_hash = salted_hmac('pdf-request-ip', request.META.get('REMOTE_ADDR', '')).hexdigest()
                recent = DocumentAccessRequest.objects.filter(created_at__gt=now - timedelta(minutes=15))
                contact_filter = Q(phone_number=form.cleaned_data['phone_number']) if form.cleaned_data.get('contact_channel') == 'sms' else Q(email__iexact=email)
                if recent.filter(Q(ip_hash=ip_hash) | contact_filter).count() >= 5:
                    error = 'Too many requests. Please wait 15 minutes before trying again.'
                else:
                    try:
                        # Claim the single-use link as the first transaction write.
                        # This avoids SQLite's read-to-write lock upgrade.
                        with transaction.atomic():
                            claimed = DocumentAccessLink.objects.filter(pk=link.pk, is_active=True).update(
                                is_active=False, superseded_at=now,
                            )
                            if claimed:
                                entry = DocumentAccessRequest.objects.create(
                                    link=link, name=form.cleaned_data['name'], email=email,
                                    phone_number=form.cleaned_data['phone_number'], contact_channel=form.cleaned_data.get('contact_channel') or 'email',
                                    organization=form.cleaned_data['organization'], reason=form.cleaned_data['reason'],
                                    status='pending', otp_hash='', otp_expires_at=now, ip_hash=ip_hash,
                                    ip_address=get_client_ip(request),
                                )
                                if link.category == 'pdf':
                                    DocumentAccessLink.objects.create(contract=link.contract)
                                log_activity(request, 'edited', contract=link.contract,
                                             note=f'{link.get_category_display()} submitted; request={entry.pk}; awaiting staff approval')
                        if not claimed:
                            error = 'This QR code has already been used. Please request a fresh access sheet.'
                        else:
                            mapping = request.session.get('document_access_requests', {}).copy()
                            mapping[str(link.pk)] = str(entry.pk)
                            request.session['document_access_requests'] = dict(list(mapping.items())[-20:])
                            return redirect('request_document_access', token=token)
                    except OperationalError as exc:
                        if 'locked' not in str(exc).lower():
                            raise
                        entry = None
                        error = 'The system is busy. Please submit your request again shortly.'
        else:
            error = 'Choose a valid action.'
    if request.method == 'POST':
        log_activity(request, 'verification', contract=link.contract,
                     note=f'PDF request submission not accepted; link={link.pk}; invalid details or request limit')
    response = render(request, 'access/request.html', {
        'link': link, 'entry': entry, 'form': form, 'error': error,
        'public_access': link.contract.is_public and link.category != 'sgpdf',
        'can_open': _approved(entry) and _verified_in_session(request, entry) if entry else False,
    })
    response['Referrer-Policy'] = 'no-referrer'
    return response


@never_cache
def approved_pdf(request, token):
    link = get_object_or_404(DocumentAccessLink.objects.select_related('contract'), token=token, contract__is_trashed=False)
    entry = _session_request(request, link)
    if not _approved(entry) or not _verified_in_session(request, entry):
        log_activity(request, 'verification', contract=link.contract,
                     note=f'Unauthorized approved PDF view denied; link={link.pk}')
        raise Http404
    if link.category == 'sgpdf':
        _requested_file(link)
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
        # Acquire the SQLite write lock before reading the review state.
        DocumentAccessRequest.objects.filter(pk=request_id).update(status=F('status'))
        entry = get_object_or_404(DocumentAccessRequest.objects.select_for_update().select_related('link__contract'), pk=request_id)
        action = request.POST.get('action')
        contract = entry.link.contract
        if contract.is_trashed:
            raise Http404
        if action in ('approve', 'reject') and entry.status == 'pending':
            if action == 'approve':
                try:
                    hours = int(request.POST.get('hours', '24'))
                except ValueError:
                    raise Http404
                if hours not in (1, 24, 72):
                    raise Http404
                if entry.link.category == 'sgpdf':
                    _requested_file(entry.link)
                    version = entry.link.requested_version
                else:
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
        log_activity(request, {'approved': 'approved', 'rejected': 'rejected'}.get(entry.status, 'edited'), contract=contract,
                     note=f'PDF access {entry.status}; request={entry.pk}; expires={entry.expires_at or "none"}')
    return redirect('document_approval_queue')


@login_required
@never_cache
def access_sheet(request, contract_id):
    contract = get_object_or_404(Contract, pk=contract_id, is_trashed=False)
    if not can_share(request.user, contract):
        raise Http404
    link = DocumentAccessLink.objects.filter(contract=contract, is_active=True, category='pdf').order_by('-created_at').first()
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


@login_required
@never_cache
def review_pdf(request, request_id):
    _require_reviewer(request)
    entry = get_object_or_404(DocumentAccessRequest.objects.select_related('link__contract', 'link__requested_version'), pk=request_id, link__contract__is_trashed=False)
    try:
        file = _requested_file(entry.link)
        document = pdf_storage.open(file.name, 'rb')
    except (OSError, PDFDecryptionError, Http404):
        log_activity(request, 'verification', contract=entry.link.contract,
                     note=f'Request PDF preview failed; request={entry.pk}')
        raise Http404
    log_activity(request, 'viewed', contract=entry.link.contract,
                 version_number=entry.link.requested_version.version_number if entry.link.requested_version_id else None,
                 note=f'Staff decrypted/viewed requested PDF; request={entry.pk}; category={entry.link.category}')
    response = FileResponse(document, content_type='application/pdf', filename='requested-document.pdf')
    response['X-Content-Type-Options'] = 'nosniff'
    response['Referrer-Policy'] = 'no-referrer'
    return response
