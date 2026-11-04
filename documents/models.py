import uuid
from django.db import models
from django.db.models import F, Q
from organizations.models import TaxpayerOrganization
from sales.models import Sale

class ElectronicReceipt(models.Model):
    class DocumentType(models.IntegerChoices):
        TAXABLE_RECEIPT=39,"Boleta electrónica afecta"; EXEMPT_RECEIPT=41,"Boleta electrónica exenta"
    class Status(models.TextChoices):
        NOT_CREATED="NOT_CREATED","No creada"; PREPARED="PREPARED","Preparada"; SUBMITTING="SUBMITTING","Enviando"; UNCERTAIN="UNCERTAIN","Estado incierto"; ACCEPTED="ACCEPTED","Aceptada"; REJECTED="REJECTED","Rechazada"; CANCELLATION_PENDING="CANCELLATION_PENDING","Anulación pendiente"; CANCELLED="CANCELLED","Anulada"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(TaxpayerOrganization, on_delete=models.PROTECT, related_name="receipts")
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="receipts")
    document_type = models.PositiveSmallIntegerField(choices=DocumentType.choices)
    folio = models.PositiveBigIntegerField(null=True, blank=True)
    tax_issue_date = models.DateField(null=True, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)
    net_amount = models.PositiveBigIntegerField(default=0)
    vat_amount = models.PositiveBigIntegerField(default=0)
    total_amount = models.PositiveBigIntegerField(default=0)
    issuer_name = models.CharField(max_length=200, blank=True)
    signer_name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NOT_CREATED)
    track_id = models.CharField(max_length=120, blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "document_type", "folio"], condition=Q(folio__isnull=False), name="uq_receipt_org_type_folio"),
            models.CheckConstraint(condition=Q(net_amount=F("total_amount")-F("vat_amount")), name="receipt_net_plus_vat_total"),
            models.CheckConstraint(condition=Q(document_type=39) | Q(vat_amount=0), name="receipt_dte41_vat_zero"),
        ]


class TaxDocument(models.Model):
    """Documento tributario informado por el SII en el Registro de Compras y Ventas (ADR 0009).

    Es un registro documental: no es una venta, no implica pago ni emisión y no entra en
    el libro mensual de boletas. Los montos se guardan tal como los informa el SII (sin
    signo); el efecto económico negativo de una nota de crédito se deriva del tipo.
    """
    class Direction(models.TextChoices):
        PURCHASE="PURCHASE","Compra"; SALE="SALE","Venta"
    class DocumentType(models.IntegerChoices):
        INVOICE=33,"Factura electrónica"; EXEMPT_INVOICE=34,"Factura no afecta o exenta electrónica"
        DEBIT_NOTE=56,"Nota de débito electrónica"; CREDIT_NOTE=61,"Nota de crédito electrónica"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(TaxpayerOrganization, on_delete=models.PROTECT, related_name="tax_documents")
    direction = models.CharField(max_length=8, choices=Direction.choices)
    document_type = models.PositiveSmallIntegerField(choices=DocumentType.choices)
    issuer_tax_id = models.CharField("RUT emisor", max_length=12, help_text="Formato 12345678-9. En ventas es el contribuyente; en compras, el proveedor.")
    folio = models.PositiveBigIntegerField()
    counterparty_tax_id = models.CharField("RUT contraparte", max_length=12)
    counterparty_name = models.CharField("Razón social informada por el SII", max_length=200, blank=True)
    transaction_type = models.CharField("Tipo de compra o venta", max_length=40, blank=True)
    issue_date = models.DateField("Fecha de emisión")
    tax_period = models.DateField("Período tributario", help_text="Primer día del mes del RCV")
    sii_received_at = models.DateTimeField("Recepción en el SII", null=True, blank=True)
    exempt_amount = models.PositiveBigIntegerField(default=0)
    net_amount = models.PositiveBigIntegerField(default=0)
    vat_amount = models.PositiveBigIntegerField(default=0, help_text="Ventas: IVA. Compras: IVA recuperable.")
    vat_non_recoverable_amount = models.PositiveBigIntegerField(default=0)
    vat_non_recoverable_code = models.CharField(max_length=4, blank=True)
    vat_common_use_amount = models.PositiveBigIntegerField(default=0)
    total_amount = models.PositiveBigIntegerField(default=0)
    reference_document_type = models.PositiveSmallIntegerField(null=True, blank=True)
    reference_folio = models.PositiveBigIntegerField(null=True, blank=True)
    referenced_document = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="adjustments")
    source_format = models.CharField(max_length=40)
    source_filename = models.CharField(max_length=255)
    source_sha256 = models.CharField(max_length=64)
    source_line = models.PositiveIntegerField()
    imported_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        verbose_name = "documento tributario (RCV)"
        verbose_name_plural = "documentos tributarios (RCV)"
        indexes = [models.Index(fields=["organization", "direction", "tax_period"], name="ix_taxdoc_org_dir_period")]
        constraints = [
            models.UniqueConstraint(fields=["organization", "direction", "document_type", "issuer_tax_id", "folio"], name="uq_taxdoc_natural_key"),
            models.CheckConstraint(condition=Q(folio__gt=0), name="taxdoc_folio_positive"),
            models.CheckConstraint(
                condition=Q(total_amount=F("exempt_amount")+F("net_amount")+F("vat_amount")+F("vat_non_recoverable_amount")+F("vat_common_use_amount")),
                name="taxdoc_components_sum_total",
            ),
            models.CheckConstraint(
                condition=~Q(document_type=34) | (Q(net_amount=0) & Q(vat_amount=0) & Q(vat_non_recoverable_amount=0) & Q(vat_common_use_amount=0)),
                name="taxdoc_exempt_invoice_no_vat",
            ),
            models.CheckConstraint(
                condition=Q(direction="PURCHASE") | (Q(vat_non_recoverable_amount=0) & Q(vat_common_use_amount=0) & Q(vat_non_recoverable_code="")),
                name="taxdoc_sale_no_purchase_vat",
            ),
            models.CheckConstraint(
                condition=Q(reference_document_type__isnull=True, reference_folio__isnull=True) | Q(reference_document_type__isnull=False, reference_folio__isnull=False),
                name="taxdoc_reference_complete",
            ),
        ]
    def __str__(self): return f"{self.get_document_type_display()} {self.folio} ({self.issuer_tax_id})"
