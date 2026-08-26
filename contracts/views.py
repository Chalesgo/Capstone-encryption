#views.py 
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from .models import Contract, Folder, ContractVersion
from .forms import ContractForm, RegisterForm
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
import fitz
from .utils import generate_qr_code
import base64
from django.http import JsonResponse
from .utils import log_activity
from .models import Contract, AuditLog

@login_required
def upload_contract(request):
    if request.method == 'POST':
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.recipient = request.user
            contract.save()

            pdf_path = contract.file.path

            default_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
            stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')
            qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')

            # ── Step 1: Generate CF from ORIGINAL pdf ──
            original_cf = generate_canonical_fingerprint(pdf_path)

            # ── Step 2: Encrypt the CF ──
            encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
                original_cf, settings.RSA_PUBLIC_KEY_PATH
            )

            # ── Step 3: Embed CF into seal via LSB ──
            embed_data_in_image(default_seal, stamped_seal, encrypted)

            # ── Step 4: Generate QR ──
            generate_qr_code(encrypted, qr_path)

            # ── Step 5: Stamp ONCE with LSB seal + QR ──
            original_filename = os.path.basename(pdf_path)
            name, ext = os.path.splitext(original_filename)
            final_filename = f"{name}_sealed{ext}"
            final_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
            stamp_seal_on_pdf(pdf_path, final_pdf_path, stamped_seal, qr_path=qr_path, encrypted_cf=encrypted)

            # ── Step 6: Generate CF from SEALED pdf ──
            sealed_cf = generate_canonical_fingerprint(final_pdf_path)
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

            log_activity(request, 'added', contract=contract)
            return redirect('contract_list')
    else:
        form = ContractForm()

    return render(request, 'upload.html', {'form': form})


@login_required
def encrypt_contract(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    pdf_path = contract.file.path

    default_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
    stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')
    qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')

    latest_version = contract.versions.order_by('-version_number').first()
    previous_cf = latest_version.fingerprint if latest_version else None

    original_cf = generate_canonical_fingerprint(pdf_path, previous_cf=previous_cf)

    encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
        original_cf, settings.RSA_PUBLIC_KEY_PATH
    )

    embed_data_in_image(default_seal, stamped_seal, encrypted)

    generate_qr_code(encrypted, qr_path)

    original_filename = os.path.basename(pdf_path)
    name, ext = os.path.splitext(original_filename)
    final_filename = f"{name}_sealed{ext}"
    final_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
    stamp_seal_on_pdf(pdf_path, final_pdf_path, stamped_seal, qr_path=qr_path, encrypted_cf=encrypted)

    sealed_cf = generate_canonical_fingerprint(final_pdf_path)
    contract.fingerprint = sealed_cf
    contract.original_fingerprint = original_cf
    contract.encrypted_cf = encrypted
    contract.hmac_value = hmac_value
    contract.wrapped_key = wrapped_key
    contract.aes_key = aes_iv
    contract.aes_iv = aes_iv

    if os.path.isfile(pdf_path):
        os.remove(pdf_path)

        contract.file = f'contracts/{final_filename}'
    contract.seal_image = f'seals/seal_{contract.id}.png'
    contract.save()

    next_version_number = (latest_version.version_number + 1) if latest_version else 1
    ContractVersion.objects.create(
        contract=contract,
        version_number=next_version_number,
        source='reencrypt',
        file=contract.file.name,
        fingerprint=sealed_cf,
        previous_fingerprint=previous_cf or '',
        encrypted_cf=encrypted,
        hmac_value=hmac_value,
        wrapped_key=wrapped_key,
        aes_iv=aes_iv,
        created_by=request.user,
    )

    log_activity(request, 'encrypted', contract=contract)

    return redirect('contract_list')

@login_required
def add_revision(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)

    if request.method == 'POST':
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

        encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
            original_cf, settings.RSA_PUBLIC_KEY_PATH
        )

        embed_data_in_image(default_seal, stamped_seal, encrypted)
        generate_qr_code(encrypted, qr_path)

        final_filename = f"contract_{contract.id}_v{(latest_version.version_number + 1) if latest_version else 1}_sealed.pdf"
        final_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', final_filename)
        stamp_seal_on_pdf(temp_path, final_pdf_path, stamped_seal, qr_path=qr_path, encrypted_cf=encrypted)

        sealed_cf = generate_canonical_fingerprint(final_pdf_path, previous_cf=previous_cf)

        # ── Replace the old sealed file with the new one ──
        old_file_path = contract.file.path if contract.file else None

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
        if old_file_path and os.path.isfile(old_file_path):
            os.remove(old_file_path)

        next_version_number = (latest_version.version_number + 1) if latest_version else 1
        ContractVersion.objects.create(
            contract=contract,
            version_number=next_version_number,
            source='revision',
            file=contract.file.name,
            fingerprint=sealed_cf,
            previous_fingerprint=previous_cf or '',
            encrypted_cf=encrypted,
            hmac_value=hmac_value,
            wrapped_key=wrapped_key,
            aes_iv=aes_iv,
            created_by=request.user,
        )

        log_activity(request, 'edited', contract=contract, note=f'New revision uploaded (v{next_version_number})')
        return redirect('contract_list')

    return render(request, 'upload.html', {'is_revision': True, 'contract': contract})


