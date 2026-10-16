from .pdf_storage import open_pdf, read_pdf, write_pdf, pdf_storage
#views.py
from django.shortcuts import render, redirect, get_object_or_404
from .access import (can_view, can_download, can_share, can_manage, is_admin, is_read_only_user,
                     managed_contracts, viewable_contracts,
                     visible_logs, document_access, view_document_access, bulk_document_access, workspace_access,
                     workspace_mutation)
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.conf import settings
from .models import Contract, Folder, ContractVersion, PhysicalVerificationManifest, Tutorial, AccountSecurity, PasswordResetRequest, StaffInvitation
from .forms import (ContractForm, TutorialForm, StaffRegistrationForm, StaffPasswordChangeForm,
                    PasswordResetRequestForm, ApprovedPasswordResetForm, sanitize_tutorial_html)
from .utils import (
    generate_file_hash,
    generate_canonical_fingerprint,
    generate_vector_fingerprint,
    encrypt_cf,
    decrypt_cf,
    embed_data_in_image,
    extract_data_from_image,
    extract_lsb_marker_from_pdf,
    extract_cf_from_metadata,
    stamp_seal_on_pdf,
    generate_qr_code,
)
import os
import logging
import time
import re
import uuid
import fitz
from .utils import generate_qr_code
import base64
import json
import csv
import io
import zipfile
import secrets
import hashlib
from PIL import Image
from hmac import compare_digest
from django.http import JsonResponse, FileResponse, Http404, HttpResponse
from .utils import log_activity
from .models import Contract, AuditLog
from django.utils import timezone
from datetime import timedelta
from django.db import transaction
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.http import urlencode
from django.db.models import Case, Value, When
from django.db.models.functions import Lower
from django.core.files import File
from django.contrib.auth.models import User
from django.contrib.auth import login
from .messaging import issue_challenge, check_challenge, notify_account, valid_code, reset_contact_valid
from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_date
from .physical_verification import (
    build_manifest, compare_page, decode_page_token, sign_manifest,
    validate_token_membership, verify_manifest_signature,
)

TRASH_RETENTION_DAYS = 15
logger = logging.getLogger(__name__)


def _document_name_stem(value):
    """Return a case-insensitive document name without its path or PDF suffix."""
    name = os.path.basename(str(value or '')).strip()
    if name.lower().endswith('.pdf'):
        name = name[:-4]
    return name.strip()


def _same_name_contract(uploaded_name, submitted_title='', user=None):
    names = {
        name.casefold()
        for name in (
            _document_name_stem(uploaded_name),
            _document_name_stem(submitted_title),
        )
        if name
    }
    if not names:
        return None

    query = Q()
    for name in names:
        query |= Q(base_filename__iexact=name) | Q(title__iexact=name) | Q(title__iexact=f'{name}.pdf')
    return managed_contracts(user).filter(query, is_trashed=False).order_by('id').first()


def _contract_pdf_filename(contract, version_number, *, short_title=False):
    """Build the browser download name from the system title and version."""
    title = _document_name_stem(contract.title)
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).rstrip('. ')
    if not title:
        title = f'contract_{contract.id}'
    title_limit = 80 if short_title else 220
    return f'{title[:title_limit].rstrip(" ._")}_v{version_number}.pdf'


def _verification_url(request):
    """Return the configured public verification URL for soft-copy links."""
    base = getattr(settings, 'PUBLIC_BASE_URL', '').strip().rstrip('/')
    return base + reverse('public_verify') if base else request.build_absolute_uri(reverse('public_verify'))


def _process_log(process, stage, *, contract=None, request=None, level='info', **details):
    """Emit structured diagnostics without logging keys, hashes, or encrypted payloads."""
    safe_details = {
        'process': process,
        'stage': stage,
        'contract_id': getattr(contract, 'id', None),
        'user_id': request.user.id if request and request.user.is_authenticated else None,
        **details,
    }
    getattr(logger, level)("document_process %s", safe_details)


def _prepare_physical_manifest(contract, version_number, source_pdf, source_fingerprint):
    manifest, tokens = build_manifest(
        contract.id, version_number, source_pdf, source_fingerprint, timezone.now()
    )
    signature = sign_manifest(manifest, settings.RSA_PRIVATE_KEY_PATH)
    qr_directory = os.path.join(
        settings.MEDIA_ROOT, 'seals', 'physical', f'contract_{contract.id}_v{version_number}'
    )
    os.makedirs(qr_directory, exist_ok=True)
    qr_paths = []
    for page_number, token in enumerate(tokens, start=1):
        path = os.path.join(qr_directory, f'page_{page_number}.png')
        generate_qr_code(token, path)
        qr_paths.append(path)
    return manifest, signature, qr_paths


def _save_physical_manifest(version, manifest, signature):
    return PhysicalVerificationManifest.objects.create(
        version=version, manifest_id=manifest['manifest_id'],
        manifest=manifest, signature=signature,
    )


def _hard_delete_contract(contract, request=None, note=''):
    """Permanently removes a contract's files, seal, QR, and DB row. No way back after this."""
    for version in contract.versions.all():
        if version.file:
            version_path = os.path.join(settings.MEDIA_ROOT, str(version.file))
            if os.path.isfile(version_path):
                os.remove(version_path)

    if contract.seal_image:
        seal_path = os.path.join(settings.MEDIA_ROOT, str(contract.seal_image))
        if os.path.isfile(seal_path):
            os.remove(seal_path)

    qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')
    if os.path.isfile(qr_path):
        os.remove(qr_path)

    title = contract.title
    if request is not None:
        log_activity(request, 'deleted', contract=contract, note=note or f'Permanently deleted: {title}')
    contract.delete()


def _purge_expired_trash(request=None):
    """Hard-deletes any trashed contract whose 15-day window has passed."""
    cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    expired = Contract.objects.filter(is_trashed=True, trashed_at__lt=cutoff)
    for contract in expired:
        _hard_delete_contract(
            contract, request=request,
            note=f'Auto-purged after {TRASH_RETENTION_DAYS} days: {contract.title}'
        )


@login_required
def help_tutorials(request):
    tutorials = Tutorial.objects.all()
    selected = None
    selected_id = request.GET.get('tutorial')
    if selected_id:
        selected = tutorials.filter(pk=selected_id).first()
    if selected is None:
        selected = tutorials.first()

    form = TutorialForm()
    if request.method == 'POST':
        if not request.user.has_perm('contracts.add_tutorial'):
            return JsonResponse({'success': False, 'error': 'not_allowed'}, status=403)

        form = TutorialForm(request.POST)
        if form.is_valid():
            tutorial = form.save(commit=False)
            tutorial.created_by = request.user
            tutorial.sort_order = Tutorial.objects.count()
            tutorial.save()
            return redirect(f"{reverse('help_tutorials')}?tutorial={tutorial.id}")

    return render(request, 'help.html', {
        'tutorials': tutorials,
        'selected_tutorial': selected,
        'tutorial_form': form,
        'icon_choices': Tutorial.ICON_CHOICES,
        'editor_content': sanitize_tutorial_html(form.data.get('content', '')) if form.is_bound else '',
        'selected_tutorial_data': {
            'id': selected.id,
            'title': selected.title,
            'summary': selected.summary,
            'icon': selected.icon,
            'content': selected.content,
        } if selected else None,
    })


