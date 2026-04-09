from django.shortcuts import render, redirect
from .models import Contract
from .forms import ContractForm
from django.contrib.auth.decorators import login_required
from .utils import generate_file_hash, encrypt_data
import os
from django.conf import settings
from .utils import embed_data_in_image
from .utils import stamp_seal_on_pdf

@login_required
def upload_contract(request):
    if request.method == 'POST':
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            contract = form.save(commit=False)

            file = request.FILES['file']

            # ONLY HASH (no encryption yet)
            contract.fingerprint = generate_file_hash(file)

            contract.save()
            return redirect('contract_list')
    else:
        form = ContractForm()

    return render(request, 'upload.html', {'form': form})


def contract_list(request):
    contracts = Contract.objects.all()
    return render(request, 'list.html', {'contracts': contracts})

from .forms import RegisterForm

def register(request):
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('/accounts/login/')
    else:
        form = RegisterForm()

    return render(request, 'registration/register.html', {'form': form})

from django.shortcuts import get_object_or_404
from .utils import encrypt_data

import os

@login_required
def encrypt_contract(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)

    if not contract.fingerprint.startswith('gAAAA'):
        encrypted_fp = encrypt_data(contract.fingerprint)

        # Paths
        input_image = os.path.join(settings.MEDIA_ROOT, 'seal.png')
        seal_output = os.path.join(settings.MEDIA_ROOT, f'seals/seal_{contract.id}.png')

        embed_data_in_image(input_image, seal_output, encrypted_fp)

        # PDF paths
        original_pdf = contract.file.path
        sealed_pdf_path = os.path.join(
            settings.MEDIA_ROOT,
            f'contracts/sealed_{contract.id}.pdf'
        )

        # 🔥 Stamp seal into PDF
        stamp_seal_on_pdf(original_pdf, sealed_pdf_path, seal_output)

        # Update contract
        contract.fingerprint = encrypted_fp
        contract.seal_image = f'seals/seal_{contract.id}.png'
        contract.file.name = f'contracts/sealed_{contract.id}.pdf'
        contract.save()

    return redirect('contract_list')