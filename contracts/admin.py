from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.utils.safestring import mark_safe
import secrets
import logging
from datetime import timedelta
from django.utils import timezone
from .models import Contract, Tutorial, PasswordResetRequest, StaffInvitation
from .models import EmailMessageLog, SMSMessageLog, AccountSecurity
from .messaging import send_message, code_digest
from .signals import sync_sealguard_role_group


admin.site.site_header = 'SealGuard Administration'
admin.site.site_title = 'SealGuard Admin'
admin.site.index_title = 'System administration'
logger = logging.getLogger(__name__)


def _superuser_only_admin(request):
    """Django's is_staff flag is the SealGuard staff workspace role, not admin."""
    return bool(request.user and request.user.is_active and request.user.is_superuser)


admin.site.has_permission = _superuser_only_admin

admin.site.unregister(User)


class AccountSecurityInline(admin.StackedInline):
    model = AccountSecurity
    can_delete = False
    extra = 0
    fields = ('phone_number', 'phone_verified', 'email_verified', 'preferred_channel')
    readonly_fields = fields


@admin.register(User)
class SealGuardUserAdmin(UserAdmin):
    """Keep Django's account controls, but make SealGuard's roles explicit."""

    inlines = (AccountSecurityInline,)
    readonly_fields = ('sealguard_role', 'sealguard_access')
    fieldsets = UserAdmin.fieldsets + (
        ('SealGuard access', {
            'fields': ('sealguard_role', 'sealguard_access'),
            'description': (
                'How access works: the role checkboxes identify the account type, '
                'the matching SealGuard Group lists the broad permissions for that '
                'role, and the document access rules decide which individual PDFs '
                'the user may view or change. Staff status does not grant Django '
                'Admin access; only a superuser can enter this portal. When Staff '
                'status changes, the matching role group is synchronized automatically.'
            ),
        }),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if change and 'email' in form.changed_data:
            security, _ = AccountSecurity.objects.get_or_create(user=form.instance)
            security.email_verified = False
            security.save(update_fields=['email_verified'])
        # The role flags are authoritative; restore the matching role group
        # after Django saves any manually edited group selections.
        sync_sealguard_role_group(form.instance)

    @admin.display(description='Role')
    def sealguard_role(self, obj):
        if obj.is_superuser:
            return 'Admin (superuser)'
        if obj.is_staff:
            return 'Staff (workspace access)'
        return 'User (read-only assigned documents)'

    @admin.display(description='Effective capabilities')
    def sealguard_access(self, obj):
        if obj.is_superuser:
            capabilities = (
                'Open the Admin Portal', 'Manage users, groups, tutorials, and contracts',
                'Manage all documents and access', 'Run and cancel integrity scans',
            )
        elif obj.is_staff:
            capabilities = (
                'Open List and Dashboard', 'View non-trashed PDFs',
                'Upload and manage documents when document access allows it',
                'Use verification and physical verification',
            )
        else:
            capabilities = (
                'Open List and Dashboard', 'View assigned or uploaded PDFs',
                'Use verification and physical verification',
            )
        items = ''.join(f'<li>{capability}</li>' for capability in capabilities)
        return format_html('<ul class="sealguard-user-capabilities">{}</ul>', mark_safe(items))


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    exclude = ('collaborators',)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)

    list_display = (
        'title', 'status', 'recipient', 'is_public', 'is_trashed', 'modified_at'
    )
    list_filter = ('status', 'is_public', 'is_trashed')
    search_fields = ('title', 'recipient__username', 'tags')
    ordering = ('-modified_at',)
    list_per_page = 25


@admin.register(Tutorial)
class TutorialAdmin(admin.ModelAdmin):
    list_display = ('title', 'icon', 'sort_order', 'created_by', 'updated_at')
    list_filter = ('icon',)
    search_fields = ('title', 'summary', 'content')
    ordering = ('sort_order', 'title')
    list_per_page = 25


@admin.register(PasswordResetRequest)
class PasswordResetRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'email', 'phone_number', 'channel', 'status', 'created_at', 'approved_at', 'expires_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__username', 'email')
    readonly_fields = ('user', 'email', 'phone_number', 'channel', 'attempts', 'created_at', 'approved_at', 'expires_at', 'otp')
    actions = ('approve_reset_requests', 'reject_reset_requests')

    @admin.action(description='Issue or resend selected password reset codes')
    def approve_reset_requests(self, request, queryset):
        count = 0
        now = timezone.now()
        for reset in queryset.select_related('user'):
            can_issue = reset.status == 'pending' or (
                reset.status == 'approved' and
                (not reset.expires_at or reset.expires_at < now)
            )
            if not can_issue:
                logger.info('password reset approval skipped; reset_id=%s status=%s', reset.pk, reset.status)
                continue
            from .messaging import reset_contact_valid
            if not reset_contact_valid(reset):
                logger.warning('password reset approval blocked by invalid contact; reset_id=%s user_id=%s channel=%s',
                               reset.pk, reset.user_id, reset.channel)
                continue
            code = f'{secrets.randbelow(1000000):06d}'
            reset.status = 'approved'
            reset.otp = code_digest(code)
            reset.attempts = 0
            reset.approved_at = now
            reset.expires_at = now + timedelta(minutes=10)
            reset.save(update_fields=['status', 'otp', 'attempts', 'approved_at', 'expires_at'])
            recipient = reset.phone_number if reset.channel == 'sms' else reset.email
            delivered = send_message(reset.channel, recipient, 'SealGuard password reset verification code', f'Your SealGuard password reset verification code is {code}. It expires in 10 minutes.')
            logger.info('password reset code delivery attempted; reset_id=%s user_id=%s channel=%s delivered=%s',
                        reset.pk, reset.user_id, reset.channel, delivered)
            if not delivered:
                reset.status = 'pending'
                reset.otp = ''
                reset.save(update_fields=['status', 'otp'])
                continue
            count += 1
        self.message_user(request, f'{count} password reset request(s) approved; delivery requested through the selected channel.')

    @admin.action(description='Reject selected password reset requests')
    def reject_reset_requests(self, request, queryset):
        count = queryset.filter(status='pending').update(status='rejected')
        self.message_user(request, f'{count} password reset request(s) rejected.')


@admin.register(StaffInvitation)
class StaffInvitationAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_by', 'created_at', 'expires_at', 'used_at')
    list_filter = ('created_at', 'expires_at', 'used_at')
    readonly_fields = ('token_hash', 'created_by', 'created_at', 'expires_at', 'used_at')
    search_fields = ('created_by__username',)


@admin.register(EmailMessageLog)
class EmailMessageLogAdmin(admin.ModelAdmin):
    list_display = ('subject', 'recipient', 'delivered', 'sent_at')
    list_filter = ('delivered', 'sent_at')
    search_fields = ('recipient', 'subject', 'body')
    readonly_fields = ('recipient', 'subject', 'body', 'delivered', 'sent_at')
    date_hierarchy = 'sent_at'


@admin.register(SMSMessageLog)
class SMSMessageLogAdmin(admin.ModelAdmin):
    list_display = ('subject', 'recipient', 'status', 'sent_at')
    list_filter = ('status', 'sent_at')
    search_fields = ('recipient', 'subject', 'body')
    readonly_fields = ('recipient', 'subject', 'body', 'status', 'sent_at')
    date_hierarchy = 'sent_at'

    def has_add_permission(self, request):
        return False