@login_required
@workspace_mutation
def edit_tutorial(request, pk):
    if not request.user.has_perm('contracts.change_tutorial'):
        return JsonResponse({'success': False, 'error': 'not_allowed'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    tutorial = get_object_or_404(Tutorial, pk=pk)
    form = TutorialForm(request.POST, instance=tutorial)
    if not form.is_valid():
        return JsonResponse({'success': False, 'errors': form.errors.get_json_data()}, status=400)
    form.save()
    return redirect(f"{reverse('help_tutorials')}?tutorial={tutorial.id}")


@login_required
@workspace_mutation
def delete_tutorial(request, pk):
    if not request.user.has_perm('contracts.delete_tutorial'):
        return JsonResponse({'success': False, 'error': 'not_allowed'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    tutorial = get_object_or_404(Tutorial, pk=pk)
    tutorial.delete()
    return redirect('help_tutorials')

def _existing_sealed_upload(uploaded_file, user=None):
    """Recognize issued copies by their marker and exact stored PDF bytes."""
    import hashlib
    uploaded_file.seek(0)
    contents = uploaded_file.read()
    uploaded_file.seek(0)
    try:
        with fitz.open(stream=contents, filetype='pdf') as document:
            keywords = document.metadata.get('keywords', '') or ''
    except Exception:
        return None
    if not keywords.startswith('SEALGUARD:'):
        return None
    marker = keywords[len('SEALGUARD:'):]
    if not marker:
        return None
    digest = hashlib.sha256(contents).digest()
    versions = ContractVersion.objects.select_related('contract').filter(
        encrypted_cf=marker, contract__is_trashed=False, contract__in=managed_contracts(user),
    )
    for version in versions:
        try:
            with version.file.open('rb') as stored:
                stored_digest = hashlib.sha256()
                for chunk in iter(lambda: stored.read(65536), b''):
                    stored_digest.update(chunk)
            if digest == stored_digest.digest():
                return {
                    'title': version.contract.title,
                    'version': version.version_number,
                    'url': reverse('preview_contract_version', args=[version.id]),
                }
        except OSError:
            continue
    return None


@login_required
@workspace_mutation
def check_duplicate_upload(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'success': False, 'error': 'missing_file'}, status=400)

    validation_form = ContractForm({'title': request.POST.get('title') or 'Duplicate check'}, request.FILES)
    validation_form.is_valid()
    if validation_form.errors.get('file'):
        return JsonResponse({
            'success': False,
            'error': 'invalid_file',
            'message': ' '.join(validation_form.errors['file']),
        }, status=400)
    uploaded_file.seek(0)

    existing = _existing_sealed_upload(uploaded_file, request.user)
    if existing:
        return JsonResponse({'success': True, 'already_authenticated': existing, 'duplicates': []})

    same_name = _same_name_contract(uploaded_file.name, request.POST.get('title', ''), request.user)
    if same_name:
        latest = same_name.versions.order_by('-version_number').first()
        return JsonResponse({
            'success': True,
            'duplicates': [{
                'id': same_name.id,
                'title': same_name.title,
                'filename': os.path.basename(same_name.file.name),
                'match_type': 'same_name',
                'next_version': (latest.version_number + 1) if latest else 1,
            }],
        })

    temp_path = os.path.join(
        settings.MEDIA_ROOT, 'temp', 'duplicate-check', f'{uuid.uuid4().hex}.pdf'
    )
    os.makedirs(os.path.dirname(temp_path), exist_ok=True)
    try:
        write_pdf(temp_path, uploaded_file.read())
        original_cf = generate_canonical_fingerprint(temp_path)
        duplicates = managed_contracts(request.user).filter(
            original_fingerprint=original_cf,
            is_trashed=False,
        ).order_by('id')
        return JsonResponse({
            'success': True,
            'duplicates': [
                {
                    'id': contract.id,
                    'title': contract.title,
                    'filename': os.path.basename(contract.file.name),
                    'match_type': 'same_content',
                    'next_version': (
                        (contract.versions.order_by('-version_number').first().version_number + 1)
                        if contract.versions.exists() else 1
                    ),
                }
                for contract in duplicates
            ],
        })
    except Exception:
        return JsonResponse({'success': False, 'error': 'unable_to_check_file'}, status=400)
    finally:
        if os.path.isfile(temp_path):
            os.remove(temp_path)


@login_required
@workspace_mutation
def upload_contract(request):
    if request.method == 'POST':
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            existing = _existing_sealed_upload(request.FILES['file'], request.user)
            if existing:
                return render(request, 'upload.html', {'form': form, 'already_authenticated': existing})
            existing_name_match = _same_name_contract(
                request.FILES['file'].name,
                request.POST.get('title', ''), request.user,
            )
            if existing_name_match and request.POST.get('force_new') != '1':
                return add_revision(request, existing_name_match.id)

            started_at = time.perf_counter()
            contract = form.save(commit=False)
            contract.recipient = request.user
            contract.uploaded_by = request.user
            contract.save()
            _process_log('initial_encryption', 'contract_created', contract=contract, request=request)

            pdf_path = contract.file.path
            original_filename_only = os.path.splitext(os.path.basename(pdf_path))[0]
            contract.base_filename = original_filename_only

            # Keep the legacy staging flag for existing imports and tests. The
            # normal upload interface no longer exposes an unencrypted option.
            if request.POST.get('skip_encryption') == '1':
                original_cf = generate_canonical_fingerprint(pdf_path)
                vector_cf = generate_vector_fingerprint(pdf_path)
                contract.original_fingerprint = original_cf
                contract.fingerprint = original_cf
                contract.vector_fingerprint = vector_cf
                contract.save(update_fields=[
                    'base_filename', 'original_fingerprint', 'fingerprint',
                    'vector_fingerprint', 'modified_at',
                ])
                ContractVersion.objects.create(
                    contract=contract,
                    version_number=1,
                    source='upload-unencrypted',
                    file=contract.file.name,
                    fingerprint=original_cf,
                    vector_fingerprint=vector_cf,
                    previous_fingerprint='',
                    created_by=request.user,
                )
                log_activity(request, 'added', contract=contract,
                             note='Initial upload encrypted at rest; authenticity sealing skipped')
                _process_log(
                    'initial_upload', 'completed_without_encryption',
                    contract=contract, request=request, version=1,
                )
                return redirect('contract_list')

            default_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
            stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')
            qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')

            # ── Step 1: Generate CF from ORIGINAL pdf ──
            original_cf = generate_canonical_fingerprint(pdf_path)
            _process_log('initial_encryption', 'canonical_fingerprint_generated', contract=contract, request=request)

            # ── Step 2: Encrypt the CF ──
            encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
                original_cf, settings.RSA_PUBLIC_KEY_PATH
            )
            _process_log('initial_encryption', 'fingerprint_encrypted_and_key_wrapped', contract=contract, request=request)

            # ── Step 3: Embed CF into seal via LSB ──
            embed_data_in_image(default_seal, stamped_seal, encrypted)
            _process_log('initial_encryption', 'lsb_seal_created', contract=contract, request=request)

            # ── Step 4: Generate QR ──
            manifest, manifest_signature, qr_paths = _prepare_physical_manifest(
                contract, 1, pdf_path, original_cf
            )
            generate_qr_code(encrypted, qr_path)
            _process_log('initial_encryption', 'qr_marker_created', contract=contract, request=request)

            # ── Step 5: Stamp ONCE with LSB seal + QR ──
            # The database ID is unique, so generated files cannot collide
            # when two users upload PDFs with the same original filename.
            final_filename = f"contract_{contract.id}_v1.pdf"
            final_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
            stamp_seal_on_pdf(
                pdf_path, final_pdf_path, stamped_seal, qr_path=qr_path,
                encrypted_cf=encrypted, qr_paths=qr_paths, encrypt_output=True,
                verify_url=_verification_url(request),
            )
            _process_log('initial_encryption', 'pdf_sealed', contract=contract, request=request)

            # ── Step 6: Generate CF from SEALED pdf ──
            sealed_cf = generate_canonical_fingerprint(final_pdf_path)
            vector_cf = generate_vector_fingerprint(final_pdf_path)
            _process_log('initial_encryption', 'sealed_fingerprint_generated', contract=contract, request=request)
            contract.fingerprint = sealed_cf
            contract.vector_fingerprint = vector_cf
            contract.original_fingerprint = original_cf
            contract.encrypted_cf = encrypted
            contract.hmac_value = hmac_value
            contract.wrapped_key = wrapped_key
            contract.aes_key = ''
            contract.aes_iv = aes_iv

            # ── Step 7: Clean up original ──
            if os.path.isfile(pdf_path):
                os.remove(pdf_path)

            # Contract pointer and version row must commit together, or the
            # viewer loses track of which version is current.
            with transaction.atomic():
                contract.file = f'contracts/{final_filename}'
                contract.seal_image = f'seals/seal_{contract.id}.png'
                contract.save()

                version = ContractVersion.objects.create(
                    contract=contract,
                    version_number=1,
                    source='upload',
                    file=contract.file.name,
                    fingerprint=sealed_cf,
                    vector_fingerprint=vector_cf,
                    previous_fingerprint='',
                    encrypted_cf=encrypted,
                    hmac_value=hmac_value,
                    wrapped_key=wrapped_key,
                    aes_iv=aes_iv,
                    created_by=request.user,
                )

                elapsed_ms = round((time.perf_counter() - started_at) * 1000)
                log_activity(
                    request, 'added', contract=contract,
                    note=f'Initial encryption completed; version=1; duration_ms={elapsed_ms}'
                )
                _save_physical_manifest(version, manifest, manifest_signature)
            _process_log(
                'initial_encryption', 'completed', contract=contract, request=request,
                version=1, duration_ms=elapsed_ms,
            )
            return redirect('contract_list')
    else:
        form = ContractForm()

    return render(request, 'upload.html', {'form': form})


@login_required
@document_access()
def encrypt_contract(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    started_at = time.perf_counter()
    _process_log('reencryption', 'started', contract=contract, request=request)
    pdf_path = contract.file.path

    default_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
    stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')
    qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')

    latest_version = contract.versions.order_by('-version_number').first()
    previous_cf = latest_version.fingerprint if latest_version else None

    original_cf = generate_canonical_fingerprint(pdf_path)
    _process_log('reencryption', 'source_fingerprint_generated', contract=contract, request=request)

    encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
        original_cf, settings.RSA_PUBLIC_KEY_PATH
    )
    _process_log('reencryption', 'fingerprint_encrypted_and_key_wrapped', contract=contract, request=request)

    embed_data_in_image(default_seal, stamped_seal, encrypted)
    _process_log('reencryption', 'lsb_seal_created', contract=contract, request=request)

    generate_qr_code(encrypted, qr_path)
    _process_log('reencryption', 'qr_marker_created', contract=contract, request=request)

    next_version_number = (latest_version.version_number + 1) if latest_version else 1
    manifest, manifest_signature, qr_paths = _prepare_physical_manifest(
        contract, next_version_number, pdf_path, original_cf
    )
    final_filename = f"contract_{contract.id}_v{next_version_number}.pdf"
    final_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
    stamp_seal_on_pdf(
        pdf_path, final_pdf_path, stamped_seal, qr_path=qr_path,
        encrypted_cf=encrypted, qr_paths=qr_paths, encrypt_output=True,
        verify_url=_verification_url(request),
    )
    _process_log('reencryption', 'pdf_sealed', contract=contract, request=request, version=next_version_number)

    sealed_cf = generate_canonical_fingerprint(final_pdf_path)
    version_cf = generate_canonical_fingerprint(final_pdf_path, previous_cf=previous_cf)
    vector_cf = generate_vector_fingerprint(final_pdf_path)
    contract.fingerprint = sealed_cf
    contract.vector_fingerprint = vector_cf
    contract.original_fingerprint = original_cf
    contract.encrypted_cf = encrypted
    contract.hmac_value = hmac_value
    contract.wrapped_key = wrapped_key
    contract.aes_key = ''
    contract.aes_iv = aes_iv

    # Contract pointer and version row must commit together, or the
    # viewer loses track of which version is current.
    with transaction.atomic():
        contract.file = f'contracts/{final_filename}'
        contract.seal_image = f'seals/seal_{contract.id}.png'
        contract.save()

        version = ContractVersion.objects.create(
            contract=contract,
            version_number=next_version_number,
            source='reencrypt',
            file=contract.file.name,
            fingerprint=version_cf,
            vector_fingerprint=vector_cf,
            previous_fingerprint=previous_cf or '',
            encrypted_cf=encrypted,
            hmac_value=hmac_value,
            wrapped_key=wrapped_key,
            aes_iv=aes_iv,
            created_by=request.user,
        )

        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        log_activity(
            request, 'encrypted', contract=contract,
            note=f'Re-encryption completed; version={next_version_number}; duration_ms={elapsed_ms}'
        )
        _save_physical_manifest(version, manifest, manifest_signature)
    _process_log(
        'reencryption', 'completed', contract=contract, request=request,
        version=next_version_number, duration_ms=elapsed_ms,
    )

    return redirect('contract_list')

@login_required
@document_access()
def add_revision(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)

    if request.method == 'POST':
        started_at = time.perf_counter()
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return redirect('contract_list')

        existing = _existing_sealed_upload(uploaded_file, request.user)
        if existing:
            return render(request, 'upload.html', {
                'is_revision': True, 'contract': contract, 'already_authenticated': existing,
            })

        # ── Validate it's actually a PDF (mirrors validate_pdf_signature) ──
        header = uploaded_file.read(5)
        uploaded_file.seek(0)
        if header != b'%PDF-' or not uploaded_file.name.lower().endswith('.pdf'):
            return render(request, 'upload.html', {
                'is_revision': True,
                'contract': contract,
                'pdf_error': 'This file is not a valid PDF.',
            })

        # ── Save the new revision file temporarily ──
        temp_path = os.path.join(settings.MEDIA_ROOT, 'temp', f'{uuid.uuid4().hex}.pdf')
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        write_pdf(temp_path, uploaded_file.read())

        default_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
        stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')
        qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')

        latest_version = contract.versions.order_by('-version_number').first()
        previous_cf = latest_version.fingerprint if latest_version else None

        # Fingerprint the unstamped revision directly. Version chaining is
        # applied separately to the final sealed ContractVersion below.
        original_cf = generate_canonical_fingerprint(temp_path)
        _process_log('revision_encryption', 'source_fingerprint_generated', contract=contract, request=request)

        encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
            original_cf, settings.RSA_PUBLIC_KEY_PATH
        )
        _process_log('revision_encryption', 'fingerprint_encrypted_and_key_wrapped', contract=contract, request=request)

        embed_data_in_image(default_seal, stamped_seal, encrypted)
        _process_log('revision_encryption', 'lsb_seal_created', contract=contract, request=request)
        generate_qr_code(encrypted, qr_path)
        _process_log('revision_encryption', 'qr_marker_created', contract=contract, request=request)

        next_version_number = (latest_version.version_number + 1) if latest_version else 1
        manifest, manifest_signature, qr_paths = _prepare_physical_manifest(
            contract, next_version_number, temp_path, original_cf
        )
        final_filename = f"contract_{contract.id}_v{next_version_number}.pdf"
        final_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
        stamp_seal_on_pdf(
            temp_path, final_pdf_path, stamped_seal, qr_path=qr_path,
            encrypted_cf=encrypted, qr_paths=qr_paths, encrypt_output=True,
            verify_url=_verification_url(request),
        )
        _process_log('revision_encryption', 'pdf_sealed', contract=contract, request=request, version=next_version_number)

        sealed_cf = generate_canonical_fingerprint(final_pdf_path)
        version_cf = generate_canonical_fingerprint(final_pdf_path, previous_cf=previous_cf)
        vector_cf = generate_vector_fingerprint(final_pdf_path)

        contract.fingerprint = sealed_cf
        contract.vector_fingerprint = vector_cf
        contract.original_fingerprint = original_cf
        contract.encrypted_cf = encrypted
        contract.hmac_value = hmac_value
        contract.wrapped_key = wrapped_key
        contract.aes_key = ''
        contract.aes_iv = aes_iv
        # Contract pointer and version row must commit together, or the
        # viewer loses track of which version is current.
        with transaction.atomic():
            contract.file = f'contracts/{final_filename}'
            contract.seal_image = f'seals/seal_{contract.id}.png'
            contract.save()

            if os.path.isfile(temp_path):
                os.remove(temp_path)

            version = ContractVersion.objects.create(
                contract=contract,
                version_number=next_version_number,
                source='revision',
                file=contract.file.name,
                fingerprint=version_cf,
                vector_fingerprint=vector_cf,
                previous_fingerprint=previous_cf or '',
                encrypted_cf=encrypted,
                hmac_value=hmac_value,
                wrapped_key=wrapped_key,
                aes_iv=aes_iv,
                created_by=request.user,
            )

            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            log_activity(
                request, 'edited', contract=contract,
                note=f'New revision encrypted; version={next_version_number}; duration_ms={elapsed_ms}'
            )
            _save_physical_manifest(version, manifest, manifest_signature)
        _process_log(
            'revision_encryption', 'completed', contract=contract, request=request,
            version=next_version_number, duration_ms=elapsed_ms,
        )
        return redirect('contract_list')

    return render(request, 'upload.html', {'is_revision': True, 'contract': contract})


@login_required
@workspace_access
def contract_list(request):
    contracts = viewable_contracts(request.user).filter(is_trashed=False).order_by('-modified_at', '-id')
    search_query = request.GET.get('q', '').strip()
    if search_query:
        contracts = contracts.filter(
            Q(title__icontains=search_query)
            | Q(uploaded_by__username__icontains=search_query)
            | Q(uploaded_by_name__icontains=search_query)
            | Q(tags__icontains=search_query)
        )
    status_filter = request.GET.get('status', '').strip()
    if status_filter in {'pending', 'final'}:
        contracts = contracts.filter(status=status_filter)
    folder_filter = request.GET.get('folder', '').strip()
    if folder_filter.isdigit():
        contracts = contracts.filter(folder_id=int(folder_filter))
    # Uploader choices come from every visible document, not just the filtered
    # page, so switching between uploaders never empties the dropdown.
    visible_contracts = viewable_contracts(request.user).filter(is_trashed=False)
    uploaders = User.objects.filter(uploaded_contracts__in=visible_contracts).distinct().order_by('username')
    # Deleted accounts only survive as the username snapshot on their documents.
    deleted_uploaders = list(
        visible_contracts.filter(uploaded_by__isnull=True).exclude(uploaded_by_name='')
        .order_by('uploaded_by_name').values_list('uploaded_by_name', flat=True).distinct()
    )
    uploader_filter = request.GET.get('uploader', '').strip()
    uploader_label, uploader_label_deleted = '', False
    deleted_name = uploader_filter.removeprefix('deleted:') if uploader_filter.startswith('deleted:') else None
    if uploader_filter.isdigit():
        contracts = contracts.filter(uploaded_by_id=int(uploader_filter))
        uploader_label = next((u.username for u in uploaders if u.pk == int(uploader_filter)), '')
    elif deleted_name in deleted_uploaders:
        contracts = contracts.filter(uploaded_by__isnull=True, uploaded_by_name=deleted_name)
        uploader_label, uploader_label_deleted = deleted_name, True
    else:
        uploader_filter = ''
    scope = request.GET.get('scope', '').strip()
    if scope == 'mine':
        contracts = contracts.filter(uploaded_by=request.user)
    elif scope == 'shared':
        contracts = contracts.filter(collaborators=request.user).exclude(uploaded_by=request.user)
    else:
        scope = ''
    sort = request.GET.get('sort', '').strip()
    if sort == 'name':
        contracts = contracts.order_by(Lower('title'), 'id')
    elif sort == 'newest':
        contracts = contracts.order_by('-uploaded_at', '-id')
    elif sort == 'oldest':
        contracts = contracts.order_by('uploaded_at', 'id')
    elif sort == 'modified_new':
        contracts = contracts.order_by('-modified_at', '-id')
    elif sort == 'modified_old':
        contracts = contracts.order_by('modified_at', 'id')
    elif sort in {'status_draft', 'status_final'}:
        draft_rank = 0 if sort == 'status_draft' else 1
        contracts = contracts.order_by(
            Case(When(status='pending', then=Value(draft_rank)), default=Value(1 - draft_rank)),
            '-modified_at', '-id',
        )
    else:
        sort = ''
    contracts = contracts.annotate(version_count=Count('versions', distinct=True))
    try:
        per_page = int(request.GET.get('per_page', 25))
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in {10, 25, 50}:
        per_page = 25
    page_obj = Paginator(contracts, per_page).get_page(request.GET.get('page'))
    # Shared by the pagination links so every filter survives a page change.
    filter_query = urlencode({
        key: value for key, value in (
            ('q', search_query), ('status', status_filter), ('folder', folder_filter),
            ('uploader', uploader_filter), ('scope', scope), ('sort', sort), ('per_page', per_page),
        ) if value
    })
    for contract in page_obj.object_list:
        contract.can_share = can_share(request.user, contract)
        contract.can_manage = can_manage(request.user, contract)
    # Folders are shared across the staff workspace.  Folder deletion and
    # document removal are still enforced by delete_folder below.
    folders = Folder.objects.all()
    return render(request, 'list.html', {
        'contracts': page_obj.object_list,
        'page_obj': page_obj,
        'search_query': search_query,
        'folders': folders,
        'status_filter': status_filter,
        'folder_filter': folder_filter,
        'uploaders': uploaders,
        'uploader_filter': uploader_filter,
        'deleted_uploaders': deleted_uploaders,
        'uploader_label': uploader_label,
        'uploader_label_deleted': uploader_label_deleted,
        'uploader_choice_count': len(uploaders) + len(deleted_uploaders),
        'scope': scope,
        'sort': sort,
        'filter_query': filter_query,
        'filters_active': bool(search_query or status_filter or folder_filter or uploader_filter or scope),
        'per_page': per_page,
        'read_only_user': is_read_only_user(request.user),
        'workspace_role': 'Admin' if is_admin(request.user) else ('Staff' if request.user.is_staff else 'User'),
    })

@login_required
@document_access()
def delete_contract(request, contract_id):

    contract = get_object_or_404(Contract, id=contract_id)

    if request.method == 'POST':
        if contract.is_public:
            # Public contracts must be unpublished before they can be trashed.
            # (Blocked client-side too, but enforce it server-side either way.)
            return redirect('contract_list')

        contract.is_trashed = True
        contract.trashed_at = timezone.now()
        contract.save()

        log_activity(request, 'deleted', contract=contract, note=f'Moved to trash: {contract.title}')
        return redirect('contract_list')
    return render(request, 'confirm_delete.html', {'contract': contract})

@login_required
@bulk_document_access
def bulk_delete_contracts(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    ids = request.POST.getlist('ids')
    contracts = Contract.objects.filter(id__in=ids, is_trashed=False)
    moved = 0
    skipped_public = 0
    for contract in contracts:
        if contract.is_public:
            skipped_public += 1
            continue
        contract.is_trashed = True
        contract.trashed_at = timezone.now()
        contract.save(update_fields=['is_trashed', 'trashed_at', 'modified_at'])
        log_activity(request, 'deleted', contract=contract, note=f'Moved to trash: {contract.title}')
        moved += 1
    return JsonResponse({'success': True, 'moved': moved, 'skipped_public': skipped_public})


def _bulk_contract_ids(request):
    """Return unique numeric contract IDs submitted by the bulk-action toolbar."""
    seen = set()
    ids = []
    for value in request.POST.getlist('ids'):
        try:
            contract_id = int(value)
        except (TypeError, ValueError):
            continue
        if contract_id > 0 and contract_id not in seen:
            seen.add(contract_id)
            ids.append(contract_id)
    return ids


@login_required
@bulk_document_access
def bulk_assign_folder(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    ids = _bulk_contract_ids(request)
    folder_id = request.POST.get('folder_id', '').strip()
    folder = None
    if folder_id:
        folder = Folder.objects.filter(pk=folder_id).first()
        if not folder:
            return JsonResponse({'success': False, 'error': 'folder_not_found'}, status=404)

    contracts = Contract.objects.filter(id__in=ids, is_trashed=False)
    updated = 0
    for contract in contracts:
        if contract.folder_id == (folder.id if folder else None):
            continue
        contract.folder = folder
        contract.save(update_fields=['folder', 'modified_at'])
        destination = folder.name if folder else 'No folder'
        log_activity(request, 'edited', contract=contract, note=f'Moved to folder: {destination}')
        updated += 1
    return JsonResponse({'success': True, 'updated': updated, 'requested': len(ids)})


@login_required
@bulk_document_access
def bulk_update_status(request):
    if not is_admin(request.user):
        return JsonResponse({'success': False, 'error': 'admin_required'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    status = request.POST.get('status', '').strip()
    status_labels = dict(Contract.STATUS_CHOICES)
    if status not in {'pending', 'final'}:
        return JsonResponse({'success': False, 'error': 'invalid_status'}, status=400)

    ids = _bulk_contract_ids(request)
    contracts = Contract.objects.filter(id__in=ids, is_trashed=False)
    updated = 0
    for contract in contracts:
        if contract.status == status and (status != 'final' or contract.is_public):
            continue
        contract.status = status
        if status == 'final':
            contract.is_public = True
        contract.save(update_fields=['status', 'is_public', 'modified_at'])
        action = 'approved' if status == 'final' else 'edited'
        log_activity(
            request, action, contract=contract,
            note=f'Status changed to {status_labels[status]}',
        )
        updated += 1
    return JsonResponse({'success': True, 'updated': updated, 'requested': len(ids)})


@login_required
@bulk_document_access
def bulk_encrypt_contracts(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    ids = _bulk_contract_ids(request)
    contracts = list(Contract.objects.filter(id__in=ids, is_trashed=False).order_by('id'))
    encrypted = 0
    already_encrypted = 0
    failed = []
    for contract in contracts:
        if contract.encrypted_cf:
            already_encrypted += 1
            continue
        try:
            # Reuse the established single-document encryption pipeline so a
            # bulk operation creates the same seal, version, and audit data.
            encrypt_contract(request, contract.id)
            encrypted += 1
        except Exception:
            logger.exception('Bulk encryption failed for contract %s', contract.id)
            failed.append(contract.id)

    return JsonResponse({
        'success': not failed,
        'encrypted': encrypted,
        'already_encrypted': already_encrypted,
        'failed': failed,
        'requested': len(ids),
    }, status=207 if failed else 200)


@login_required
@bulk_document_access
@never_cache
def bulk_download_contracts(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    ids = _bulk_contract_ids(request)
    contracts = Contract.objects.filter(id__in=ids, is_trashed=False).order_by('id')
    archive_buffer = io.BytesIO()
    added = 0
    used_names = set()
    with zipfile.ZipFile(archive_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for contract in contracts:
            if not contract.file:
                continue
            try:
                latest_version = contract.versions.order_by('-version_number').first()
                version_number = latest_version.version_number if latest_version else 1
                filename = os.path.splitext(_contract_pdf_filename(contract, version_number))[0] + '.sgpdf'
                if filename.casefold() in used_names:
                    stem, extension = os.path.splitext(filename)
                    filename = f'{stem}_{contract.id}{extension}'
                used_names.add(filename.casefold())
                from .encrypted_documents import package_bytes
                archive.writestr(filename, package_bytes(contract, contract.file,
                    latest_version if latest_version and latest_version.file.name == contract.file.name else None))
                log_activity(
                    request, 'downloaded', contract=contract,
                    note='Downloaded current contract PDF in bulk ZIP',
                    version_number=version_number,
                )
                added += 1
            except (FileNotFoundError, OSError):
                logger.warning('Skipped missing contract file during bulk download: %s', contract.id)

    if not added:
        return JsonResponse({'success': False, 'error': 'no_files_available'}, status=404)
    response = HttpResponse(archive_buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="SealGuard_selected_documents.zip"'
    response['X-SealGuard-File-Count'] = str(added)
    return response

@login_required
def trash_list(request):

    _purge_expired_trash(request)

    trashed = managed_contracts(request.user).filter(is_trashed=True).order_by('-trashed_at')
    trash_items = []
    for c in trashed:
        expires_at = c.trashed_at + timedelta(days=TRASH_RETENTION_DAYS)
        days_left = max((expires_at - timezone.now()).days, 0)
        trash_items.append({
            'contract': c,
            'expires_at': expires_at,
            'days_left': days_left,
        })

    folders = Folder.objects.filter(owner=request.user)
    return render(request, 'trash.html', {'trash_items': trash_items, 'folders': folders})


@login_required
@document_access()
def restore_contract(request, contract_id):

    if request.method == 'POST':
        contract = get_object_or_404(Contract, id=contract_id, is_trashed=True)
        contract.is_trashed = False
        contract.trashed_at = None
        contract.save()
        log_activity(request, 'edited', contract=contract, note=f'Restored from trash: {contract.title}')
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)


@login_required
@document_access()
def permanently_delete_contract(request, contract_id):

    if request.method == 'POST':
        contract = get_object_or_404(Contract, id=contract_id, is_trashed=True)
        title = contract.title
        _hard_delete_contract(contract, request=request, note=f'Permanently deleted from trash: {title}')
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)


@login_required
def empty_trash(request):

    if request.method == 'POST':
        trashed = managed_contracts(request.user).filter(is_trashed=True)
        count = trashed.count()
        for contract in trashed:
            _hard_delete_contract(contract, request=request, note=f'Trash emptied: {contract.title}')
        return JsonResponse({'success': True, 'count': count})
    return JsonResponse({'success': False}, status=400)

def _resolve_physical_page(token):
    """Validate a page token against its signed manifest.

    Returns (manifest_record, payload, None) or (None, None, error_response).
    """
    token = (token or '').strip()
    if not token:
        return None, None, JsonResponse({'valid': False, 'message': 'No QR code was received.'}, status=400)

    try:
        payload = decode_page_token(token)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None, None, JsonResponse({
            'valid': False,
            'message': 'This is not a valid SealGuard physical-page QR code.',
        }, status=400)

    manifest_record = PhysicalVerificationManifest.objects.select_related(
        'version__contract'
    ).filter(manifest_id=payload['m']).first()
    if not manifest_record:
        return None, None, JsonResponse({
            'valid': False,
            'message': 'No registered SealGuard document was found for this QR code.',
        }, status=404)

    if not verify_manifest_signature(
        manifest_record.manifest,
        manifest_record.signature,
        settings.RSA_PUBLIC_KEY_PATH,
    ) or not validate_token_membership(payload, manifest_record.manifest):
        return None, None, JsonResponse({
            'valid': False,
            'message': 'The QR code does not match the signed document manifest.',
        }, status=422)

    if manifest_record.version.contract.is_trashed:
        return None, None, JsonResponse({
            'valid': False,
            'message': 'This QR belongs to a document currently in the trash.',
        }, status=410)
    return manifest_record, payload, None


def _registered_page_pdf(manifest_record, page_number, thumbnail_width=None):
    """Return a one-page PDF so a single QR never exposes the rest of the document.

    With thumbnail_width, returns (pdf_bytes, png_bytes) instead.
    """
    with open_pdf(manifest_record.version.file.path) as document:
        thumbnail = None
        if thumbnail_width:
            page = document.load_page(page_number - 1)
            scale = thumbnail_width / page.rect.width
            thumbnail = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png')
        single = fitz.open()
        try:
            single.insert_pdf(document, from_page=page_number - 1, to_page=page_number - 1)
            data = single.tobytes(garbage=3, deflate=True)
        finally:
            single.close()
    return (data, thumbnail) if thumbnail_width else data


def verify_physical_qr(request):
    """Validate a scanned page token before the full physical-page comparison."""
    if request.method != 'POST':
        return JsonResponse({'valid': False, 'message': 'Invalid request method.'}, status=405)

    token = request.POST.get('qr_token', '').strip()
    manifest_record, payload, error = _resolve_physical_page(token)
    if error:
        return error

    page_number = int(payload['p'])
    total_pages = int(payload['n'])
    contract = manifest_record.version.contract
    try:
        _data, thumbnail = _registered_page_pdf(manifest_record, page_number, thumbnail_width=240)
    except (OSError, ValueError, RuntimeError):
        return JsonResponse({'valid': False, 'message': 'The registered page is unavailable. Please contact staff.'}, status=410)
    response = JsonResponse({
        'valid': True,
        'contract_id': contract.id,
        'title': contract.title,
        'version': manifest_record.version.version_number,
        'page': page_number,
        'total_pages': total_pages,
        'thumbnail': 'data:image/png;base64,' + base64.b64encode(thumbnail).decode('ascii'),
        'page_pdf_url': reverse('verify_physical_page') + '?' + urlencode({'qr_token': token}),
        'message': (
            f'Registered QR confirmed: page {page_number} of {total_pages}. '
            'Compare it with your printed page, then capture the complete page to finish verification.'
        ),
    })
    response['Cache-Control'] = 'private, no-store'
    return response


@never_cache
def verify_physical_page(request):
    """Serve only the registered page identified by a valid QR token, as a PDF."""
    manifest_record, payload, error = _resolve_physical_page(request.GET.get('qr_token', ''))
    if error:
        raise Http404
    try:
        data = _registered_page_pdf(manifest_record, int(payload['p']))
    except (OSError, ValueError, RuntimeError):
        raise Http404
    return FileResponse(io.BytesIO(data), content_type='application/pdf', filename='registered-page.pdf')


def verify_physical(request):
    result = None
    details = {}
    if request.method == 'POST':
        uploaded_files = request.FILES.getlist('physical_file')
        uploaded_file = uploaded_files[0] if uploaded_files else None
        raw_tokens = request.POST.get('page_tokens', '').strip()
        temp_path = None
        try:
            tokens = json.loads(raw_tokens)
            if not uploaded_file or not isinstance(tokens, list) or not tokens:
                raise ValueError('A scan and all page QR tokens are required.')
            temp_directory = os.path.join(settings.MEDIA_ROOT, 'temp', 'physical')
            os.makedirs(temp_directory, exist_ok=True)
            temp_path = os.path.join(temp_directory, f'{uuid.uuid4().hex}.pdf')
            uploaded_file.seek(0)
            if len(uploaded_files) == 1 and uploaded_file.read(5) == b'%PDF-':
                uploaded_file.seek(0)
                write_pdf(temp_path, uploaded_file.read())
            else:
                images = []
                for page_file in uploaded_files:
                    page_file.seek(0)
                    images.append(Image.open(page_file).convert('RGB'))
                if not images:
                    raise ValueError('Upload a scanned PDF or page photographs.')
                image_pdf = io.BytesIO()
                images[0].save(image_pdf, 'PDF', save_all=True, append_images=images[1:])
                write_pdf(temp_path, image_pdf.getvalue())

            payloads = [decode_page_token(token) for token in tokens]
            manifest_record = PhysicalVerificationManifest.objects.select_related(
                'version__contract'
            ).filter(manifest_id=payloads[0]['m']).first()
            if not manifest_record:
                result = 'invalid'
                details['message'] = 'No SealGuard physical manifest was found for these pages.'
            elif not verify_manifest_signature(
                manifest_record.manifest, manifest_record.signature, settings.RSA_PUBLIC_KEY_PATH
            ):
                result = 'invalid'
                details['message'] = 'The registered manifest signature is invalid.'
            else:
                manifest = manifest_record.manifest
                contract = manifest_record.version.contract
                token_membership_valid = all(
                    validate_token_membership(payload, manifest) for payload in payloads
                )
                page_numbers = [int(payload['p']) for payload in payloads]
                expected_numbers = list(range(1, manifest['total_pages'] + 1))
                duplicates = sorted({number for number in page_numbers if page_numbers.count(number) > 1})
                missing = sorted(set(expected_numbers) - set(page_numbers))
                mixed = any(payload['m'] != manifest['manifest_id'] for payload in payloads)
                details.update({
                    'contract': contract,
                    'version': manifest['version_number'],
                    'missing_pages': missing,
                    'duplicate_pages': duplicates,
                    'submitted_order': page_numbers,
                    'expected_order': expected_numbers,
                })

                with open_pdf(temp_path) as scan_document:
                    page_count_matches = scan_document.page_count == manifest['total_pages']
                    token_count_matches = len(payloads) == scan_document.page_count
                    structure_valid = (
                        token_membership_valid and not mixed and not duplicates and not missing
                        and page_numbers == expected_numbers and page_count_matches and token_count_matches
                    )
                    if not structure_valid:
                        result = 'invalid'
                        details['message'] = 'The submitted pages do not match the registered document structure.'
                    else:
                        comparisons = []
                        with open_pdf(manifest_record.version.file.path) as official_document:
                            for index, payload in enumerate(payloads):
                                page_number = int(payload['p'])
                                comparison = compare_page(
                                    scan_document[index], official_document[page_number - 1],
                                    official_document[page_number - 1].get_text('text'),
                                )
                                comparison['page_number'] = page_number
                                comparisons.append(comparison)
                        details['comparisons'] = comparisons
                        states = {comparison['state'] for comparison in comparisons}
                        if 'differences' in states:
                            result = 'differences'
                            details['message'] = 'Valid page identities were found, but content differences were detected.'
                        elif 'manual_review' in states:
                            result = 'manual_review'
                            details['message'] = 'Page identity is valid, but scan quality requires staff review.'
                        else:
                            result = 'verified'
                            details['message'] = 'Every page and its registered content passed verification.'

                action = (
                    'viewed' if result == 'verified'
                    else 'reported_tampering' if result == 'differences'
                    else 'verification'
                )
                verification_log = log_activity(
                    request, action, contract=contract,
                    note=f'Physical verification: {result}; version={manifest["version_number"]}',
                )
                if result in {'differences', 'manual_review'}:
                    _attach_verification_evidence(verification_log, temp_path, uploaded_file.name)
                    if request.user.is_authenticated:
                        details['review_url'] = reverse(
                            'physical_verification_review',
                            args=[verification_log.id, manifest_record.version.id],
                        )
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            result = 'invalid'
            details['message'] = str(error)
        except Exception as error:
            logger.exception('Physical verification failed')
            result = 'error'
            details['message'] = 'Verification could not be completed safely.'
        finally:
            if temp_path and os.path.isfile(temp_path):
                os.remove(temp_path)

    return render(request, 'verify_physical.html', {'result': result, 'details': details})


@login_required
def physical_verification_review(request, log_id, version_id):
    audit_log = get_object_or_404(
        visible_logs(request.user).select_related('contract'), pk=log_id,
        contract__isnull=False,
    )
    version = get_object_or_404(
        ContractVersion, pk=version_id, contract=audit_log.contract
    )
    if not audit_log.evidence_file:
        raise Http404
    return render(request, 'physical_review.html', {
        'audit_log': audit_log,
        'version': version,
        'submitted_url': reverse('audit_evidence_preview', args=[audit_log.id]),
    })

@login_required
@workspace_mutation
@document_access()
def upload_signed_scan(request, contract_id):
    """
    Accepts a scanned copy of a physically-signed contract. Verifies it via
    the QR-encoded encrypted CF (the only marker that survives print/scan),
    then records it as a new chained version tagged 'physical_scan'.
    """
    contract = get_object_or_404(Contract, id=contract_id)

    if request.method == 'POST':
        uploaded_file = request.FILES.get('scanned_file')
        if not uploaded_file:
            return redirect('contract_list')

        temp_path = os.path.join(settings.MEDIA_ROOT, 'temp', f'{uuid.uuid4().hex}.pdf')
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        write_pdf(temp_path, uploaded_file.read())

        try:
            latest_version = contract.versions.order_by('-version_number').first()
            previous_cf = latest_version.fingerprint if latest_version else None
            next_version_number = (latest_version.version_number + 1) if latest_version else 1
            source_cf = generate_canonical_fingerprint(temp_path)
            encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
                source_cf, settings.RSA_PUBLIC_KEY_PATH
            )
            default_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
            stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')
            embed_data_in_image(default_seal, stamped_seal, encrypted)
            manifest, manifest_signature, qr_paths = _prepare_physical_manifest(
                contract, next_version_number, temp_path, source_cf
            )
            final_filename = f"contract_{contract.id}_signed_v{next_version_number}.pdf"
            final_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
            stamp_seal_on_pdf(
                temp_path, final_path, stamped_seal, encrypted_cf=encrypted, qr_paths=qr_paths,
                encrypt_output=True, verify_url=_verification_url(request)
            )
            sealed_cf = generate_canonical_fingerprint(final_path)
            version_cf = generate_canonical_fingerprint(final_path, previous_cf=previous_cf)
            vector_cf = generate_vector_fingerprint(final_path)
            version = ContractVersion.objects.create(
                contract=contract,
                version_number=next_version_number,
                source='physical_scan',
                file=f'contracts/{final_filename}',
                fingerprint=version_cf,
                vector_fingerprint=vector_cf,
                previous_fingerprint=previous_cf or '',
                encrypted_cf=encrypted,
                hmac_value=hmac_value,
                wrapped_key=wrapped_key,
                aes_iv=aes_iv,
                created_by=request.user,
            )
            _save_physical_manifest(version, manifest, manifest_signature)
            contract.status = 'final'
            contract.is_public = True
            contract.file = f'contracts/{final_filename}'
            contract.fingerprint = sealed_cf
            contract.vector_fingerprint = vector_cf
            contract.original_fingerprint = source_cf
            contract.encrypted_cf = encrypted
            contract.hmac_value = hmac_value
            contract.wrapped_key = wrapped_key
            contract.aes_iv = aes_iv
            contract.aes_key = ''
            contract.seal_image = f'seals/seal_{contract.id}.png'
            contract.save()
            log_activity(
                request, 'approved', contract=contract,
                note=f'Signed scan registered, sealed, and chained as version {next_version_number}',
            )

        finally:
            if os.path.isfile(temp_path):
                os.remove(temp_path)

        return redirect('contract_list')

    return render(request, 'upload_signed_scan.html', {'contract': contract})

import re

def is_valid_encrypted_cf(data: str):
    """
    Checks if extracted LSB data looks like a genuine
    AES-256-CBC encrypted CF from our system.
    """
    if not data or len(data) < 44:
        return False

    # Must match Base64 pattern
    base64_pattern = re.compile(r'^[A-Za-z0-9+/]+={0,2}$')
    if not base64_pattern.match(data):
        return False

    # Decode and check byte length is multiple of 16 (AES block size)
    try:
        decoded = base64.b64decode(data)
        if len(decoded) % 16 != 0:
            return False
        # Must be at least 32 bytes (one AES block minimum)
        if len(decoded) < 32:
            return False
    except Exception:
        return False

    return True


def has_barangay_footer(pdf_path: str):
    """
    Checks if the PDF contains the barangay footer text,
    indicating it was processed by our system.
    """
    try:
        doc = open_pdf(pdf_path)
        footer_keywords = [
            'barangay sto. nino',
            'digitally authenticated document',
            'barangay sto. niño',
            'binan city',
        ]

        for page in doc:
            text = page.get_text().lower()
            # Check if at least 2 footer keywords are present
            matches = sum(1 for kw in footer_keywords if kw in text)
            if matches >= 2:
                doc.close()
                return True

        doc.close()
        return False
    except Exception:
        return False

from django.shortcuts import render, redirect

def get_contract_meta(contract):
    verified_log = AuditLog.objects.filter(
        contract=contract, action='viewed'
    ).order_by('-timestamp').first()
    latest_version = contract.versions.order_by('-version_number').first()
    return {
        'created': contract.uploaded_at,
        'encrypted': latest_version.created_at if latest_version else None,
        'verified': verified_log.timestamp if verified_log else None,
        'version': latest_version.version_number if latest_version else 1,
    }


@login_required
@view_document_access
def mark_contract_viewed(request, contract_id):
    if request.method != 'POST':
        return JsonResponse({'success': False}, status=405)
    contract = get_object_or_404(Contract, id=contract_id, is_trashed=False)
    latest_version = contract.versions.order_by('-version_number').first()
    _process_log('pdf_view', 'viewed_marker_received', contract=contract, request=request)
    log_activity(
        request, 'viewed', contract=contract,
        note='Opened in document viewer',
        version_number=latest_version.version_number if latest_version else None,
    )
    return JsonResponse({'success': True})

@view_document_access
@never_cache
def contract_version_history(request, contract_id):
    started_at = time.perf_counter()
    metadata_only = request.GET.get('metadata_only') == '1'
    _process_log(
        'pdf_view', 'version_history_started', request=request,
        requested_contract_id=contract_id, metadata_only=metadata_only,
    )
    contract = get_object_or_404(Contract, id=contract_id)
    contract_loaded_ms = round((time.perf_counter() - started_at) * 1000)
    verified_log = AuditLog.objects.filter(contract=contract, action='viewed').order_by('-timestamp').first()
    versions_qs = contract.versions.select_related('created_by').order_by('-version_number')
    versions = list(versions_qs)
    versions_loaded_ms = round((time.perf_counter() - started_at) * 1000)
    _process_log(
        'pdf_view', 'version_history_data_loaded', contract=contract, request=request,
        metadata_only=metadata_only, version_count=len(versions),
        contract_loaded_ms=contract_loaded_ms, versions_loaded_ms=versions_loaded_ms,
    )
    versions_data = []
    for v in versions:
        versions_data.append({
            'version_number': v.version_number,
            'source': v.get_source_display(),
            'created_at': v.created_at.strftime('%b %d, %Y'),
            'created_by': v.created_by_display or 'Unknown account',
            'created_by_deleted': v.created_by_deleted,
            'file_url': reverse('download_contract_version', args=[v.id]) if v.file else '',
            'preview_url': reverse('preview_contract_version', args=[v.id]) if v.file else '',
            'is_current': bool(contract.file) and v.file.name == contract.file.name,
        })

    current_file_is_tracked = any(version['is_current'] for version in versions_data)

    response_data = {
        'success': True,
        'created': contract.uploaded_at.strftime('%b %d, %Y'),
        'encrypted': versions_data[0]['created_at'] if versions_data else None,
        'verified': verified_log.timestamp.strftime('%b %d, %Y') if verified_log else 'Not yet verified',
        'version_count': len(versions_data),
        'uploaded_by': contract.uploaded_by_display or 'Unknown account',
        'uploaded_by_deleted': contract.uploaded_by_deleted,
        'current_file_is_tracked': current_file_is_tracked,
        'title': contract.title,
        'versions': versions_data,
    }
    _process_log(
        'pdf_view', 'version_history_completed', contract=contract, request=request,
        metadata_only=metadata_only, version_count=len(versions_data),
        response_build_ms=round((time.perf_counter() - started_at) * 1000),
    )
    return JsonResponse(response_data)


def _verification_preview_path(token):
    if not token or not re.fullmatch(r'[0-9a-f]{32}', token):
        return None
    return os.path.join(settings.MEDIA_ROOT, 'verification_previews', f'{token}.pdf')


def _clear_verification_preview(request):
    token = request.session.pop('verify_preview_token', None)
    preview_path = _verification_preview_path(token)
    if preview_path and os.path.isfile(preview_path):
        os.remove(preview_path)


def _purge_old_verification_previews(preview_dir, max_age_seconds=3600):
    cutoff = time.time() - max_age_seconds
    for filename in os.listdir(preview_dir):
        if not re.fullmatch(r'[0-9a-f]{32}\.pdf', filename):
            continue
        path = os.path.join(preview_dir, filename)
        if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
            os.remove(path)


@never_cache
def verification_preview(request, token):
    if request.session.get('verify_preview_token') != token:
        raise Http404
    preview_path = _verification_preview_path(token)
    if not preview_path or not os.path.isfile(preview_path):
        raise Http404
    return FileResponse(
        io.BytesIO(read_pdf(preview_path)),
        content_type='application/pdf',
        filename='verification-preview.pdf',
    )


@login_required
@never_cache
def audit_evidence_preview(request, log_id):
    audit_log = get_object_or_404(
        visible_logs(request.user).select_related('contract'),
        pk=log_id,
    )
    if not audit_log.evidence_file:
        raise Http404
    return FileResponse(
        audit_log.evidence_file.open('rb'),
        content_type='application/pdf',
        filename=audit_log.display_document_title or 'reported-document.pdf',
    )


def _attach_verification_evidence(audit_log, temp_path, original_name):
    """Copy the temporary upload into durable evidence storage for admin review."""
    if not audit_log or not temp_path or not os.path.isfile(temp_path):
        return
    safe_name = os.path.basename(original_name or 'reported-document.pdf')
    with io.BytesIO(read_pdf(temp_path, allow_plaintext=True)) as evidence_handle:
        audit_log.evidence_file.save(safe_name, File(evidence_handle), save=True)


def _serialize_verification_debug_log(debug_log):
    """Store the user-facing, secret-safe verification trace with a size cap."""
    clean_lines = []
    for entry in debug_log or []:
        clean_entry = str(entry).replace('\x00', '').replace('\r', ' ').strip()
        if clean_entry:
            clean_lines.append(clean_entry[:1000])
    return '\n'.join(clean_lines)[:20000]


def _log_public_verification(
    request,
    uploaded_file,
    result,
    *,
    contract=None,
    note='',
    debug_log=None,
):
    """Record verification details without retaining the uploaded PDF itself."""
    detail_map = {
        'authentic': ('Authentic', 'Complete'),
        'tampered': ('Possible Modification', 'Failed'),
        'not_found': ('Not Found', 'No Record'),
        'error': ('Unable to Verify', 'Incomplete'),
    }
    verification_result, integrity_check = detail_map[result]
    action = 'reported_tampering' if result == 'tampered' else (
        'viewed' if result == 'authentic' else 'verification'
    )
    return log_activity(
        request,
        action,
        contract=contract,
        note=note,
        document_title=uploaded_file.name,
        verification_source='Official Barangay Database',
        verification_result=verification_result,
        integrity_check=integrity_check,
        document_size=uploaded_file.size,
        verification_debug_log=_serialize_verification_debug_log(debug_log),
    )


@login_required
def audit_verification_history(request, log_id):
    selected_log = get_object_or_404(
        visible_logs(request.user).select_related('contract'),
        pk=log_id,
    )
    history = visible_logs(request.user).filter(verification_result__gt='')
    if selected_log.contract_id:
        history = history.filter(contract_id=selected_log.contract_id)
    elif selected_log.document_title:
        history = history.filter(document_title=selected_log.document_title)
    else:
        history = history.none()

    events = [
        {
            'id': entry.id,
            'verified_at': timezone.localtime(entry.timestamp).strftime('%b %d, %Y · %I:%M %p'),
            'result': entry.inspection_result or 'Unknown',
            'integrity': entry.inspection_integrity or 'Unknown',
            'is_selected': entry.id == selected_log.id,
            'has_debug_log': bool(entry.verification_debug_log),
        }
        for entry in history.order_by('-timestamp', '-id')
    ]
    return JsonResponse({
        'success': True,
        'selected_result': selected_log.inspection_result,
        'events': events,
    })


@login_required
def audit_verification_log_detail(request, log_id):
    if not (request.user.is_superuser or request.user.is_staff):
        return JsonResponse({'success': False, 'message': 'Administrator access required.'}, status=403)
    entry = get_object_or_404(
        visible_logs(request.user), pk=log_id, verification_result__gt='',
    )
    if not request.user.is_superuser and entry.verification_result.casefold() == 'authentic':
        return JsonResponse({'success': False, 'message': 'Administrator access required.'}, status=403)
    elapsed_ms = None
    if entry.verification_result == 'Running':
        elapsed_ms = max(0, round((timezone.now() - entry.timestamp).total_seconds() * 1000))
    return JsonResponse({
        'success': True,
        'note': entry.note,
        'debug_log': entry.verification_debug_log,
        'result': entry.verification_result,
        'integrity': entry.integrity_check,
        'timestamp': timezone.localtime(entry.timestamp).strftime('%Y-%m-%d %H:%M:%S'),
        'elapsed_ms': elapsed_ms,
    })


STAFF_REGISTRATION_OTP_TTL = 10 * 60
STAFF_DEFAULT_PASSWORD = 'Welcome123!'


@login_required
def create_staff_invitation(request):
    if not request.user.is_superuser:
        return JsonResponse({'success': False, 'error': 'Administrator access required.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    token = secrets.token_urlsafe(32)
    StaffInvitation.objects.create(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_by=request.user,
        expires_at=timezone.now() + timedelta(hours=24),
    )
    return JsonResponse({'success': True, 'url': request.build_absolute_uri(f'/accounts/register/?invite={token}')})


@never_cache
def staff_register(request):
    if request.user.is_authenticated:
        return redirect('contract_list')
    invite_token = request.GET.get('invite', '') or request.POST.get('invite', '') or request.session.get('staff_invite_token', '')
    invite = StaffInvitation.objects.filter(
        token_hash=hashlib.sha256(invite_token.encode()).hexdigest(),
        used_at__isnull=True, expires_at__gt=timezone.now(),
    ).first() if invite_token else None
    if not invite:
        return render(request, 'registration/register.html', {'invite_required': True})
    request.session['staff_invite_token'] = invite_token
    if not request.session.session_key:
        request.session.create()
    pending_key = f'sealguard:staff-registration:{request.session.session_key}'
    pending = cache.get(pending_key)
    form = StaffRegistrationForm(request.POST or None)
    context = {'form': form, 'invite': invite_token, 'otp_sent': bool(pending),
               'recipient': pending.get('recipient', '') if pending else '',
               'channel': pending.get('channel', 'email') if pending else 'email'}
    if request.method == 'POST' and request.POST.get('action') == 'restart':
        cache.delete(pending_key)
        return redirect(request.path)
    if request.method == 'POST' and request.POST.get('otp'):
        verified = check_challenge(pending_key, request.POST.get('otp', '').strip())
        if not verified:
            context.update(otp_sent=bool(cache.get(pending_key)), error='That code is invalid or expired. Codes allow five attempts; request a new code if needed.')
            return render(request, 'registration/register.html', context)
        username, email = verified['username'], verified['email']
        from django.db import IntegrityError, transaction
        try:
            with transaction.atomic():
                claimed = StaffInvitation.objects.filter(pk=invite.pk, used_at__isnull=True, expires_at__gt=timezone.now()).update(used_at=timezone.now())
                if not claimed or User.objects.filter(email__iexact=email).exists():
                    raise IntegrityError('Invitation used or email already registered')
                # Use Django's password setter so the temporary credential is
                # encoded exactly like every other account password.
                user = User(username=username, email=email, is_staff=True)
                user.set_password(STAFF_DEFAULT_PASSWORD)
                user.save()
                AccountSecurity.objects.create(user=user, must_change_password=True,
                    phone_number=verified['phone_number'], phone_verified=verified['channel'] == 'sms',
                    email_verified=verified['channel'] == 'email', preferred_channel=verified['channel'])
        except IntegrityError:
            context.update(otp_sent=False, error='These account details or invitation have already been used. Please try again.')
            return render(request, 'registration/register.html', context)
        # The invitation is single-use. Clear its browser state and return the
        # new account to the required first-password screen instead of
        # revisiting the consumed invitation URL.
        request.session.pop('staff_invite_token', None)
        cache.delete(pending_key)
        # Delivery is an audit/notification concern and must not prevent the
        # completed registration from reaching the password screen.
        try:
            delivered = notify_account(user, 'Your SealGuard account is ready',
                f'Your username is {username}. Your temporary password is {STAFF_DEFAULT_PASSWORD}. Sign in once, then change it immediately.')
            logger.info('staff registration completed; user_id=%s invitation_id=%s notification_delivered=%s',
                        user.pk, invite.pk, delivered)
        except Exception:
            logger.exception('staff registration notification failed; user_id=%s invitation_id=%s', user.pk, invite.pk)
        login(request, user)
        return redirect('password_change_required')
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        if User.objects.filter(email__iexact=email).exists():
            form.add_error('email', 'That email address is already in use.')
        else:
            channel = form.cleaned_data.get('channel') or 'email'
            recipient = form.cleaned_data['phone_number'] if channel == 'sms' else email
            try:
                pending = issue_challenge(pending_key, channel, recipient, 'SealGuard account verification code',
                    {'username': form.cleaned_data['username'], 'email': email, 'phone_number': form.cleaned_data['phone_number']})
                context.update(otp_sent=True, recipient=recipient, channel=channel)
            except ValidationError as exc:
                context['error'] = exc.messages[0]
    return render(request, 'registration/register.html', context)


@login_required
def password_change_required(request):
    security = getattr(request.user, 'account_security', None)
    if not security or not security.must_change_password:
        return redirect('contract_list')
    form = StaffPasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        security.must_change_password = False
        security.password_changed_at = timezone.now()
        security.save(update_fields=['must_change_password', 'password_changed_at'])
        return redirect('contract_list')
    return render(request, 'registration/password_change_required.html', {'form': form})


@never_cache
def password_reset_request(request):
    if request.method == 'POST' and request.POST.get('stage') == 'restart':
        logger.info('password reset flow restarted; session=%s', request.session.session_key)
        request.session.pop('password_reset_verified_id', None)
        request.session.pop('password_reset_request_id', None)
        return redirect('password_reset_request')
    reset = None
    step = 'email'
    verified_id = request.session.get('password_reset_verified_id')
    if verified_id:
        reset = PasswordResetRequest.objects.filter(
            pk=verified_id, status='approved', user__is_active=True,
        ).select_related('user').first()
        if reset and reset.expires_at and reset.expires_at >= timezone.now():
            step = 'password'
        else:
            request.session.pop('password_reset_verified_id', None)
            reset = None
    reset_form = PasswordResetRequestForm(request.POST or None)
    if request.method == 'POST' and request.POST.get('stage') == 'password':
        reset = PasswordResetRequest.objects.filter(
            pk=request.POST.get('request_id'), status='approved', user__is_active=True,
        ).select_related('user').first()
        if not reset or not reset_contact_valid(reset) or request.session.get('password_reset_verified_id') != reset.pk or not reset.expires_at or reset.expires_at < timezone.now():
            request.session.pop('password_reset_verified_id', None)
            return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'error': 'Your verification session expired. Request a new code.', 'step': 'email'})
        form = ApprovedPasswordResetForm(reset.user, request.POST)
        if form.is_valid():
            form.save()
            reset.status = 'used'
            reset.otp = ''
            reset.save(update_fields=['status', 'otp'])
            logger.info('password reset completed; reset_id=%s user_id=%s', reset.pk, reset.user_id)
            request.session.pop('password_reset_verified_id', None)
            request.session.pop('password_reset_request_id', None)
            return redirect('login')
        return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'reset': reset, 'form': form, 'step': 'password'})
    if request.method == 'POST' and request.POST.get('stage') == 'verify':
        reset = PasswordResetRequest.objects.filter(pk=request.POST.get('request_id'), status='approved').select_related('user').first()
        if reset and request.session.get('password_reset_request_id') == reset.pk:
            from django.db.models import F
            claimed = PasswordResetRequest.objects.filter(pk=reset.pk, attempts__lt=5).update(attempts=F('attempts') + 1)
        else:
            claimed = False
        if not claimed or not reset_contact_valid(reset) or not reset.expires_at or reset.expires_at < timezone.now() or not valid_code(request.POST.get('otp', ''), reset.otp):
            logger.warning('password reset code rejected; reset_id=%s claimed=%s', reset.pk if reset else None, claimed)
            return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'reset': reset, 'error': 'That verification code is invalid or expired.', 'step': 'verify'})
        request.session['password_reset_verified_id'] = reset.pk
        logger.info('password reset code accepted; reset_id=%s user_id=%s', reset.pk, reset.user_id)
        return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'reset': reset, 'form': ApprovedPasswordResetForm(reset.user), 'step': 'password'})
    if request.method == 'POST' and reset_form.is_valid():
        identifier = reset_form.cleaned_data['identifier'].strip()
        channel = reset_form.cleaned_data.get('channel') or 'email'
        users = User.objects.filter(is_active=True, is_staff=True, is_superuser=False)
        if channel == 'sms':
            user = users.filter(account_security__phone_number=identifier, account_security__phone_verified=True).first()
        else:
            user = users.filter(email__iexact=identifier).exclude(account_security__email_verified=False).first()
        if user:
            logger.info('password reset identifier matched; user_id=%s channel=%s', user.pk, channel)
            session_request_id = request.session.get('password_reset_request_id')
            reset = PasswordResetRequest.objects.filter(pk=session_request_id, user=user, channel=channel).first() if session_request_id else None
            if reset and reset.status == 'approved' and reset.expires_at and reset.expires_at >= timezone.now():
                logger.info('password reset awaiting code; reset_id=%s status=approved', reset.pk)
                request.session['password_reset_request_id'] = reset.pk
                return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'reset': reset, 'step': 'verify'})
            if reset and reset.status == 'pending':
                logger.info('password reset still pending admin approval; reset_id=%s', reset.pk)
                return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'pending': True, 'step': 'email'})
            from .messaging import code_digest
            throttle = 'sealguard:reset-request:' + code_digest(str(user.pk))
            if not cache.add(throttle, True, 60):
                logger.warning('password reset request throttled; user_id=%s channel=%s', user.pk, channel)
                return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'pending': True, 'step': 'email'})
            reset = PasswordResetRequest.objects.create(user=user, email=user.email, channel=channel,
                phone_number=identifier if channel == 'sms' else '', status='pending')
            request.session['password_reset_request_id'] = reset.pk
            logger.info('password reset request created; reset_id=%s user_id=%s channel=%s', reset.pk, user.pk, channel)
            for admin_user in User.objects.filter(is_active=True, is_superuser=True):
                admin_notified = notify_account(admin_user,
                    'SealGuard password reset approval required',
                    f'{user.username} requested a password reset. Review request #{reset.pk} in the Admin Portal.',
                )
                logger.info('password reset admin notification attempted; reset_id=%s admin_user_id=%s channel=%s delivered=%s',
                            reset.pk, admin_user.pk, getattr(getattr(admin_user, 'account_security', None), 'preferred_channel', 'email'), admin_notified)
            return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'pending': True, 'step': 'email'})
        logger.info('password reset identifier did not match an eligible account; channel=%s', channel)
        return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'pending': True, 'step': 'email'})
    return render(request, 'registration/password_reset.html', {'reset_form': reset_form, 'reset': reset, 'step': step})


