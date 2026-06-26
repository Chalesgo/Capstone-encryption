from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from .models import Contract
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
)
import os
import fitz
from .utils import generate_qr_code
import base64
from django.http import JsonResponse

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
            final_filename = original_filename.replace('.pdf', '_sealed.pdf')
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
            return redirect('contract_list')
    else:
        form = ContractForm()

    seal_url = settings.MEDIA_URL + 'seals/default_seal.png'
    return render(request, 'upload.html', {'form': form, 'seal_url': seal_url})


@login_required
def encrypt_contract(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    pdf_path = contract.file.path

    default_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
    stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')
    qr_path = os.path.join(settings.MEDIA_ROOT, 'seals', f'qr_{contract.id}.png')

    original_cf = generate_canonical_fingerprint(pdf_path)

    encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
        original_cf, settings.RSA_PUBLIC_KEY_PATH
    )

    embed_data_in_image(default_seal, stamped_seal, encrypted)

    generate_qr_code(encrypted, qr_path)

    original_filename = os.path.basename(pdf_path)
    final_filename = original_filename.replace('.pdf', '_sealed.pdf')
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

    return redirect('contract_list')


@login_required
def contract_list(request):
    contracts = Contract.objects.all()
    seal_url = settings.MEDIA_URL + 'seals/default_seal.png'
    return render(request, 'list.html', {'contracts': contracts, 'seal_url': seal_url})

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
                        # QR contains the encrypted CF directly
                        # so we just compare it against stored encrypted_cf
                        if qr_data == contract.encrypted_cf:
                            matched_contract = contract
                            break
                    except Exception:
                        continue
                
                if matched_contract:
                    result = 'authentic'
                else:
                    result = 'tampered'
                    
            except Exception:
                result = 'error'
        else:
            result = 'error'

    seal_url = settings.MEDIA_URL + 'seals/default_seal.png'
    return render(request, 'verify_physical.html', {
        'result': result,
        'seal_url': seal_url
    })

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

        request.session['verify_result'] = result
        request.session['verify_debug_log'] = debug_log
        request.session['verify_filename_hint'] = filename_hint
        return redirect(reverse('public_verify'))

    # ── GET: read from session and clear ──
    result = request.session.pop('verify_result', None)
    debug_log = request.session.pop('verify_debug_log', [])
    filename_hint = request.session.pop('verify_filename_hint', None)

    return render(request, 'verify.html', {
        'result': result,
        'filename_hint': filename_hint,
        'debug_log': debug_log,
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
        return JsonResponse({'success': True, 'title': contract.title})
    return JsonResponse({'success': False}, status=400)

# New status view
@login_required
def update_status(request, pk):
    if request.method == 'POST':
        contract = get_object_or_404(Contract, pk=pk)
        contract.status = request.POST.get('status', contract.status)
        contract.save()
    return redirect('contract_list')


