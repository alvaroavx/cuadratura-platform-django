from django.contrib import admin
from .models import OrganizationMembership, TaxpayerActivity, TaxpayerAddress, TaxpayerOrganization, UserInvitation

@admin.register(TaxpayerOrganization)
class TaxpayerOrganizationAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "tax_id", "is_active", "archived_at")
    list_filter = ("is_active", ("archived_at", admin.EmptyFieldListFilter))
    search_fields = ("legal_name", "tax_id")
    readonly_fields = ("archived_by", "created_at", "updated_at")

admin.site.register([OrganizationMembership, UserInvitation])

@admin.register(TaxpayerActivity)
class TaxpayerActivityAdmin(admin.ModelAdmin):
    list_display = ("organization", "code", "name", "source", "last_reported_at", "not_reported_since")
    list_filter = ("source",)
    search_fields = ("code", "name", "organization__legal_name", "organization__tax_id")
    readonly_fields = ("created_at", "updated_at")

@admin.register(TaxpayerAddress)
class TaxpayerAddressAdmin(admin.ModelAdmin):
    list_display = ("organization", "street", "district", "is_main_in_sii", "source", "not_reported_since")
    list_filter = ("source", "is_main_in_sii")
    search_fields = ("street", "district", "organization__legal_name", "organization__tax_id")
    readonly_fields = ("created_at", "updated_at")