def public_verify(request):
    result = None
    debug_log = []
    filename_hint = None

    if request.method == 'GET' and request.GET.get('reset') == '1':
        _clear_verification_preview(request)
        request.session.pop('verify_result', None)
        request.session.pop('verify_debug_log', None)
        request.session.pop('verify_filename_hint', None)
        request.session.pop('verify_timestamp', None)
        return redirect('public_verify')

    if request.method == 'POST':
        started_at = time.perf_counter()
        uploaded_file = request.FILES.get('pdf_file')
        request.session['verify_timestamp'] = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')

        if not uploaded_file:
            request.session['verify_result'] = 'error'
            request.session['verify_debug_log'] = []
            request.session['verify_filename_hint'] = None
            return redirect(reverse('public_verify'))

        from .encrypted_documents import PACKAGE_MAGIC
        header = uploaded_file.read(len(PACKAGE_MAGIC))
        uploaded_file.seek(0)
        if uploaded_file.name.lower().endswith('.sgpdf') or header == PACKAGE_MAGIC:
            _clear_verification_preview(request)
            request.session['verify_result'] = 'error'
            request.session['verify_filename_hint'] = 'encrypted'
            request.session['verify_debug_log'] = []
            log_activity(request, 'verification', note='Encrypted package sent to PDF verification; directed to encrypted documents')
            return redirect('public_verify')

        _clear_verification_preview(request)
        preview_dir = os.path.join(settings.MEDIA_ROOT, 'verification_previews')
        os.makedirs(preview_dir, exist_ok=True)
        _purge_old_verification_previews(preview_dir)
        preview_token = uuid.uuid4().hex
        temp_path = os.path.join(preview_dir, f'{preview_token}.pdf')
        write_pdf(temp_path, uploaded_file.read())
        request.session['verify_preview_token'] = preview_token

        # Filenames are display labels only, never evidence of authenticity.
        # Every ordinary PDF submission must execute the real verification pipeline.
        debug_log.append(f"[INFO] File received: {uploaded_file.name} ({uploaded_file.size} bytes)")
        debug_log.append("[INFO] Verification mode: full cryptographic pipeline")
        _process_log('verification', 'file_received', request=request, size_bytes=uploaded_file.size)

        matched_contract = None
        failure_reason = ''
        try:
            with open_pdf(temp_path) as uploaded_pdf:
                debug_log.append(f"[PASS] PDF structure opened successfully: {uploaded_pdf.page_count} page(s)")
            _process_log('verification', 'pdf_structure_validated', request=request)

            has_footer = has_barangay_footer(temp_path)
            if has_footer:
                debug_log.append("[PASS] Footer check: Barangay footer detected")
            else:
                debug_log.append("[WARN] Footer check: No barangay footer found")

            current_cf = generate_canonical_fingerprint(temp_path)
            current_vector_cf = generate_vector_fingerprint(temp_path)
            debug_log.append("[PASS] Sealed PDF fingerprint recomputed")
            debug_log.append("[PASS] Vector drawing fingerprint recomputed")
            _process_log('verification', 'sealed_fingerprint_generated', request=request)

            matched_contract = Contract.objects.filter(
                fingerprint=current_cf,
                is_trashed=False,
            ).first()
            debug_log.append("[INFO] Active contract fingerprint lookup completed")

            expected_vector_cf = ''
            if matched_contract:
                expected_vector_cf = matched_contract.vector_fingerprint
                if not expected_vector_cf:
                    latest_version = matched_contract.versions.order_by('-version_number').first()
                    expected_vector_cf = latest_version.vector_fingerprint if latest_version else ''
                if not expected_vector_cf and matched_contract.file:
                    try:
                        expected_vector_cf = generate_vector_fingerprint(matched_contract.file.path)
                        debug_log.append("[INFO] Legacy vector baseline recovered from the official stored PDF")
                    except (FileNotFoundError, OSError, ValueError, RuntimeError):
                        debug_log.append("[WARN] No vector baseline is available for this legacy record")

            extracted_encrypted = extract_cf_from_metadata(temp_path)
            embedded_lsb_matches = bool(
                matched_contract
                and extract_lsb_marker_from_pdf(temp_path, matched_contract.encrypted_cf)
            )
            enrollment_seal_lsb_matches = False
            if matched_contract and not embedded_lsb_matches and matched_contract.seal_image:
                try:
                    enrollment_seal_marker = extract_data_from_image(matched_contract.seal_image.path)
                    enrollment_seal_lsb_matches = compare_digest(
                        enrollment_seal_marker,
                        matched_contract.encrypted_cf,
                    )
                except (FileNotFoundError, OSError, ValueError):
                    enrollment_seal_lsb_matches = False

            if not matched_contract:
                marker_owner_exists = bool(
                    extracted_encrypted
                    and Contract.objects.filter(
                        encrypted_cf=extracted_encrypted,
                        is_trashed=False,
                    ).exists()
                )
                if marker_owner_exists:
                    debug_log.append("[FAIL] Sealed PDF fingerprint does not match the contract identified by its marker")
                    failure_reason = 'sealed fingerprint mismatch'
                    result = 'tampered'
                elif has_footer or extracted_encrypted:
                    debug_log.append("[WARN] SealGuard authentication evidence remains, but no active official record could be identified")
                    debug_log.append(
                        "[WARN] Authentication details may have been removed or changed by editing or re-exporting this PDF. "
                        "The remaining footer or marker does not prove its authenticity."
                    )
                    failure_reason = 'authentication evidence remains without an identifiable active official record'
                    filename_hint = 'unlinked_modification'
                    result = 'tampered'
                else:
                    debug_log.append("[FAIL] No active contract matches the sealed fingerprint or encrypted marker")
                    failure_reason = 'no active database record matched'
                    filename_hint = 'unknown'
                    result = 'not_found'
            elif expected_vector_cf and not compare_digest(current_vector_cf, expected_vector_cf):
                debug_log.append("[FAIL] Vector drawing fingerprint differs from the official record")
                debug_log.append(
                    "[WARN] This PDF may have been edited or re-saved with a PDF editor. "
                    "Even without a visible change, editor processing can alter protected vector data."
                )
                failure_reason = (
                    'vector drawing data changed; the PDF may have been edited or re-saved '
                    'with a PDF editor'
                )
                result = 'tampered'
            elif not extracted_encrypted:
                debug_log.append("[FAIL] Metadata marker is missing from the matched sealed document")
                failure_reason = 'metadata marker missing'
                result = 'tampered'
            elif not is_valid_encrypted_cf(extracted_encrypted):
                debug_log.append("[FAIL] Metadata marker has an invalid encrypted payload format")
                failure_reason = 'metadata marker malformed'
                result = 'tampered'
            elif not compare_digest(extracted_encrypted, matched_contract.encrypted_cf):
                debug_log.append("[FAIL] Metadata marker does not belong to the matched contract")
                failure_reason = 'metadata marker ownership mismatch'
                result = 'tampered'
            elif not embedded_lsb_matches and not enrollment_seal_lsb_matches:
                debug_log.append("[FAIL] Digital seal LSB marker is missing or inconsistent")
                failure_reason = 'digital seal LSB marker mismatch'
                result = 'tampered'
            elif not all([
                matched_contract.wrapped_key,
                matched_contract.aes_iv,
                matched_contract.hmac_value,
                matched_contract.original_fingerprint,
            ]):
                debug_log.append("[ERROR] Matched contract is missing required cryptographic enrollment data")
                failure_reason = 'incomplete cryptographic enrollment data'
                result = 'error'
            else:
                debug_log.append(f"[PASS] Sealed fingerprint matched active contract [{matched_contract.id}]")
                debug_log.append("[PASS] Metadata marker belongs to the matched contract")
                if embedded_lsb_matches:
                    debug_log.append("[PASS] Digital seal LSB marker recovered from the PDF")
                else:
                    debug_log.append("[PASS] Legacy enrollment seal LSB marker validated")
                _process_log('verification', 'marker_ownership_validated', contract=matched_contract, request=request)

                try:
                    decrypted_original_cf = decrypt_cf(
                        extracted_encrypted,
                        matched_contract.wrapped_key,
                        matched_contract.aes_iv,
                        matched_contract.hmac_value,
                        settings.RSA_PRIVATE_KEY_PATH,
                    )
                except (FileNotFoundError, PermissionError, OSError):
                    raise
                except Exception as crypto_error:
                    debug_log.append("[FAIL] RSA/AES recovery or HMAC integrity validation failed")
                    _process_log(
                        'verification', 'cryptographic_validation_failed',
                        contract=matched_contract, request=request,
                        error_type=type(crypto_error).__name__,
                    )
                    failure_reason = 'cryptographic integrity validation failed'
                    result = 'tampered'
                else:
                    debug_log.append("[PASS] AES key recovered with RSA-OAEP")
                    debug_log.append("[PASS] Original fingerprint decrypted with AES-256-CBC and HMAC-SHA256 verified")

                    if not compare_digest(decrypted_original_cf, matched_contract.original_fingerprint):
                        debug_log.append("[FAIL] Decrypted source fingerprint does not match the contract enrollment record")
                        failure_reason = 'original fingerprint mismatch'
                        result = 'tampered'
                    else:
                        debug_log.append("[PASS] Decrypted fingerprint matches the source PDF fingerprint")
                        result = 'authentic'

        except Exception as e:
            debug_log.append(f"[ERROR] Verification stopped safely: {type(e).__name__}")
            _process_log('verification', 'failed', request=request, level='exception', error_type=type(e).__name__)
            result = 'error'

        finally:
            if os.path.isfile(temp_path):
                debug_log.append("[INFO] Uploaded PDF retained temporarily for result preview")

        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        debug_log.append(f"Verification outcome: {result or 'error'}")
        debug_log.append(f"Verification completed in {elapsed_ms} ms")
        _process_log(
            'verification', 'completed', contract=locals().get('matched_contract'),
            request=request, result=result or 'error', duration_ms=elapsed_ms,
        )

        if result == 'authentic':
            _log_public_verification(
                request, uploaded_file, 'authentic', contract=matched_contract,
                note=f"Public verification: authentic ({uploaded_file.name})",
                debug_log=debug_log,
            )
        elif result == 'tampered':
            tampering_log = _log_public_verification(
                request, uploaded_file, 'tampered', contract=matched_contract,
                note=f"Public verification: tampered ({uploaded_file.name}); {failure_reason or 'integrity check failed'}",
                debug_log=debug_log,
            )
            _attach_verification_evidence(tampering_log, temp_path, uploaded_file.name)
        elif result == 'not_found':
            _log_public_verification(
                request, uploaded_file, 'not_found',
                note=f"Public verification: no SealGuard record ({uploaded_file.name})",
                debug_log=debug_log,
            )
        else:
            _log_public_verification(
                request, uploaded_file, 'error',
                note=f"Public verification: unable to verify ({uploaded_file.name}); {failure_reason or 'operational error'}",
                debug_log=debug_log,
            )

        request.session['verify_result'] = result
        request.session['verify_debug_log'] = debug_log
        request.session['verify_filename_hint'] = filename_hint
        request.session['verify_filename'] = os.path.basename(uploaded_file.name or '')
        return redirect(reverse('public_verify'))

    # ── GET: read from session and clear ──
    result = request.session.pop('verify_result', None)
    debug_log = request.session.pop('verify_debug_log', [])
    filename_hint = request.session.pop('verify_filename_hint', None)
    verify_filename = request.session.pop('verify_filename', None)
    verification_timestamp = request.session.pop('verify_timestamp', None)
    preview_token = request.session.get('verify_preview_token')
    preview_url = reverse('verification_preview', args=[preview_token]) if result and preview_token else None

    public_contract_queryset = Contract.objects.filter(is_public=True, is_trashed=False).order_by('-uploaded_at', '-id')
    public_page_obj = Paginator(public_contract_queryset, 20).get_page(request.GET.get('page'))
    public_contracts = [
        # The public browser only needs display metadata; file verification
        # runs only after a PDF is submitted to the verification portal.
        {'contract': c, 'meta': get_contract_meta(c)}
        for c in public_page_obj.object_list
    ]

    return render(request, 'verify.html', {
        'result': result,
        'filename_hint': filename_hint,
        'debug_log': debug_log,
        'public_contracts': public_contracts,
        'public_page_obj': public_page_obj,
        'verify_preview_url': preview_url,
        'verify_filename': verify_filename,
        'verification_timestamp': verification_timestamp,
    })


