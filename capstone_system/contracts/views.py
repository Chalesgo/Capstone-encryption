from django.shortcuts import render, redirect
from .models import Contract
from .forms import ContractForm

def upload_contract(request):
    if request.method == 'POST':
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('contract_list')
    else:
        form = ContractForm()

    return render(request, 'upload.html', {'form': form})


def contract_list(request):
    contracts = Contract.objects.all()
    return render(request, 'list.html', {'contracts': contracts})