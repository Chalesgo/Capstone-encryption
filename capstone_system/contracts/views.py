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


@login_required
def upload_contract(request):
    if request.method == 'POST':
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.save()

            pdf_path = contract.file.path

            cf = generate_canonical_fingerprint(pdf_path)
            contract.fingerprint = cf

            encrypted, aes_key, aes_iv = encrypt_cf(cf)
            contract.encrypted_cf = encrypted
            contract.aes_key = aes_key
            contract.aes_iv = aes_iv

            original_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
            transparent_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'transparent_seal.png')
            stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')

            make_seal_transparent(original_seal, transparent_seal)
            embed_data_in_image(transparent_seal, stamped_seal, encrypted)

            original_filename = os.path.basename(pdf_path)
            sealed_filename = original_filename.replace('.pdf', '_sealed.pdf')
            sealed_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', sealed_filename)

            stamp_seal_on_pdf(pdf_path, sealed_pdf_path, stamped_seal)

            if os.path.isfile(pdf_path):
                os.remove(pdf_path)

            contract.file = f'contracts/{sealed_filename}'
            contract.seal_image = f'seals/seal_{contract.id}.png'
            contract.save()
            return redirect('contract_list')
    else:
        form = ContractForm()

    return render(request, 'upload.html', {'form': form})


@login_required
def contract_list(request):
    contracts = Contract.objects.all()
    return render(request, 'list.html', {'contracts': contracts})


@login_required
def encrypt_contract(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    pdf_path = contract.file.path

    cf = generate_canonical_fingerprint(pdf_path)
    contract.fingerprint = cf

    encrypted, aes_key, aes_iv = encrypt_cf(cf)
    contract.encrypted_cf = encrypted
    contract.aes_key = aes_key
    contract.aes_iv = aes_iv

    original_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'default_seal.png')
    transparent_seal = os.path.join(settings.MEDIA_ROOT, 'seals', 'transparent_seal.png')
    stamped_seal = os.path.join(settings.MEDIA_ROOT, 'seals', f'seal_{contract.id}.png')

    make_seal_transparent(original_seal, transparent_seal)
    embed_data_in_image(transparent_seal, stamped_seal, encrypted)

    original_filename = os.path.basename(pdf_path)
    sealed_filename = original_filename.replace('.pdf', '_sealed.pdf')
    sealed_pdf_path = os.path.join(settings.MEDIA_ROOT, 'contracts', sealed_filename)

    stamp_seal_on_pdf(pdf_path, sealed_pdf_path, stamped_seal)

    if os.path.isfile(pdf_path):
        os.remove(pdf_path)

    contract.file = f'contracts/{sealed_filename}'
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
    uploaded_pdf_path = None

    if request.method == 'POST':
        uploaded_file = request.FILES.get('pdf_file')

        if uploaded_file:
            # Save temporarily
            temp_path = os.path.join(settings.MEDIA_ROOT, 'temp', uploaded_file.name)
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)

            with open(temp_path, 'wb+') as f:
                for chunk in uploaded_file.chunks():
                    f.write(chunk)

            uploaded_pdf_path = temp_path

            # Extract seal from uploaded PDF
            # Find matching contract by re-generating fingerprint and comparing
            try:
                current_cf = generate_canonical_fingerprint(temp_path)
                matched_contract = None

                for contract in Contract.objects.exclude(encrypted_cf=''):
                    decrypted = decrypt_cf(
                        contract.encrypted_cf,
                        contract.aes_key,
                        contract.aes_iv
                    )
                    if decrypted == current_cf:
                        matched_contract = contract
                        break

                if matched_contract:
                    result = 'authentic'
                else:
                    result = 'tampered'

            except Exception as e:
                result = 'error'

            finally:
                # Always delete the uploaded file immediately after checking
                if os.path.isfile(temp_path):
                    os.remove(temp_path)

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