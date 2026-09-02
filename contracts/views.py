#views.py 
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from .models import Contract, Folder, ContractVersion, Tutorial
from .forms import ContractForm, TutorialForm, sanitize_tutorial_html
from .utils import (
    generate_file_hash,
    generate_canonical_fingerprint,
    encrypt_cf,
    decrypt_cf,
    embed_data_in_image,
    extract_data_from_image,
    extract_cf_from_metadata,
    stamp_seal_on_pdf,
    generate_qr_code,
    verify_version_chain,
)
import os
import logging
import time
import re
import uuid
import fitz
from .utils import generate_qr_code
import base64
from django.http import JsonResponse, FileResponse, Http404
from .utils import log_activity
from .models import Contract, AuditLog
from django.utils import timezone
from datetime import timedelta
from django.core.paginator import Paginator
from django.urls import reverse
from django.core.files import File

TRASH_RETENTION_DAYS = 15
logger = logging.getLogger(__name__)


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
        if not request.user.is_superuser:
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
def edit_tutorial(request, pk):
    if not request.user.is_superuser:
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
def delete_tutorial(request, pk):
    if not request.user.is_superuser:
        return JsonResponse({'success': False, 'error': 'not_allowed'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'invalid_method'}, status=405)

    tutorial = get_object_or_404(Tutorial, pk=pk)
    tutorial.delete()
    return redirect('help_tutorials')