def _contract_file_access(request, contract):
    allowed = can_view(request.user, contract)
    if not allowed and request.user.is_authenticated:
        raise Http404
    return allowed


@never_cache
def preview_contract(request, contract_id):
    started_at = time.perf_counter()
    contract = get_object_or_404(Contract, pk=contract_id, is_trashed=False)
    if not _contract_file_access(request, contract):
        return redirect(f"{reverse('login')}?next={request.path}")
    if not contract.file:
        raise Http404
    latest_version = contract.versions.order_by('-version_number').first()
    version_number = latest_version.version_number if latest_version else 1
    _process_log(
        'pdf_view', 'current_pdf_response_started', contract=contract, request=request,
        version=version_number, lookup_ms=round((time.perf_counter() - started_at) * 1000),
    )
    response = FileResponse(
        contract.file.open('rb'),
        content_type='application/pdf',
        filename=_contract_pdf_filename(contract, version_number),
    )
    if is_admin(request.user):
        response['X-SealGuard-PDF-Download'] = reverse('download_contract', args=[contract.pk]) + '?format=pdf'
    return response


@never_cache
def preview_contract_version(request, version_id):
    started_at = time.perf_counter()
    version = get_object_or_404(
        ContractVersion.objects.select_related('contract'),
        pk=version_id,
        contract__is_trashed=False,
    )
    if not _contract_file_access(request, version.contract):
        return redirect(f"{reverse('login')}?next={request.path}")
    if not version.file:
        raise Http404
    _process_log(
        'pdf_view', 'version_pdf_response_started', contract=version.contract, request=request,
        version=version.version_number, version_id=version.id,
        lookup_ms=round((time.perf_counter() - started_at) * 1000),
    )
    response = FileResponse(
        version.file.open('rb'),
        content_type='application/pdf',
        filename=_contract_pdf_filename(version.contract, version.version_number),
    )
    if is_admin(request.user):
        response['X-SealGuard-PDF-Download'] = reverse('download_contract_version', args=[version.pk]) + '?format=pdf'
    return response


