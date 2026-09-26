import uuid
from django.db import models
from django.conf import settings


class DocumentAccessLink(models.Model):
    category = models.CharField(max_length=20, choices=[('pdf', 'PDF access request'), ('sgpdf', 'SGPDF decryption request')], default='pdf')
    requested_version = models.ForeignKey('ContractVersion', null=True, blank=True, on_delete=models.SET_NULL)
    requested_file = models.CharField(max_length=255, blank=True)
    ciphertext_sha256 = models.CharField(max_length=64, blank=True)
    upload_name = models.CharField(max_length=255, blank=True)
    contract = models.ForeignKey('Contract', on_delete=models.CASCADE, related_name='access_links')
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    superseded_at = models.DateTimeField(null=True, blank=True)


class DocumentAccessRequest(models.Model):
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    STATUS_CHOICES = [
        ('email_pending', 'Verify email'), ('pending', 'Awaiting staff approval'),
        ('approved', 'Approved'), ('rejected', 'Rejected'), ('revoked', 'Revoked'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    link = models.ForeignKey(DocumentAccessLink, on_delete=models.CASCADE, related_name='requests')
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=16, blank=True)
    contact_channel = models.CharField(max_length=5, choices=[('email', 'Email'), ('sms', 'SMS')], default='email')
    organization = models.CharField(max_length=160, blank=True)
    reason = models.TextField(max_length=1000)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='email_pending')
    otp_hash = models.CharField(max_length=128, blank=True)
    otp_expires_at = models.DateTimeField()
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    ip_hash = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=500, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    approved_version = models.ForeignKey('ContractVersion', null=True, blank=True, on_delete=models.SET_NULL)
    approved_file = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']
