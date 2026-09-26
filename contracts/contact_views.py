from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from .forms import ContactVerificationForm
from .messaging import issue_challenge, check_challenge
from .models import AccountSecurity


@login_required
@never_cache
def verification_contacts(request):
    security, _ = AccountSecurity.objects.get_or_create(user=request.user)
    key = f'sealguard:contact:{request.user.pk}:{request.session.session_key}'
    form = ContactVerificationForm(request.POST or None, initial={'phone_number': security.phone_number, 'channel': security.preferred_channel})
    error = notice = ''
    if request.method == 'POST':
        if request.POST.get('action') == 'restart':
            cache.delete(key)
        elif request.POST.get('action') == 'verify':
            pending = check_challenge(key, request.POST.get('otp', ''))
            if not pending:
                error = 'That code is invalid or expired. Request another code after five unsuccessful attempts.'
            elif pending['channel'] == 'email' and pending['recipient'] != request.user.email:
                error = 'Your email changed. Request a new verification code.'
            else:
                if pending['channel'] == 'sms':
                    security.phone_number = pending['recipient']
                    security.phone_verified = True
                else:
                    security.email_verified = True
                security.preferred_channel = pending['channel']
                try:
                    security.save()
                    notice = 'Contact verified and saved as your preferred delivery channel.'
                except IntegrityError:
                    security.refresh_from_db()
                    error = 'That mobile number is already in use.'
        elif form.is_valid():
            channel = form.cleaned_data['channel']
            recipient = form.cleaned_data['phone_number'] if channel == 'sms' else request.user.email
            if not recipient:
                error = 'Ask an administrator to add your account email first.'
            elif channel == 'sms' and AccountSecurity.objects.filter(phone_number=recipient).exclude(user=request.user).exists():
                error = 'That mobile number is already in use.'
            else:
                try:
                    issue_challenge(key, channel, recipient, 'SealGuard contact verification code')
                except ValidationError as exc:
                    error = exc.messages[0]
    return render(request, 'registration/verification_contacts.html', {
        'form': form, 'security': security, 'pending': cache.get(key), 'error': error, 'notice': notice,
    })