@never_cache
def download_contract(request, contract_id):
    if request.GET.get('format') == 'pdf' and not is_admin(request.user):
        raise Http404
    if not request.user.is_authenticated:
        contract = Contract.objects.filter(pk=contract_id, is_trashed=False).first()
        if not contract or not contract.is_public:
            return redirect(f"{reverse('login')}?next={request.path}")
    else:
        contract = get_object_or_404(Contract, pk=contract_id, is_trashed=False)
    if not can_download(request.user, contract):
        return redirect(f"{reverse('login')}?next={request.path}")
    if not contract.file:
        raise Http404
    latest_version = contract.versions.order_by('-version_number').first()
    log_activity(
        request, 'downloaded', contract=contract,
        note='Downloaded current contract PDF',
        version_number=latest_version.version_number if latest_version else None,
    )
    if request.GET.get('format') == 'pdf' or (contract.is_public and (not request.user.is_authenticated or request.GET.get('public') == '1')):
        document = pdf_storage.open(contract.file.name, 'rb')
        response = FileResponse(
            document,
            content_type='application/pdf',
            as_attachment=True,
            filename=_contract_pdf_filename(
                contract,
                latest_version.version_number if latest_version else 1,
                short_title=request.GET.get('short_name') == '1',
            ),
        )
        response['Cache-Control'] = 'private, no-store'
        return response
    from .encrypted_documents import package_response
    return package_response(
        contract, contract.file,
        filename=_contract_pdf_filename(
            contract,
            latest_version.version_number if latest_version else 1,
            short_title=request.GET.get('short_name') == '1',
        ),
        version=latest_version if latest_version and latest_version.file.name == contract.file.name else None,
    )


