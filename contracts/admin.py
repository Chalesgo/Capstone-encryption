from django.contrib import admin
from .models import Contract, Tutorial


admin.site.site_header = 'SealGuard Administration'
admin.site.site_title = 'SealGuard Admin'
admin.site.index_title = 'System administration'


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
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