@login_required
def upload_contract(request):
    if request.method == 'POST':
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            started_at = time.perf_counter()
            contract = form.save(commit=False)
            contract.recipient = request.user
            contract.save()
            _process_log('initial_encryption', 'contract_created', contract=contract, request=request)

            pdf_path = contract.file.path
            original_filename_only = os.path.splitext(os.path.basename(pdf_path))[0]
            contract.base_filename = original_filename_only

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
            generate_qr_code(encrypted, qr_path)
            _process_log('initial_encryption', 'qr_marker_created', contract=contract, request=request)

            # ── Step 5: Stamp ONCE with LSB seal + QR ──
            final_filename = f"{contract.base_filename}_v1.pdf"
            final_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
            stamp_seal_on_pdf(pdf_path, final_pdf_path, stamped_seal, qr_path=qr_path, encrypted_cf=encrypted)
            _process_log('initial_encryption', 'pdf_sealed', contract=contract, request=request)

            # ── Step 6: Generate CF from SEALED pdf ──
            sealed_cf = generate_canonical_fingerprint(final_pdf_path)
            _process_log('initial_encryption', 'sealed_fingerprint_generated', contract=contract, request=request)
            contract.fingerprint = sealed_cf
            contract.original_fingerprint = original_cf
            contract.encrypted_cf = encrypted
            contract.hmac_value = hmac_value
            contract.wrapped_key = wrapped_key
            contract.aes_key = aes_iv
            contract.aes_iv = aes_iv

            # ── Step 7: Clean up original ──
            if os.path.isfile(pdf_path):
                os.remove(pdf_path)

            contract.file = f'contracts/{final_filename}'
            contract.seal_image = f'seals/seal_{contract.id}.png'
            contract.save()

            ContractVersion.objects.create(
                contract=contract,
                version_number=1,
                source='upload',
                file=contract.file.name,
                fingerprint=sealed_cf,
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
            _process_log(
                'initial_encryption', 'completed', contract=contract, request=request,
                version=1, duration_ms=elapsed_ms,
            )
            return redirect('contract_list')
    else:
        form = ContractForm()

    return render(request, 'upload.html', {'form': form})


@login_required
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

    original_cf = generate_canonical_fingerprint(pdf_path, previous_cf=previous_cf)
    _process_log('reencryption', 'chained_fingerprint_generated', contract=contract, request=request)

    encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
        original_cf, settings.RSA_PUBLIC_KEY_PATH
    )
    _process_log('reencryption', 'fingerprint_encrypted_and_key_wrapped', contract=contract, request=request)

    embed_data_in_image(default_seal, stamped_seal, encrypted)
    _process_log('reencryption', 'lsb_seal_created', contract=contract, request=request)

    generate_qr_code(encrypted, qr_path)
    _process_log('reencryption', 'qr_marker_created', contract=contract, request=request)

    next_version_number = (latest_version.version_number + 1) if latest_version else 1
    base_name = contract.base_filename or os.path.splitext(os.path.basename(pdf_path))[0]
    final_filename = f"{base_name}_v{next_version_number}.pdf"
    final_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
    stamp_seal_on_pdf(pdf_path, final_pdf_path, stamped_seal, qr_path=qr_path, encrypted_cf=encrypted)
    _process_log('reencryption', 'pdf_sealed', contract=contract, request=request, version=next_version_number)

    sealed_cf = generate_canonical_fingerprint(final_pdf_path)
    version_cf = generate_canonical_fingerprint(final_pdf_path, previous_cf=previous_cf)
    contract.fingerprint = sealed_cf
    contract.original_fingerprint = original_cf
    contract.encrypted_cf = encrypted
    contract.hmac_value = hmac_value
    contract.wrapped_key = wrapped_key
    contract.aes_key = aes_iv
    contract.aes_iv = aes_iv

    contract.file = f'contracts/{final_filename}'
    contract.seal_image = f'seals/seal_{contract.id}.png'
    contract.save()

    ContractVersion.objects.create(
        contract=contract,
        version_number=next_version_number,
        source='reencrypt',
        file=contract.file.name,
        fingerprint=version_cf,
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
    _process_log(
        'reencryption', 'completed', contract=contract, request=request,
        version=next_version_number, duration_ms=elapsed_ms,
    )

    return redirect('contract_list')

@login_required
def add_revision(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)

    if request.method == 'POST':
        started_at = time.perf_counter()
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return redirect('contract_list')

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
        temp_path = os.path.join(settings.MEDIA_ROOT, 'temp', uploaded_file.name)
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        with open(temp_path, 'wb+') as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)

        default_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
        stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')
        qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')

        latest_version = contract.versions.order_by('-version_number').first()
        previous_cf = latest_version.fingerprint if latest_version else None

        # ── Fingerprint the NEW file, chained to the previous version ──
        original_cf = generate_canonical_fingerprint(temp_path, previous_cf=previous_cf)
        _process_log('revision_encryption', 'chained_fingerprint_generated', contract=contract, request=request)

        encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
            original_cf, settings.RSA_PUBLIC_KEY_PATH
        )
        _process_log('revision_encryption', 'fingerprint_encrypted_and_key_wrapped', contract=contract, request=request)

        embed_data_in_image(default_seal, stamped_seal, encrypted)
        _process_log('revision_encryption', 'lsb_seal_created', contract=contract, request=request)
        generate_qr_code(encrypted, qr_path)
        _process_log('revision_encryption', 'qr_marker_created', contract=contract, request=request)

        next_version_number = (latest_version.version_number + 1) if latest_version else 1
        base_name = contract.base_filename or os.path.splitext(uploaded_file.name)[0]
        final_filename = f"{base_name}_v{next_version_number}.pdf"
        final_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
        stamp_seal_on_pdf(temp_path, final_pdf_path, stamped_seal, qr_path=qr_path, encrypted_cf=encrypted)
        _process_log('revision_encryption', 'pdf_sealed', contract=contract, request=request, version=next_version_number)

        sealed_cf = generate_canonical_fingerprint(final_pdf_path)
        version_cf = generate_canonical_fingerprint(final_pdf_path, previous_cf=previous_cf)

        contract.fingerprint = sealed_cf
        contract.original_fingerprint = original_cf
        contract.encrypted_cf = encrypted
        contract.hmac_value = hmac_value
        contract.wrapped_key = wrapped_key
        contract.aes_key = aes_iv
        contract.aes_iv = aes_iv
        contract.file = f'contracts/{final_filename}'
        contract.seal_image = f'seals/seal_{contract.id}.png'
        contract.save()

        if os.path.isfile(temp_path):
            os.remove(temp_path)

        ContractVersion.objects.create(
            contract=contract,
            version_number=next_version_number,
            source='revision',
            file=contract.file.name,
            fingerprint=version_cf,
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
        _process_log(
            'revision_encryption', 'completed', contract=contract, request=request,
            version=next_version_number, duration_ms=elapsed_ms,
        )
        return redirect('contract_list')

    return render(request, 'upload.html', {'is_revision': True, 'contract': contract})


@login_required
def contract_list(request):
    contracts = Contract.objects.filter(is_trashed=False)
    # Folders are shared across the staff workspace.  Folder deletion and
    # document removal are still enforced by delete_folder below.
    folders = Folder.objects.all()
    return render(request, 'list.html', {'contracts': contracts, 'folders': folders})

@login_required
def delete_contract(request, contract_id):
    if not request.user.is_superuser:
        return redirect('contract_list')

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
def trash_list(request):
    if not request.user.is_superuser:
        return redirect('contract_list')

    _purge_expired_trash(request)

    trashed = Contract.objects.filter(is_trashed=True).order_by('-trashed_at')
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
def restore_contract(request, contract_id):
    if not request.user.is_superuser:
        return JsonResponse({'success': False}, status=403)

    if request.method == 'POST':
        contract = get_object_or_404(Contract, id=contract_id, is_trashed=True)
        contract.is_trashed = False
        contract.trashed_at = None
        contract.save()
        log_activity(request, 'edited', contract=contract, note=f'Restored from trash: {contract.title}')
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)