@never_cache
def download_contract_version(request, version_id):
    if request.GET.get('format') == 'pdf' and not is_admin(request.user):
        raise Http404
    version = get_object_or_404(
        ContractVersion.objects.select_related('contract'),
        pk=version_id,
        contract__is_trashed=False,
    )
    if not can_download(request.user, version.contract):
        return redirect(f"{reverse('login')}?next={request.path}")
    if not version.file:
        raise Http404
    log_activity(
        request, 'downloaded', contract=version.contract,
        note=f'Downloaded version {version.version_number} PDF',
        version_number=version.version_number,
    )
    if request.GET.get('format') == 'pdf':
        return FileResponse(version.file.open('rb'), content_type='application/pdf',
                            as_attachment=True,
                            filename=_contract_pdf_filename(version.contract, version.version_number))
    from .encrypted_documents import package_response
    return package_response(
        version.contract, version.file,
        filename=_contract_pdf_filename(version.contract, version.version_number),
        version=version,
    )

# New rename view
@login_required
@document_access()
def rename_contract(request, pk):
    if request.method == 'POST':
        import json
        contract = get_object_or_404(Contract, pk=pk)
        data = json.loads(request.body)
        contract.title = data.get('title', contract.title)
        contract.save()
        log_activity(request, 'edited', contract=contract, note=f"Renamed to '{contract.title}'")
        return JsonResponse({'success': True, 'title': contract.title})
    return JsonResponse({'success': False}, status=400)

