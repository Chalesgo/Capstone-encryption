from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.utils.safestring import mark_safe
import secrets
from datetime import timedelta
from django.utils import timezone
from .models import Contract, Tutorial, PasswordResetRequest
from .models import EmailMessageLog
from .emailing import send_sealguard_mail
from .signals import sync_sealguard_role_group


admin.site.site_header = 'SealGuard Administration'
admin.site.site_title = 'SealGuard Admin'
admin.site.index_title = 'System administration'


def _superuser_only_admin(request):
    """Django's is_staff flag is the SealGuard staff workspace role, not admin."""
    return bool(request.user and request.user.is_active and request.user.is_superuser)


admin.site.has_permission = _superuser_only_admin

admin.site.unregister(User)


@admin.register(User)
class SealGuardUserAdmin(UserAdmin):
    """Keep Django's account controls, but make SealGuard's roles explicit."""

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
    list_display = ('user', 'email', 'status', 'created_at', 'approved_at', 'expires_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__username', 'email')
    readonly_fields = ('user', 'email', 'created_at', 'approved_at', 'expires_at', 'otp')
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
                continue
            code = f'{secrets.randbelow(1000000):06d}'
            reset.status = 'approved'
            reset.otp = code
            reset.approved_at = now
            reset.expires_at = now + timedelta(minutes=10)
            reset.save(update_fields=['status', 'otp', 'approved_at', 'expires_at'])
            send_sealguard_mail('SealGuard password reset verification code', f'Your SealGuard password reset verification code is {code}. It expires in 10 minutes.', [reset.email], fail_silently=True)
            count += 1
        self.message_user(request, f'{count} password reset request(s) approved and emailed.')

    @admin.action(description='Reject selected password reset requests')
    def reject_reset_requests(self, request, queryset):
        count = queryset.filter(status='pending').update(status='rejected')
        self.message_user(request, f'{count} password reset request(s) rejected.')


@admin.register(EmailMessageLog)
class EmailMessageLogAdmin(admin.ModelAdmin):
    list_display = ('subject', 'recipient', 'delivered', 'sent_at')
    list_filter = ('delivered', 'sent_at')
    search_fields = ('recipient', 'subject', 'body')
    readonly_fields = ('recipient', 'subject', 'body', 'delivered', 'sent_at')
    date_hierarchy = 'sent_at'
