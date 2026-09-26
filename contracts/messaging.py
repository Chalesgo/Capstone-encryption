"""Channel-independent delivery seam; SMS defaults to a local outbox."""
import re
import secrets
import time
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.crypto import salted_hmac, constant_time_compare
from django.utils.module_loading import import_string


logger = logging.getLogger(__name__)


def send_email_via_n8n(recipient, subject, body, *, message_id=''):
    """Send one email through the configured authenticated n8n webhook."""
    url = getattr(settings, 'N8N_EMAIL_WEBHOOK_URL', '').strip()
    secret = getattr(settings, 'N8N_EMAIL_WEBHOOK_SECRET', '')
    header = getattr(settings, 'N8N_EMAIL_WEBHOOK_HEADER', 'X-SealGuard-Webhook').strip()
    if not url or not secret or not header:
        logger.error('n8n email delivery is configured incompletely; url=%s header_configured=%s secret_configured=%s',
                     bool(url), bool(header), bool(secret))
        return False
    payload = json.dumps({
        'message_id': str(message_id),
        'channel': 'email',
        'recipient': str(recipient),
        'subject': str(subject),
        'body': str(body),
    }).encode('utf-8')
    request = Request(url, data=payload, method='POST', headers={
        'Content-Type': 'application/json',
        header: secret,
    })
    try:
        with urlopen(request, timeout=getattr(settings, 'N8N_EMAIL_WEBHOOK_TIMEOUT', 10)) as response:
            if not 200 <= response.status < 300:
                logger.error('n8n email delivery returned HTTP %s; message_id=%s host=%s',
                             response.status, message_id, urlsplit(url).netloc)
                return False
            result = json.loads(response.read().decode('utf-8') or '{}')
            accepted = result.get('ok') is True and result.get('channel') == 'email'
            if not accepted:
                logger.error('n8n email delivery returned an unexpected response; message_id=%s host=%s keys=%s',
                             message_id, urlsplit(url).netloc, sorted(result.keys()))
            else:
                logger.info('n8n email delivery accepted; message_id=%s provider_message_id=%s host=%s',
                            message_id, result.get('provider_message_id', ''), urlsplit(url).netloc)
            return accepted
    except HTTPError as exc:
        logger.error('n8n email delivery HTTP error %s; message_id=%s host=%s',
                     exc.code, message_id, urlsplit(url).netloc)
        return False
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        logger.error('n8n email delivery failed (%s); message_id=%s host=%s',
                     type(exc).__name__, message_id, urlsplit(url).netloc)
        return False


def send_email_message(recipient, subject, body):
    """Log an email and use n8n when configured, otherwise Django's backend."""
    n8n_url = getattr(settings, 'N8N_EMAIL_WEBHOOK_URL', '').strip()
    if n8n_url:
        from .models import EmailMessageLog
        entry = EmailMessageLog.objects.create(recipient=str(recipient), subject=subject, body=body)
        entry.delivered = send_email_via_n8n(recipient, subject, body, message_id=entry.pk)
        entry.save(update_fields=['delivered'])
        logger.info('email message log updated; message_id=%s delivered=%s', entry.pk, entry.delivered)
        return int(entry.delivered)
    from .emailing import send_sealguard_mail
    return send_sealguard_mail(subject, body, [recipient], fail_silently=True)


def normalize_phone(value):
    value = re.sub(r'[\s().-]', '', str(value or ''))
    if re.fullmatch(r'09[0-9]{9}', value):
        value = '+63' + value[1:]
    elif re.fullmatch(r'639[0-9]{9}', value):
        value = '+' + value
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise ValidationError('Use a mobile number such as 0917 123 4567 or an international number starting with + and the country code.')
    return value


def send_message(channel, recipient, subject, body):
    if channel == 'email':
        return bool(send_email_message(recipient, subject, body))
    if channel != 'sms':
        raise ValueError('Unsupported delivery channel')
    from .models import SMSMessageLog
    entry = SMSMessageLog.objects.create(recipient=normalize_phone(recipient), subject=subject, body=body)
    backend = getattr(settings, 'SMS_DELIVERY_BACKEND', '')
    if backend:
        try:
            # Adapter must return True only after provider acceptance.
            accepted = import_string(backend)(message_id=entry.pk, recipient=entry.recipient, subject=subject, body=body)
            entry.status = 'sent' if accepted is True else 'failed'
        except Exception:
            entry.status = 'failed'
        entry.save(update_fields=['status'])
    return entry.status != 'failed'


def code_digest(code):
    return salted_hmac('sealguard-verification-code', str(code), algorithm='sha256').hexdigest()


def valid_code(code, digest):
    return bool(re.fullmatch(r'[0-9]{6}', str(code)) and constant_time_compare(code_digest(code), digest))


def issue_challenge(key, channel, recipient, subject, payload=None):
    # Bound deliveries across browser sessions as well as per challenge.
    throttle = 'sealguard:code-send:' + salted_hmac('code-recipient', recipient.lower()).hexdigest()
    if not cache.add(throttle, True, 60):
        raise ValidationError('Please wait one minute before requesting another code.')
    code = f'{secrets.randbelow(1000000):06d}'
    pending = dict(payload or {}, digest=code_digest(code), channel=channel, recipient=recipient,
                   expires=time.time() + 600, attempts=0)
    cache.set(key, pending, 600)
    if not send_message(channel, recipient, subject, f'Your SealGuard verification code is {code}. It expires in 10 minutes.'):
        cache.delete(key)
        raise ValidationError('Delivery failed. Please try again in one minute.')
    return pending


def check_challenge(key, code):
    pending = cache.get(key)
    if not pending or pending.get('expires', 0) <= time.time() or pending.get('attempts', 5) >= 5:
        cache.delete(key)
        return None
    pending['attempts'] += 1
    cache.set(key, pending, max(1, int(pending['expires'] - time.time())))
    if not valid_code(code, pending['digest']):
        return None
    cache.delete(key)
    return pending


def notify_account(user, subject, body):
    security = getattr(user, 'account_security', None)
    sms_backend = getattr(settings, 'SMS_DELIVERY_BACKEND', '').strip()
    if (security and security.preferred_channel == 'sms' and security.phone_verified
            and security.phone_number and sms_backend):
        return send_message('sms', security.phone_number, subject, body)
    if user.email and (not security or security.email_verified):
        return send_message('email', user.email, subject, body)
    return False


def reset_contact_valid(reset):
    security = getattr(reset.user, 'account_security', None)
    if not reset.user.is_active:
        return False
    if reset.channel == 'sms':
        return bool(security and security.phone_verified and security.phone_number == reset.phone_number)
    return bool(reset.email and reset.email.lower() == reset.user.email.lower() and (not security or security.email_verified))