# New tag view
@login_required
@document_access()
def tag_contract(request, pk):
    if request.method == 'POST':
        import json
        contract = get_object_or_404(Contract, pk=pk)
        data = json.loads(request.body)
        contract.tags = data.get('tags', contract.tags)
        contract.save()
        log_activity(request, 'edited', contract=contract, note=f"Tags updated: '{contract.tags}'")
        return JsonResponse({'success': True, 'tags': contract.tags, 'tag_list': contract.tag_list()})
    return JsonResponse({'success': False}, status=400)

@login_required
@workspace_mutation
def create_folder(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:100] or 'Untitled Folder'
        last_order = Folder.objects.order_by('-sort_order').values_list('sort_order', flat=True).first()
        folder = Folder.objects.create(
            name=name,
            owner=request.user,
            sort_order=(last_order + 1) if last_order is not None else 0,
        )
        return JsonResponse({'success': True, 'id': folder.id, 'name': folder.name})
    return JsonResponse({'success': False}, status=400)

@login_required
@bulk_document_access
def bulk_permanently_delete_contracts(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    ids = request.POST.getlist('ids')
    contracts = Contract.objects.filter(id__in=ids, is_trashed=True)
    count = contracts.count()
    for contract in contracts:
        _hard_delete_contract(contract, request=request, note=f'Permanently deleted from trash: {contract.title}')
    return JsonResponse({'success': True, 'count': count})


@login_required
@workspace_mutation
def reorder_folders(request):
    if request.method != 'POST':
        return JsonResponse({'success': False}, status=400)

    import json
    try:
        folder_ids = [int(folder_id) for folder_id in json.loads(request.body).get('folder_ids', [])]
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'error': 'invalid_order'}, status=400)

    existing = list(Folder.objects.values_list('id', flat=True))
    if len(folder_ids) != len(set(folder_ids)) or sorted(folder_ids) != sorted(existing):
        return JsonResponse({'success': False, 'error': 'invalid_order'}, status=400)

    folders = {folder.id: folder for folder in Folder.objects.all()}
    for position, folder_id in enumerate(folder_ids):
        folders[folder_id].sort_order = position
    Folder.objects.bulk_update(folders.values(), ['sort_order'])
    return JsonResponse({'success': True})


