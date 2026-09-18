from django.conf import settings
from django.core.mail import send_mail

from .models import EmailMessageLog


def send_sealguard_mail(subject, message, recipient_list, *, fail_silently=True):
    """Record demo mail details, then send through Django's configured backend."""
    recipients = [str(address) for address in recipient_list if address]
    entries = [
        EmailMessageLog.objects.create(
            recipient=recipient,
            subject=subject,
            body=message,
        )
        for recipient in recipients
    ]
    try:
        delivered = send_mail(
            subject, message, settings.DEFAULT_FROM_EMAIL, recipients,
            fail_silently=fail_silently,
        )
    except Exception:
        for entry in entries:
            entry.delivered = False
            entry.save(update_fields=['delivered'])
        if not fail_silently:
            raise
        return 0
    for entry in entries:
        entry.delivered = bool(delivered)
        entry.save(update_fields=['delivered'])
    return delivered