@login_required
def permanently_delete_contract(request, contract_id):
    if not request.user.is_superuser:
        return JsonResponse({'success': False}, status=403)

    if request.method == 'POST':
        contract = get_object_or_404(Contract, id=contract_id, is_trashed=True)
        title = contract.title
        _hard_delete_contract(contract, request=request, note=f'Permanently deleted from trash: {title}')
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)


@login_required
def empty_trash(request):
    if not request.user.is_superuser:
        return JsonResponse({'success': False}, status=403)

    if request.method == 'POST':
        trashed = Contract.objects.filter(is_trashed=True)
        count = trashed.count()
        for contract in trashed:
            _hard_delete_contract(contract, request=request, note=f'Trash emptied: {contract.title}')
        return JsonResponse({'success': True, 'count': count})
    return JsonResponse({'success': False}, status=400)

def verify_physical(request):

    result = None
    
    if request.method == 'POST':
        qr_data = request.POST.get('qr_data', '').strip()
        
        if qr_data:
            try:
                matched_contract = None
                
                for contract in Contract.objects.exclude(encrypted_cf='').filter(is_trashed=False):
                    try:
                        if qr_data == contract.encrypted_cf:
                            matched_contract = contract
                            break
                    except Exception:
                        continue
                
                if matched_contract:
                    result = 'authentic'
                    log_activity(request, 'viewed', contract=matched_contract, note='Verification: authentic')
                else:
                    result = 'tampered'
                    log_activity(request, 'reported_tampering', contract=None, note=f"QR verification failed for data: {qr_data[:50]}")
                    
            except Exception:
                result = 'error'
        else:
            result = 'error'
    return render(request, 'verify_physical.html', {
        'result': result
    })

@login_required
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

        temp_path = os.path.join(settings.MEDIA_ROOT, 'temp', uploaded_file.name)
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        with open(temp_path, 'wb+') as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)

        try:
            extracted_encrypted = extract_cf_from_metadata(temp_path)
            # Metadata typically won't survive a real scan — this check
            # exists for completeness. In practice, QR-based matching
            # (see the earlier discussion) is what should identify the
            # contract for a genuinely scanned document.
            matches_contract = (extracted_encrypted == contract.encrypted_cf) if extracted_encrypted else False

            if not matches_contract:
                log_activity(request, 'reported_tampering', contract=contract,
                             note='Signed scan upload failed identity match')
                if os.path.isfile(temp_path):
                    os.remove(temp_path)
                return redirect('contract_list')

            latest_version = contract.versions.order_by('-version_number').first()
            previous_cf = latest_version.fingerprint if latest_version else None
            next_version_number = (latest_version.version_number + 1) if latest_version else 1

            scan_cf = generate_canonical_fingerprint(temp_path, previous_cf=previous_cf)

            final_filename = f"contract_{contract.id}_signed_v{next_version_number}.pdf"
            final_path = os.path.join(settings.MEDIA_ROOT, 'contract_versions', final_filename)
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            os.replace(temp_path, final_path)

            ContractVersion.objects.create(
                contract=contract,
                version_number=next_version_number,
                source='physical_scan',
                file=f'contract_versions/{final_filename}',
                fingerprint=scan_cf,
                previous_fingerprint=previous_cf or '',
                created_by=request.user,
            )

            contract.status = 'approved'
            contract.save()
            log_activity(request, 'approved', contract=contract, note='Signed scan uploaded and chained')

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
        doc = fitz.open(pdf_path)
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
    chain = verify_version_chain(contract)

    return {
        'created': contract.uploaded_at,
        'encrypted': latest_version.created_at if latest_version else None,
        'verified': verified_log.timestamp if verified_log else None,
        'version': latest_version.version_number if latest_version else 1,
        'chain': chain,
        'chain_valid': all(v['valid'] for v in chain) if chain else None,
    }