@login_required
@workspace_mutation
def rename_folder(request, pk):
    if request.method == 'POST':
        import json
        folder = get_object_or_404(Folder, pk=pk)
        data = json.loads(request.body)
        new_name = data.get('name', '').strip()
        if new_name:
            folder.name = new_name
            folder.save()
        return JsonResponse({'success': True, 'name': folder.name})
    return JsonResponse({'success': False}, status=400)


@login_required
@document_access()
def assign_folder(request, contract_id):
    if request.method == 'POST':
        import json
        contract = get_object_or_404(Contract, pk=contract_id)
        data = json.loads(request.body)
        folder_id = data.get('folder_id')
        if folder_id:
            folder = get_object_or_404(Folder, pk=folder_id)
            contract.folder = folder
        else:
            contract.folder = None
        contract.save()
        return JsonResponse({'success': True, 'folder_id': contract.folder_id})
    return JsonResponse({'success': False}, status=400)

@login_required
@workspace_mutation
def delete_folder(request, pk):
    if request.method == 'POST':
        import json
        folder = get_object_or_404(Folder, pk=pk)
        if not is_admin(request.user) and folder.owner_id != request.user.pk:
            raise Http404
        if folder.contracts.exclude(pk__in=managed_contracts(request.user)).exists():
            raise Http404
        data = json.loads(request.body)
        mode = data.get('mode', 'unassign')  # 'unassign' or 'delete_items'
        has_items = folder.contracts.exists()

        if has_items and not request.user.has_perm('contracts.delete_contract'):
            return JsonResponse({'success': False, 'error': 'not_allowed'}, status=403)

        if mode == 'delete_items':
            if not request.user.has_perm('contracts.delete_contract'):
                return JsonResponse({'success': False, 'error': 'not_allowed'}, status=403)
            for contract in folder.contracts.all():
                for version in contract.versions.all():
                    if version.file:
                        version_path = os.path.join(settings.MEDIA_ROOT, str(version.file))
                        if os.path.isfile(version_path):
                            os.remove(version_path)
                if contract.seal_image:
                    seal_path = os.path.join(settings.MEDIA_ROOT, str(contract.seal_image))
                    if os.path.isfile(seal_path):
                        os.remove(seal_path)
                qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')
                if os.path.isfile(qr_path):
                    os.remove(qr_path)
                log_activity(request, 'deleted', contract=contract, note=contract.title)
                contract.delete()
        else:
            folder.contracts.update(folder=None)

        folder.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)
# New status view
@login_required
@document_access()
def update_status(request, pk):
    if not is_admin(request.user):
        return redirect('contract_list')
    if request.method == 'POST':
        contract = get_object_or_404(Contract, pk=pk)
        status = request.POST.get('status', contract.status)
        if status not in {'pending', 'final'}:
            return redirect('contract_list')
        contract.status = status
        if status == 'final':
            contract.is_public = True
        contract.save()
        if status == 'final':
            log_activity(request, 'approved', contract=contract)
    return redirect('contract_list')

@login_required
@document_access(share=True)
def publish_contract(request, pk):

    if request.method == 'POST':
        import json
        contract = get_object_or_404(Contract, pk=pk)
        data = json.loads(request.body)
        make_public = data.get('is_public', False)

        contract.is_public = make_public
        contract.save()

        log_activity(
            request, 'edited', contract=contract,
            note='Added to public verified contracts' if make_public else 'Removed from public verified contracts'
        )
        return JsonResponse({'success': True, 'is_public': contract.is_public})
    return JsonResponse({'success': False}, status=400)
from django.db.models import Count, Q


@login_required
@workspace_access
def dashboard(request):
    total_documents = viewable_contracts(request.user).filter(is_trashed=False).count()
    verified_documents = viewable_contracts(request.user).filter(is_trashed=False, encrypted_cf__gt='').count()
    flagged_documents = visible_logs(request.user).filter(action='reported_tampering').count()

    valid_activity_filters = ['viewed', 'downloaded', 'added', 'encrypted', 'edited', 'approved', 'rejected', 'deleted', 'reported_tampering', 'verification', 'failed_login', 'login', 'logout', 'locked_out']
    requested_activity_filters = [
        value.strip()
        for raw_value in request.GET.getlist('activity')
        for value in raw_value.split(',')
        if value.strip()
    ]
    activity_filters = list(dict.fromkeys(
        value for value in requested_activity_filters if value in valid_activity_filters
    ))
    activity_labels = dict(AuditLog.ACTION_CHOICES)
    if not activity_filters or set(activity_filters) == set(valid_activity_filters):
        activity_filters = valid_activity_filters
        activity_filter = 'all'
        activity_filter_label = 'All activity'
    elif len(activity_filters) == 1:
        activity_filter = activity_filters[0]
        activity_filter_label = activity_labels[activity_filters[0]]
    else:
        activity_filter = ','.join(activity_filters)
        activity_filter_label = f'{len(activity_filters)} activities selected'

    sort_order = request.GET.get('sort', 'newest')
    if sort_order not in {'newest', 'oldest'}:
        sort_order = 'newest'

    recent_logs = visible_logs(request.user).select_related('user', 'contract')
    if activity_filters:
        recent_logs = recent_logs.filter(action__in=activity_filters)
    date_from = parse_date(request.GET.get('date_from', '').strip())
    date_to = parse_date(request.GET.get('date_to', '').strip())
    if date_from:
        recent_logs = recent_logs.filter(timestamp__date__gte=date_from)
    if date_to:
        recent_logs = recent_logs.filter(timestamp__date__lte=date_to)
    user_id = request.GET.get('user', '').strip()
    if user_id.isdigit():
        recent_logs = recent_logs.filter(user_id=int(user_id))
    status = request.GET.get('status', '').strip()
    if status in {'pending', 'final'}:
        recent_logs = recent_logs.filter(contract__status=status)
    document_query = request.GET.get('document', '').strip()
    if document_query:
        recent_logs = recent_logs.filter(
            Q(document_title__icontains=document_query)
            | Q(contract__title__icontains=document_query)
        )
    if sort_order == 'oldest':
        recent_logs = recent_logs.order_by('timestamp', 'id')
    else:
        recent_logs = recent_logs.order_by('-timestamp', '-id')

    try:
        rows_per_page = int(request.GET.get('per_page', 20))
    except (TypeError, ValueError):
        rows_per_page = 20
    if rows_per_page not in {10, 20, 50, 100}:
        rows_per_page = 20

    paginator = Paginator(recent_logs, rows_per_page)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Older activity rows were recorded before viewed events stored their
    # version number. Infer the version that existed when that activity took
    # place so historical rows remain useful instead of showing "Current".
    for log in page_obj:
        log.display_version_number = log.version_number
        if not log.display_version_number and log.contract_id:
            historical_version = log.contract.versions.filter(
                created_at__lte=log.timestamp,
            ).order_by('-version_number').first()
            if historical_version:
                log.display_version_number = historical_version.version_number

    return render(request, 'dashboard.html', {
        'total_documents': total_documents,
        'verified_documents': verified_documents,
        'flagged_documents': flagged_documents,
        'recent_logs': page_obj,
        'page_obj': page_obj,
        'activity_filter': activity_filter,
        'activity_filters': activity_filters,
        'activity_filter_label': activity_filter_label,
        'sort_order': sort_order,
        'rows_per_page': rows_per_page,
        'report_users': User.objects.filter(is_active=True).order_by('username'),
        'report_date_from': request.GET.get('date_from', ''),
        'report_date_to': request.GET.get('date_to', ''),
        'report_user': user_id,
        'report_status': status,
        'report_document': document_query,
        'read_only_user': is_read_only_user(request.user),
        'workspace_role': 'Admin' if is_admin(request.user) else ('Staff' if request.user.is_staff else 'User'),
    })


@login_required
@workspace_mutation
def export_dashboard_report(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="sealguard-audit-report.csv"'
    writer = csv.writer(response)
    writer.writerow(['Document', 'User', 'Action', 'Timestamp', 'IP address', 'Details'])

    valid_actions = [choice[0] for choice in AuditLog.ACTION_CHOICES]
    requested_actions = [
        value.strip()
        for raw_value in request.GET.getlist('activity')
        for value in raw_value.split(',')
        if value.strip() in valid_actions
    ]
    logs = visible_logs(request.user).select_related('user', 'contract')
    if requested_actions and 'all' not in requested_actions:
        logs = logs.filter(action__in=list(dict.fromkeys(requested_actions)))
    date_from = parse_date(request.GET.get('date_from', '').strip())
    date_to = parse_date(request.GET.get('date_to', '').strip())
    if date_from:
        logs = logs.filter(timestamp__date__gte=date_from)
    if date_to:
        logs = logs.filter(timestamp__date__lte=date_to)
    user_id = request.GET.get('user', '').strip()
    if user_id.isdigit():
        logs = logs.filter(user_id=int(user_id))
    status = request.GET.get('status', '').strip()
    if status in {'pending', 'final'}:
        logs = logs.filter(contract__status=status)
    document_query = request.GET.get('document', '').strip()
    if document_query:
        logs = logs.filter(
            Q(document_title__icontains=document_query)
            | Q(contract__title__icontains=document_query)
        )
    logs = logs.order_by('-timestamp', '-id')
    for log in logs:
        writer.writerow([
            log.display_document_title,
            log.user.username if log.user else 'N/A',
            log.get_action_display(),
            log.timestamp.isoformat(),
            log.ip_address or '',
            log.note or '',
        ])
    return response


@login_required
@document_access(share=True)
def contract_access(request, contract_id):
    """Owners and administrators grant or revoke document editing access."""
    from django.db import transaction
    contract = get_object_or_404(Contract, pk=contract_id, is_trashed=False)
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        action = request.POST.get('action')
        if action not in {'grant', 'revoke'}:
            return JsonResponse({'error': 'Choose grant or revoke.'}, status=400)
        target = User.objects.filter(username=username).first()
        if not target or (action == 'grant' and not target.is_active):
            return JsonResponse({'error': 'No active account has that username.'}, status=400)
        if is_admin(target) or target.pk == contract.uploaded_by_id:
            return JsonResponse({'error': 'The uploader and administrators already have access.'}, status=400)
        with transaction.atomic():
            if action == 'grant':
                contract.collaborators.add(target)
            else:
                contract.collaborators.remove(target)
            log_activity(request, 'edited', contract=contract,
                         note=f'Document access {"granted to" if action == "grant" else "revoked from"} {target.username}')
    elif request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    collaborator_ids = set(contract.collaborators.values_list('id', flat=True))
    accounts = [
        {
            'username': account.username,
            'created_at': timezone.localtime(account.date_joined).strftime('%b %d, %Y'),
            'has_access': account.id in collaborator_ids,
        }
        for account in User.objects.filter(is_active=True, is_superuser=False)
        .exclude(pk=contract.uploaded_by_id).order_by('username')
    ]
    response = JsonResponse({
        'owner': contract.uploaded_by_display or 'Administrator-managed document',
        'users': list(contract.collaborators.order_by('username').values('username')),
        'accounts': accounts,
    })
    response['Cache-Control'] = 'private, no-store'
    return response
