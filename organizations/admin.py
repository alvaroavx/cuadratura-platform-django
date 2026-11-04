from django.contrib import admin
from .models import OrganizationMembership, TaxpayerOrganization, UserInvitation

@admin.register(TaxpayerOrganization)
class TaxpayerOrganizationAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "tax_id", "is_active", "archived_at")
    list_filter = ("is_active", ("archived_at", admin.EmptyFieldListFilter))
    search_fields = ("legal_name", "tax_id")
    readonly_fields = ("archived_by", "created_at", "updated_at")

admin.site.register([OrganizationMembership, UserInvitation])
