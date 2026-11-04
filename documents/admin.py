from django.contrib import admin
from .models import ElectronicReceipt, TaxDocument
admin.site.register(ElectronicReceipt)


@admin.register(TaxDocument)
class TaxDocumentAdmin(admin.ModelAdmin):
    """Solo lectura: el RCV es la fuente y PostgreSQL rechaza cambios y eliminaciones (ADR 0009)."""
    list_display = ("organization", "direction", "document_type", "folio", "issuer_tax_id", "issue_date", "total_amount")
    list_filter = ("direction", "document_type", "tax_period")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
