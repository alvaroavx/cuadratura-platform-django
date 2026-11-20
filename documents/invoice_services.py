from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Sum

from audit.services import record_event
from organizations.models import TaxpayerOrganization
from organizations.rut import same_rut_text
from reports.models import MonthlyClose

from .invoice_pdf import PDF_SOURCE
from .models import TaxDocument


def invoice_period_closed(organization, period):
    return MonthlyClose.objects.filter(
        organization=organization, period=period, status=MonthlyClose.Status.CLOSED,
    ).exists()


def lock_open_organization(organization, period):
    organization = TaxpayerOrganization.objects.select_for_update().get(pk=organization.pk)
    if not organization.is_active or organization.archived_at or invoice_period_closed(organization, period):
        raise PermissionDenied("El contribuyente está inactivo o el período está cerrado.")
    return organization


@transaction.atomic
def save_reviewed_invoice(*, organization, actor, period, values, source):
    organization = lock_open_organization(organization, period)

    fields = values.copy()
    fields.pop("preview")
    recipient = fields.pop("recipient_tax_id")
    is_sale = same_rut_text(fields["issuer_tax_id"], organization.tax_id)
    if is_sale == same_rut_text(recipient, organization.tax_id):
        raise ValidationError("Los RUT revisados no corresponden al contribuyente actual.")
    direction = TaxDocument.Direction.SALE if is_sale else TaxDocument.Direction.PURCHASE
    fields["counterparty_tax_id"] = recipient if is_sale else fields["issuer_tax_id"]

    key = {
        "organization": organization, "direction": direction,
        **{name: fields.pop(name) for name in ("document_type", "issuer_tax_id", "folio")},
    }
    current = TaxDocument.objects.filter(**key).first()
    if current:
        comparable = ("counterparty_tax_id", "issue_date", "exempt_amount", "net_amount", "total_amount")
        current_vat = current.vat_amount + current.vat_non_recoverable_amount + current.vat_common_use_amount
        if current_vat != fields["vat_amount"] or any(getattr(current, name) != fields[name] for name in comparable):
            raise ValidationError("Ya existe una factura con esta identidad y datos distintos. Revisa el registro antes de continuar.")
        return current, False
    if TaxDocument.objects.filter(organization=organization, source_format=PDF_SOURCE, source_sha256=source["sha256"]).exists():
        raise ValidationError("Este PDF ya se usó para registrar otra factura del contribuyente.")
    document = TaxDocument.objects.create(
        **key, **fields, tax_period=period, source_format=PDF_SOURCE,
        source_filename=source["filename"], source_sha256=source["sha256"], source_line=1,
    )
    record_event(
        organization=organization, actor=actor, action="tax_document.invoice_pdf_saved",
        target=document, metadata={"period": period.isoformat(), "direction": direction},
    )
    return document, True


def monthly_tax_documents(organization, period):
    groups = TaxDocument.objects.filter(organization=organization, tax_period=period).values(
        "direction", "document_type",
    ).annotate(
        count=Count("pk"), net=Sum("net_amount"), exempt=Sum("exempt_amount"),
        vat=Sum("vat_amount"), non_recoverable=Sum("vat_non_recoverable_amount"),
        common=Sum("vat_common_use_amount"),
    )
    summary = {"count": 0, "sales": 0, "purchases": 0, "sales_vat": 0, "purchase_vat": 0}
    for group in groups:
        sign = -1 if group["document_type"] == TaxDocument.DocumentType.CREDIT_NOTE else 1
        summary["count"] += group["count"]
        net = sign * (group["net"] + group["exempt"])
        vat = sign * (group["vat"] + group["non_recoverable"] + group["common"])
        if group["direction"] == TaxDocument.Direction.SALE:
            summary["sales"] += net
            summary["sales_vat"] += vat
        else:
            # IVA de compra informado, sin presumir derecho a crédito fiscal.
            summary["purchase_vat"] += vat
            summary["purchases"] += net + sign * group["non_recoverable"]
    return summary
