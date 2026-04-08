from django.shortcuts import render, redirect
from .models import Contract
from .forms import ContractForm
from django.contrib.auth.decorators import login_required


@login_required
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