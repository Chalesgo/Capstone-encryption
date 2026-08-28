from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

class Folder(models.Model):
    name = models.CharField(max_length=100)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='folders')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class ContractVersion(models.Model):
    SOURCE_CHOICES = [
        ('upload', 'Initial Upload'),
        ('revision', 'New Revision Upload'),
        ('reencrypt', 'Re-encryption'),
        ('physical_scan', 'Physical Signature Scan'),
    ]

    contract = models.ForeignKey('Contract', on_delete=models.CASCADE, related_name='versions')
    version_number = models.PositiveIntegerField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    file = models.FileField(upload_to='contract_versions/')
    fingerprint = models.CharField(max_length=64, blank=True)
    previous_fingerprint = models.CharField(max_length=64, blank=True)
    encrypted_cf = models.TextField(blank=True)
    hmac_value = models.TextField(blank=True)
    wrapped_key = models.TextField(blank=True)
    aes_iv = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-version_number']
        unique_together = ['contract', 'version_number']

    def __str__(self):
        return f"{self.contract.title} — v{self.version_number} ({self.get_source_display()})"

def validate_pdf_signature(file):
    """Model-level check — catches uploads made outside ContractForm (e.g. via /admin/)."""
    try:
        file.seek(0)
        header = file.read(5)
        file.seek(0)
    except Exception:
        raise ValidationError("Unable to read the uploaded file.")

    if header != b'%PDF-':
        raise ValidationError("This file is not a valid PDF.")


class Contract(models.Model):
    title = models.CharField(max_length=255)
    file = models.FileField(
        upload_to='contracts/',
        validators=[FileExtensionValidator(['pdf']), validate_pdf_signature],
    )
    fingerprint = models.CharField(max_length=64, blank=True)
    encrypted_cf = models.TextField(blank=True)
    aes_key = models.TextField(blank=True)
    aes_iv = models.TextField(blank=True)
    wrapped_key = models.TextField(blank=True)
    hmac_value = models.TextField(blank=True)
    seal_image = models.ImageField(upload_to='seals/', blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    original_fingerprint = models.CharField(max_length=64, blank=True)
    STATUS_CHOICES = [
        ('pending', 'Draft'),
        ('sent', 'Sent'),
        ('approved', 'Signed'),
    ]
    recipient = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='contracts')
    folder = models.ForeignKey('Folder', on_delete=models.SET_NULL, null=True, blank=True, related_name='contracts')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    is_public = models.BooleanField(default=False)
    modified_at = models.DateTimeField(auto_now=True)
    tags = models.CharField(max_length=255, blank=True, help_text="Comma-separated tags")
    base_filename = models.CharField(max_length=200, blank=True, help_text="Original filename (no extension), reused across versions")
    is_trashed = models.BooleanField(default=False)
    trashed_at = models.DateTimeField(null=True, blank=True)

    def tag_list(self):
        return [t.strip() for t in self.tags.split(',') if t.strip()]

    def __str__(self):
        return self.title

class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('viewed', 'Viewed Document'),
        ('added', 'Added Document'),
        ('encrypted', 'Encrypted Document'),
        ('edited', 'Edited Document'),
        ('approved', 'Approved Document'),
        ('rejected', 'Rejected Document'),
        ('deleted', 'Deleted Document'),
        ('reported_tampering', 'Reported Tampering'),
        ('failed_login', 'Failed Login Attempt'),
    ]

    contract = models.ForeignKey(
        Contract, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs'
    )
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs'
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)  # optional extra context

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        who = self.user.username if self.user else 'N/A'
        what = self.contract.title if self.contract else '(no document)'
        return f"{who} — {self.get_action_display()} — {what}"