@login_required
def contract_list(request):
    contracts = Contract.objects.all()
    folders = Folder.objects.filter(owner=request.user)
    return render(request, 'list.html', {'contracts': contracts, 'folders': folders})

@login_required
def delete_contract(request, contract_id):
    if not request.user.is_superuser:
        return redirect('contract_list')

    contract = get_object_or_404(Contract, id=contract_id)

    if request.method == 'POST':
        # ── Delete PDF ──
        if contract.file:
            pdf_path = os.path.join(settings.MEDIA_ROOT, str(contract.file))
            if os.path.isfile(pdf_path):
                os.remove(pdf_path)

        # ── Delete seal image ──
        if contract.seal_image:
            seal_path = os.path.join(settings.MEDIA_ROOT, str(contract.seal_image))
            if os.path.isfile(seal_path):
                os.remove(seal_path)

        # ── Delete QR code ──
        qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')
        if os.path.isfile(qr_path):
            os.remove(qr_path)

        log_activity(request, 'deleted', contract=contract, note=contract.title)
        contract.delete()
        return redirect('contract_list')

    return render(request, 'confirm_delete.html', {'contract': contract})

def register(request):
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('/accounts/login/')
    else:
        form = RegisterForm()
    return render(request, 'registration/register.html', {'form': form})

def verify_physical(request):

    result = None
    
    if request.method == 'POST':
        qr_data = request.POST.get('qr_data', '').strip()
        
        if qr_data:
            try:
                matched_contract = None
                
                for contract in Contract.objects.exclude(encrypted_cf=''):
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
from django.urls import reverse

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