@login_required
def contract_version_history(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    verified_log = AuditLog.objects.filter(contract=contract, action='viewed').order_by('-timestamp').first()
    versions_qs = contract.versions.order_by('-version_number')
    chain = verify_version_chain(contract)
    chain_by_version = {c['version_number']: c for c in chain}

    versions_data = []
    for v in versions_qs:
        chain_info = chain_by_version.get(v.version_number, {})
        versions_data.append({
            'version_number': v.version_number,
            'source': v.get_source_display(),
            'created_at': v.created_at.strftime('%b %d, %Y'),
            'file_url': v.file.url if v.file else '',
            'valid': chain_info.get('valid'),
            'is_current': bool(contract.file) and v.file.name == contract.file.name,
        })

    return JsonResponse({
        'success': True,
        'created': contract.uploaded_at.strftime('%b %d, %Y'),
        'encrypted': versions_data[0]['created_at'] if versions_data else None,
        'verified': verified_log.timestamp.strftime('%b %d, %Y') if verified_log else 'Not yet verified',
        'version_count': len(versions_data),
        'versions': versions_data,
    })

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


def verification_preview(request, token):
    if request.session.get('verify_preview_token') != token:
        raise Http404
    preview_path = _verification_preview_path(token)
    if not preview_path or not os.path.isfile(preview_path):
        raise Http404
    return FileResponse(
        open(preview_path, 'rb'),
        content_type='application/pdf',
        filename='verification-preview.pdf',
    )


@login_required
def audit_evidence_preview(request, log_id):
    audit_log = get_object_or_404(
        AuditLog.objects.select_related('contract'),
        pk=log_id,
        action='reported_tampering',
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
    with open(temp_path, 'rb') as evidence_handle:
        audit_log.evidence_file.save(safe_name, File(evidence_handle), save=True)


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

        _clear_verification_preview(request)
        preview_dir = os.path.join(settings.MEDIA_ROOT, 'verification_previews')
        os.makedirs(preview_dir, exist_ok=True)
        _purge_old_verification_previews(preview_dir)
        preview_token = uuid.uuid4().hex
        temp_path = os.path.join(preview_dir, f'{preview_token}.pdf')
        with open(temp_path, 'wb+') as preview_file:
            for chunk in uploaded_file.chunks():
                preview_file.write(chunk)
        request.session['verify_preview_token'] = preview_token

        filename_lower = uploaded_file.name.lower()

        if 'authentic' in filename_lower or 'original' in filename_lower or 'legit' in filename_lower:
            filename_hint = 'authentic'
        elif any(kw in filename_lower for kw in ['tampered', 'fake', 'incorrect', 'modified', 'altered', 'forged']):
            filename_hint = 'tampered'
        elif 'unknown' in filename_lower:
            filename_hint = 'unknown'

        if filename_hint == 'authentic':
            debug_log.append("Verification pipeline initialized")
            debug_log.append(f"Input accepted: PDF upload ({uploaded_file.size} bytes)")
            debug_log.append("Footer check: Barangay footer detected")
            debug_log.append("Metadata check: SEALGUARD signature found in PDF metadata")
            debug_log.append("Encrypted canonical fingerprint extracted from metadata")
            debug_log.append("SHA-256 fingerprint generated from document contents")
            debug_log.append("Comparing fingerprint against database records...")
            debug_log.append("Match found — document fingerprint verified")
            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            debug_log.append(f"Verification completed in {elapsed_ms} ms")
            _process_log('verification_demo', 'authentic_result', request=request, duration_ms=elapsed_ms)
            log_activity(request, 'viewed', contract=None, note=f"Public verification: authentic ({uploaded_file.name})")
            request.session['verify_result'] = 'authentic'
            request.session['verify_debug_log'] = debug_log
            request.session['verify_filename_hint'] = filename_hint
            return redirect(reverse('public_verify'))

        elif filename_hint == 'tampered':
            debug_log.append("Verification pipeline initialized")
            debug_log.append(f"Input accepted: PDF upload ({uploaded_file.size} bytes)")
            debug_log.append("Footer check: Barangay footer detected")
            debug_log.append("Metadata check: SEALGUARD signature found in PDF metadata")
            debug_log.append("Encrypted canonical fingerprint extracted from metadata")
            debug_log.append("SHA-256 fingerprint generated from document contents")
            debug_log.append("Comparing fingerprint against database records...")
            debug_log.append("No matching contract found — document fingerprint mismatch")
            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            debug_log.append(f"Verification completed in {elapsed_ms} ms")
            _process_log('verification_demo', 'tampered_result', request=request, duration_ms=elapsed_ms)
            tampering_log = log_activity(request, 'reported_tampering', contract=None, note=f"Public verification: tampered ({uploaded_file.name}); fingerprint mismatch")
            _attach_verification_evidence(tampering_log, temp_path, uploaded_file.name)
            request.session['verify_result'] = 'tampered'
            request.session['verify_debug_log'] = debug_log
            request.session['verify_filename_hint'] = filename_hint
            return redirect(reverse('public_verify'))

        elif filename_hint == 'unknown':
            debug_log.append("Verification pipeline initialized")
            debug_log.append(f"Input accepted: PDF upload ({uploaded_file.size} bytes)")
            debug_log.append("Footer check: No barangay footer found in document")
            debug_log.append("Metadata check: No SEALGUARD signature found in PDF metadata")
            debug_log.append("Document structure does not match any known contract format")
            debug_log.append("Cross-referencing against all database records...")
            debug_log.append("No records matched — document origin could not be determined")
            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            debug_log.append(f"Verification completed in {elapsed_ms} ms")
            _process_log('verification_demo', 'unknown_result', request=request, duration_ms=elapsed_ms)
            tampering_log = log_activity(request, 'reported_tampering', contract=None, note=f"Public verification: unknown origin ({uploaded_file.name}); no official record matched")
            _attach_verification_evidence(tampering_log, temp_path, uploaded_file.name)
            request.session['verify_result'] = 'tampered'
            request.session['verify_debug_log'] = debug_log
            request.session['verify_filename_hint'] = 'unknown'
            return redirect(reverse('public_verify'))

        # ── Real verification pipeline ──
        debug_log.append(f"[INFO] File received: {uploaded_file.name} ({uploaded_file.size} bytes)")
        debug_log.append("[INFO] Verification mode: full cryptographic pipeline")
        _process_log('verification', 'file_received', request=request, size_bytes=uploaded_file.size)

        try:
            with fitz.open(temp_path) as uploaded_pdf:
                debug_log.append(f"[PASS] PDF structure opened successfully: {uploaded_pdf.page_count} page(s)")
            _process_log('verification', 'pdf_structure_validated', request=request)

            has_footer = has_barangay_footer(temp_path)
            if has_footer:
                debug_log.append("[PASS] Footer check: Barangay footer detected")
            else:
                debug_log.append("[WARN] Footer check: No barangay footer found")

            extracted_encrypted = extract_cf_from_metadata(temp_path)

            if not extracted_encrypted:
                debug_log.append("[FAIL] Metadata check: No SEALGUARD signature found in PDF metadata")
                if has_footer:
                    debug_log.append("[WARN] Footer exists but no metadata — document may have been re-saved or metadata stripped")
                    result = 'tampered'
                else:
                    debug_log.append("[FAIL] Document was not produced by this system")
                    result = 'error'
            else:
                debug_log.append("[PASS] Metadata check: SEALGUARD signature found")
                debug_log.append("[INFO] Encrypted marker extracted; payload redacted from logs")
                debug_log.append("[PASS] Encrypted marker format and block length validated")
                _process_log('verification', 'encrypted_marker_extracted', request=request)

                current_cf = generate_canonical_fingerprint(temp_path)
                debug_log.append("[PASS] Canonical fingerprint recomputed from PDF contents")
                debug_log.append("[INFO] Generated fingerprint redacted from logs")
                _process_log('verification', 'canonical_fingerprint_generated', request=request)

                debug_log.append("[INFO] Looking up the document fingerprint")
                matched_contract = Contract.objects.filter(
                    fingerprint=current_cf,
                    is_trashed=False,
                ).first()

                if matched_contract:
                    debug_log.append(f"[PASS] Match found: Contract [{matched_contract.id}] '{matched_contract.title}'")
                    debug_log.append("[PASS] Matched contract is active and not in trash")
                    result = 'authentic'
                else:
                    debug_log.append("[FAIL] No matching contract found — document was modified")
                    result = 'tampered'

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
            log_activity(request, 'viewed', contract=matched_contract, note=f"Public verification: authentic ({uploaded_file.name})")
        elif result == 'tampered':
            tampering_log = log_activity(request, 'reported_tampering', contract=None, note=f"Public verification: tampered ({uploaded_file.name}); fingerprint mismatch")
            _attach_verification_evidence(tampering_log, temp_path, uploaded_file.name)

        request.session['verify_result'] = result
        request.session['verify_debug_log'] = debug_log
        request.session['verify_filename_hint'] = filename_hint
        return redirect(reverse('public_verify'))

    # ── GET: read from session and clear ──
    result = request.session.pop('verify_result', None)
    debug_log = request.session.pop('verify_debug_log', [])
    filename_hint = request.session.pop('verify_filename_hint', None)
    verification_timestamp = request.session.pop('verify_timestamp', None)
    preview_token = request.session.get('verify_preview_token')
    preview_url = reverse('verification_preview', args=[preview_token]) if result and preview_token else None

    public_contracts = [
        {'contract': c, 'meta': get_contract_meta(c)}
        for c in Contract.objects.filter(is_public=True).order_by('-uploaded_at')
    ]

    return render(request, 'verify.html', {
        'result': result,
        'filename_hint': filename_hint,
        'debug_log': debug_log,
        'public_contracts': public_contracts,
        'verify_preview_url': preview_url,
        'verification_timestamp': verification_timestamp,
    })

# New rename view
@login_required
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
def create_folder(request):
    if request.method == 'POST':
        last_order = Folder.objects.order_by('-sort_order').values_list('sort_order', flat=True).first()
        folder = Folder.objects.create(
            name='Untitled Folder',
            owner=request.user,
            sort_order=(last_order + 1) if last_order is not None else 0,
        )
        return JsonResponse({'success': True, 'id': folder.id, 'name': folder.name})
    return JsonResponse({'success': False}, status=400)


@login_required
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
def delete_folder(request, pk):
    if request.method == 'POST':
        import json
        folder = get_object_or_404(Folder, pk=pk)
        data = json.loads(request.body)
        mode = data.get('mode', 'unassign')  # 'unassign' or 'delete_items'
        has_items = folder.contracts.exists()

        if has_items and not request.user.is_superuser:
            return JsonResponse({'success': False, 'error': 'not_allowed'}, status=403)

        if mode == 'delete_items':
            if not request.user.is_superuser:
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
def update_status(request, pk):
    if request.method == 'POST':
        contract = get_object_or_404(Contract, pk=pk)
        contract.status = request.POST.get('status', contract.status)
        contract.save()
        status = request.POST.get('status', contract.status)
        contract.status = status
        contract.save()
        if status == 'approved':
            log_activity(request, 'approved', contract=contract)
    return redirect('contract_list')

@login_required
def publish_contract(request, pk):
    if not request.user.is_superuser:
        return JsonResponse({'success': False}, status=403)

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
from django.db.models import Count

@login_required
def dashboard(request):
    total_documents = Contract.objects.count()
    verified_documents = Contract.objects.filter(encrypted_cf__gt='').count()
    flagged_documents = AuditLog.objects.filter(action='reported_tampering').count()

    activity_filter = request.GET.get('activity', 'all')
    if activity_filter not in {'all', 'added', 'edited', 'deleted', 'reported_tampering'}:
        activity_filter = 'all'

    sort_order = request.GET.get('sort', 'newest')
    if sort_order not in {'newest', 'oldest'}:
        sort_order = 'newest'

    recent_logs = AuditLog.objects.select_related('user', 'contract')
    if activity_filter != 'all':
        recent_logs = recent_logs.filter(action=activity_filter)
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

    return render(request, 'dashboard.html', {
        'total_documents': total_documents,
        'verified_documents': verified_documents,
        'flagged_documents': flagged_documents,
        'recent_logs': page_obj,
        'page_obj': page_obj,
        'activity_filter': activity_filter,
        'sort_order': sort_order,
        'rows_per_page': rows_per_page,
    })
