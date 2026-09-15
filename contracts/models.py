from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

class Folder(models.Model):
    name = models.CharField(max_length=100)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='folders')
    created_at = models.DateTimeField(auto_now_add=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class Tutorial(models.Model):
    ICON_CHOICES = [
        ('help', 'Help'),
        ('shield', 'Shield'),
        ('file', 'Document'),
        ('upload', 'Upload'),
        ('lock', 'Encryption'),
        ('search', 'Search'),
        ('folder', 'Folder'),
        ('dashboard', 'Dashboard'),
        ('eye', 'View'),
        ('download', 'Download'),
        ('history', 'History'),
        ('warning', 'Warning'),
        ('check', 'Check'),
        ('user', 'User'),
        ('plus', 'Add'),
        ('trash', 'Trash'),
        ('restore', 'Restore'),
    ]

    title = models.CharField(max_length=160)
    summary = models.CharField(max_length=280)
    content = models.TextField()
    icon = models.CharField(max_length=20, choices=ICON_CHOICES, default='help')
    sort_order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='tutorials'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'title']

    def __str__(self):
        return self.title

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
    vector_fingerprint = models.CharField(max_length=64, blank=True)
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

class PhysicalVerificationManifest(models.Model):
    version = models.OneToOneField(
        ContractVersion, on_delete=models.CASCADE, related_name='physical_manifest'
    )
    manifest_id = models.CharField(max_length=64, unique=True, db_index=True)
    manifest = models.JSONField()
    signature = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Physical manifest {self.manifest_id[:12]} for {self.version}"


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
    fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    vector_fingerprint = models.CharField(max_length=64, blank=True)
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
        ('downloaded', 'Downloaded Document'),
        ('added', 'Added Document'),
        ('encrypted', 'Encrypted Document'),
        ('edited', 'Edited Document'),
        ('approved', 'Approved Document'),
        ('rejected', 'Rejected Document'),
        ('deleted', 'Deleted Document'),
        ('reported_tampering', 'Reported Tampering'),
        ('integrity_scan', 'Integrity Scan'),
        ('verification', 'Verification Attempt'),
        ('failed_login', 'Failed Login Attempt'),
        ('login', 'Successful Login'),
        ('logout', 'Logout'),
        ('locked_out', 'Account Lockout'),
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
    version_number = models.PositiveIntegerField(null=True, blank=True)
    document_title = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)  # optional extra context
    evidence_file = models.FileField(upload_to='verification_evidence/', blank=True, null=True)
    verification_source = models.CharField(max_length=100, blank=True)
    verification_result = models.CharField(max_length=50, blank=True)
    integrity_check = models.CharField(max_length=30, blank=True)
    document_size = models.PositiveBigIntegerField(null=True, blank=True)
    verification_debug_log = models.TextField(blank=True)

    @property
    def display_document_title(self):
        if self.document_title:
            return self.document_title
        if self.contract:
            return self.contract.title
        return ''

    @property
    def has_inspection_details(self):
        return bool(self.verification_result or self.evidence_file)

    @property
    def inspection_source(self):
        if self.verification_source:
            return self.verification_source
        if self.action == 'reported_tampering' and self.note.startswith('Public verification:'):
            return 'Official Barangay Database'
        return ''

    @property
    def inspection_result(self):
        if self.verification_result:
            return self.verification_result
        if self.action == 'reported_tampering':
            return 'Possible Modification'
        return ''

    @property
    def inspection_integrity(self):
        if self.integrity_check:
            return self.integrity_check
        if self.action == 'reported_tampering':
            return 'Failed'
        return ''

    @property
    def display_document_size(self):
        """Return the recorded size, or derive it for older audit rows."""
        if self.document_size is not None:
            return self.document_size
        if self.contract and self.contract.file:
            try:
                return self.contract.file.size
            except (OSError, ValueError):
                pass
        return None

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp', 'id'], name='audit_time_id_idx'),
            models.Index(fields=['action', 'timestamp', 'id'], name='audit_action_time_id_idx'),
        ]

    def __str__(self):
        who = self.user.username if self.user else 'N/A'
        what = self.display_document_title or '(no document)'
        return f"{who} — {self.get_action_display()} — {what}"
