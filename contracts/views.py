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
    make_seal_transparent,
    stamp_seal_on_pdf,
)
import os
import fitz


@login_required
def upload_contract(request):
    if request.method == 'POST':
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.save()

            pdf_path = contract.file.path

            # ── Prepare seal ──
            original_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
            transparent_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'transparent_seal.png')
            stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')

            make_seal_transparent(original_seal, transparent_seal)

            # ── Stamp seal onto PDF first ──
            original_filename = os.path.basename(pdf_path)
            sealed_filename = original_filename.replace('.pdf', '_sealed.pdf')
            sealed_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', sealed_filename)
            stamp_seal_on_pdf(pdf_path, sealed_pdf_path, transparent_seal)

            # ── Generate CF from sealed PDF ──
            cf = generate_canonical_fingerprint(sealed_pdf_path)
            contract.fingerprint = cf

            # ── Encrypt CF with HMAC + AES + RSA ──
            encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
                cf, settings.RSA_PUBLIC_KEY_PATH
            )
            contract.encrypted_cf = encrypted
            contract.hmac_value = hmac_value
            contract.wrapped_key = wrapped_key
            contract.aes_key = aes_iv      # storing IV in aes_key field
            contract.aes_iv = aes_iv       # also stored here for clarity

            # ── Embed encrypted CF into seal via LSB ──
            embed_data_in_image(transparent_seal, stamped_seal, encrypted)

            # ── Re-stamp with the LSB seal ──
            final_pdf_path = sealed_pdf_path.replace('_sealed.pdf', '_final.pdf')
            stamp_seal_on_pdf(sealed_pdf_path, final_pdf_path, stamped_seal)

            # ── Clean up intermediates ──
            if os.path.isfile(pdf_path):
                os.remove(pdf_path)
            if os.path.isfile(sealed_pdf_path):
                os.remove(sealed_pdf_path)

            contract.file = f'contracts/{os.path.basename(final_pdf_path)}'
            contract.seal_image = f'seals/seal_{contract.id}.png'
            contract.save()
            return redirect('contract_list')
    else:
        form = ContractForm()

    return render(request, 'upload.html', {'form': form})


@login_required
def encrypt_contract(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    pdf_path = contract.file.path

    original_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
    transparent_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'transparent_seal.png')
    stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')

    make_seal_transparent(original_seal, transparent_seal)

    original_filename = os.path.basename(pdf_path)
    sealed_filename = original_filename.replace('.pdf', '_sealed.pdf')
    sealed_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', sealed_filename)
    stamp_seal_on_pdf(pdf_path, sealed_pdf_path, transparent_seal)

    cf = generate_canonical_fingerprint(sealed_pdf_path)
    contract.fingerprint = cf

    encrypted, hmac_value, wrapped_key, aes_iv = encrypt_cf(
        cf, settings.RSA_PUBLIC_KEY_PATH
    )
    contract.encrypted_cf = encrypted
    contract.hmac_value = hmac_value
    contract.wrapped_key = wrapped_key
    contract.aes_key = aes_iv
    contract.aes_iv = aes_iv

    embed_data_in_image(transparent_seal, stamped_seal, encrypted)

    final_pdf_path = sealed_pdf_path.replace('_sealed.pdf', '_final.pdf')
    stamp_seal_on_pdf(sealed_pdf_path, final_pdf_path, stamped_seal)

    if os.path.isfile(pdf_path):
        os.remove(pdf_path)
    if os.path.isfile(sealed_pdf_path):
        os.remove(sealed_pdf_path)

    contract.file = f'contracts/{os.path.basename(final_pdf_path)}'
    contract.seal_image = f'seals/seal_{contract.id}.png'
    contract.save()

    return redirect('contract_list')


@login_required
def delete_contract(request, contract_id):
    if not request.user.is_superuser:
        return redirect('contract_list')

    contract = get_object_or_404(Contract, id=contract_id)

    if request.method == 'POST':
        if contract.file and os.path.isfile(contract.file.path):
            os.remove(contract.file.path)
        if contract.seal_image and os.path.isfile(contract.seal_image.path):
            os.remove(contract.seal_image.path)

        contract.delete()
        return redirect('contract_list')

    return render(request, 'confirm_delete.html', {'contract': contract})


def public_verify(request):
    result = None

    if request.method == 'POST':
        uploaded_file = request.FILES.get('pdf_file')

        if uploaded_file:
            temp_path = os.path.join(settings.MEDIA_ROOT, 'temp', uploaded_file.name)
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)

            with open(temp_path, 'wb+') as f:
                for chunk in uploaded_file.chunks():
                    f.write(chunk)

            extracted_seal_path = None

            try:
                # ── Extract seal image from uploaded PDF ──
                doc = fitz.open(temp_path)

                for page in doc:
                    images = page.get_images(full=True)
                    if images:
                        largest = max(images, key=lambda img: img[2] * img[3])
                        xref = largest[0]
                        base_image = doc.extract_image(xref)

                        extracted_seal_path = os.path.join(
                            settings.MEDIA_ROOT, 'temp',
                            f'extracted_seal_{uploaded_file.name}.png'
                        )
                        with open(extracted_seal_path, 'wb') as img_file:
                            img_file.write(base_image["image"])
                        break

                doc.close()

                if not extracted_seal_path or not os.path.isfile(extracted_seal_path):
                    result = 'error'
                else:
                    try:
                        extracted_encrypted = extract_data_from_image(extracted_seal_path)

                        if not extracted_encrypted or len(extracted_encrypted) < 10:
                            result = 'error'
                        else:
                            current_cf = generate_canonical_fingerprint(temp_path)
                            matched_contract = None

                            for contract in Contract.objects.exclude(encrypted_cf=''):
                                try:
                                    decrypted = decrypt_cf(
                                        contract.encrypted_cf,
                                        contract.wrapped_key,
                                        contract.aes_iv,
                                        contract.hmac_value,
                                        settings.RSA_PRIVATE_KEY_PATH
                                    )
                                    if decrypted == current_cf:
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

            except Exception:
                result = 'error'

            finally:
                if os.path.isfile(temp_path):
                    os.remove(temp_path)
                if extracted_seal_path and os.path.isfile(extracted_seal_path):
                    os.remove(extracted_seal_path)

    return render(request, 'verify.html', {'result': result})


def register(request):
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('/accounts/login/')
    else:
        form = RegisterForm()
    return render(request, 'registration/register.html', {'form': form})