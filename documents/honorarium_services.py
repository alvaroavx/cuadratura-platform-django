from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from audit.services import record_event
from organizations.rut import same_rut_text

from .invoice_services import lock_open_organization
from .models import HonorariumReceipt


@transaction.atomic
def save_reviewed_honorarium(*, organization, actor, period, values, source):
    organization = lock_open_organization(organization, period)
    fields = {field: value for field, value in values.items() if field != "preview"}
    own_issuer = same_rut_text(fields["issuer_tax_id"], organization.tax_id)
    own_recipient = same_rut_text(fields["recipient_tax_id"], organization.tax_id)
    if own_issuer == own_recipient:
        raise ValidationError("Los RUT revisados no corresponden al contribuyente actual.")
    direction = HonorariumReceipt.Direction.INCOME if own_issuer else HonorariumReceipt.Direction.EXPENSE
    current = HonorariumReceipt.objects.filter(
        organization=organization, issuer_tax_id=fields["issuer_tax_id"], folio=fields["folio"],
    ).first()
    if current:
        comparable = ("recipient_tax_id", "issue_date", "gross_amount", "withholding_amount", "liquid_amount")
        if any(getattr(current, field) != fields[field] for field in comparable):
            raise ValidationError("Ya existe una boleta con ese emisor y folio, pero con datos distintos.")
        return current, False
    if HonorariumReceipt.objects.filter(organization=organization, source_sha256=source["sha256"]).exists():
        raise ValidationError("Este PDF ya se usó para registrar otra boleta del contribuyente.")
    receipt = HonorariumReceipt.objects.create(
        organization=organization, direction=direction, **fields,
        source_filename=source["filename"], source_sha256=source["sha256"],
    )
    record_event(organization=organization, actor=actor, action="honorarium_receipt.pdf_saved",
                 target=receipt, metadata={"period": period.isoformat(), "direction": direction})
    return receipt, True


def monthly_honoraria(organization, period):
    receipts = HonorariumReceipt.objects.filter(
        organization=organization, issue_date__year=period.year, issue_date__month=period.month,
    )
    groups = receipts.values("direction").annotate(
        gross=Sum("gross_amount"), withholding=Sum("withholding_amount"), liquid=Sum("liquid_amount"),
    )
    totals = {direction: {"gross": 0, "withholding": 0, "liquid": 0} for direction in HonorariumReceipt.Direction.values}
    totals.update({group["direction"]: group for group in groups})
    return totals, receipts.order_by("-issue_date", "-folio")[:20]