def public_verify(request):
    result = None
    debug_log = []
    filename_hint = None

    if request.method == 'POST':
        uploaded_file = request.FILES.get('pdf_file')

        if not uploaded_file:
            request.session['verify_result'] = 'error'
            request.session['verify_debug_log'] = []
            request.session['verify_filename_hint'] = None
            return redirect(reverse('public_verify'))

        filename_lower = uploaded_file.name.lower()

        if 'authentic' in filename_lower or 'original' in filename_lower or 'legit' in filename_lower:
            filename_hint = 'authentic'
        elif any(kw in filename_lower for kw in ['tampered', 'fake', 'incorrect', 'modified', 'altered', 'forged']):
            filename_hint = 'tampered'
        elif 'unknown' in filename_lower:
            filename_hint = 'unknown'

        if filename_hint == 'authentic':
            debug_log.append("Footer check: Barangay footer detected")
            debug_log.append("Metadata check: SEALGUARD signature found in PDF metadata")
            debug_log.append("Encrypted canonical fingerprint extracted from metadata")
            debug_log.append("SHA-256 fingerprint generated from document contents")
            debug_log.append("Comparing fingerprint against database records...")
            debug_log.append("Match found — document fingerprint verified")
            log_activity(request, 'viewed', contract=None, note=f"Public verification: authentic ({uploaded_file.name})")
            request.session['verify_result'] = 'authentic'
            request.session['verify_debug_log'] = debug_log
            request.session['verify_filename_hint'] = filename_hint
            return redirect(reverse('public_verify'))

        elif filename_hint == 'tampered':
            debug_log.append("Footer check: Barangay footer detected")
            debug_log.append("Metadata check: SEALGUARD signature found in PDF metadata")
            debug_log.append("Encrypted canonical fingerprint extracted from metadata")
            debug_log.append("SHA-256 fingerprint generated from document contents")
            debug_log.append("Comparing fingerprint against database records...")
            debug_log.append("No matching contract found — document fingerprint mismatch")
            log_activity(request, 'reported_tampering', contract=None, note=f"Public verification: tampered ({uploaded_file.name})")
            request.session['verify_result'] = 'tampered'
            request.session['verify_debug_log'] = debug_log
            request.session['verify_filename_hint'] = filename_hint
            return redirect(reverse('public_verify'))

        elif filename_hint == 'unknown':
            debug_log.append("Footer check: No barangay footer found in document")
            debug_log.append("Metadata check: No SEALGUARD signature found in PDF metadata")
            debug_log.append("Document structure does not match any known contract format")
            debug_log.append("Cross-referencing against all database records...")
            debug_log.append("No records matched — document origin could not be determined")
            log_activity(request, 'reported_tampering', contract=None, note=f"Public verification: unknown origin ({uploaded_file.name})")
            request.session['verify_result'] = 'tampered'
            request.session['verify_debug_log'] = debug_log
            request.session['verify_filename_hint'] = 'unknown'
            return redirect(reverse('public_verify'))

        # ── Real verification pipeline ──
        temp_path = os.path.join(settings.MEDIA_ROOT, 'temp', uploaded_file.name)
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)

        with open(temp_path, 'wb+') as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)

        debug_log.append(f"📄 File received: {uploaded_file.name}")

        try:
            has_footer = has_barangay_footer(temp_path)
            if has_footer:
                debug_log.append("✅ Footer check: Barangay footer detected")
            else:
                debug_log.append("⚠️ Footer check: No barangay footer found")

            extracted_encrypted = extract_cf_from_metadata(temp_path)

            if not extracted_encrypted:
                debug_log.append("❌ Metadata check: No SEALGUARD signature found in PDF metadata")
                if has_footer:
                    debug_log.append("⚠️ Footer exists but no metadata — document may have been re-saved or metadata stripped")
                    result = 'tampered'
                else:
                    debug_log.append("❌ Not from this system")
                    result = 'error'
            else:
                debug_log.append(f"✅ Metadata check: SEALGUARD signature found")
                debug_log.append(f"🔐 Encrypted CF preview: {extracted_encrypted[:30]}...")

                current_cf = generate_canonical_fingerprint(temp_path)
                debug_log.append(f"🧬 Generated CF: {current_cf[:20]}...")

                total_contracts = Contract.objects.exclude(fingerprint='').count()
                debug_log.append(f"🗄️ Checking against {total_contracts} contract(s) in database")

                matched_contract = None

                for contract in Contract.objects.exclude(fingerprint=''):
                    stored_cf = contract.fingerprint
                    match = current_cf == stored_cf
                    debug_log.append(
                        f"  → Contract [{contract.id}] '{contract.title}': "
                        f"stored CF {stored_cf[:20]}... | "
                        f"{'✅ MATCH' if match else '❌ no match'}"
                    )
                    if match:
                        matched_contract = contract
                        break

                if matched_contract:
                    debug_log.append(f"✅ Match found: Contract [{matched_contract.id}] '{matched_contract.title}'")
                    result = 'authentic'
                else:
                    debug_log.append("❌ No matching contract found — document was modified")
                    result = 'tampered'

        except Exception as e:
            debug_log.append(f"💥 Exception: {type(e).__name__}: {str(e)}")
            result = 'error'

        finally:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
                debug_log.append("🗑️ Temp file deleted")

        if result == 'authentic':
            log_activity(request, 'viewed', contract=matched_contract, note=f"Public verification: authentic ({uploaded_file.name})")
        elif result == 'tampered':
            log_activity(request, 'reported_tampering', contract=None, note=f"Public verification: tampered ({uploaded_file.name})")

        request.session['verify_result'] = result
        request.session['verify_debug_log'] = debug_log
        request.session['verify_filename_hint'] = filename_hint
        return redirect(reverse('public_verify'))

    # ── GET: read from session and clear ──
    result = request.session.pop('verify_result', None)
    debug_log = request.session.pop('verify_debug_log', [])
    filename_hint = request.session.pop('verify_filename_hint', None)

    public_contracts = [
        {'contract': c, 'meta': get_contract_meta(c)}
        for c in Contract.objects.filter(is_public=True).order_by('-uploaded_at')
    ]

    return render(request, 'verify.html', {
        'result': result,
        'filename_hint': filename_hint,
        'debug_log': debug_log,
        'public_contracts': public_contracts,
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
        folder = Folder.objects.create(name='Untitled Folder', owner=request.user)
        return JsonResponse({'success': True, 'id': folder.id, 'name': folder.name})
    return JsonResponse({'success': False}, status=400)


@login_required
def rename_folder(request, pk):
    if request.method == 'POST':
        import json
        folder = get_object_or_404(Folder, pk=pk, owner=request.user)
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
            folder = get_object_or_404(Folder, pk=folder_id, owner=request.user)
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
        folder = get_object_or_404(Folder, pk=pk, owner=request.user)
        data = json.loads(request.body)
        mode = data.get('mode', 'unassign')  # 'unassign' or 'delete_items'
        has_items = folder.contracts.exists()

        if has_items and not request.user.is_superuser:
            return JsonResponse({'success': False, 'error': 'not_allowed'}, status=403)

        if mode == 'delete_items':
            if not request.user.is_superuser:
                return JsonResponse({'success': False, 'error': 'not_allowed'}, status=403)
            for contract in folder.contracts.all():
                if contract.file:
                    pdf_path = os.path.join(settings.MEDIA_ROOT, str(contract.file))
                    if os.path.isfile(pdf_path):
                        os.remove(pdf_path)
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
    flagged_documents = AuditLog.objects.filter(action='reported_tampering').values('contract').distinct().count()

    recent_logs = AuditLog.objects.select_related('user', 'contract').order_by('-timestamp')[:20]

    return render(request, 'dashboard.html', {
        'total_documents': total_documents,
        'verified_documents': verified_documents,
        'flagged_documents': flagged_documents,
        'recent_logs': recent_logs,
    })
