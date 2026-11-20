import hashlib
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from organizations.access import IMPORT_ROLES, authorized_organization

from .forms import HonorariumReviewForm, InvoiceReviewForm, PdfUploadForm
from .honorarium_pdf import is_honorarium_pdf, suggested_honorarium_fields
from .honorarium_services import save_reviewed_honorarium
from .invoice_pdf import read_pdf, suggested_fields
from .invoice_services import save_reviewed_invoice

PREVIEW_SALT = "documents.invoice_pdf.review"


@login_required
@require_http_methods(["GET", "POST"])
def upload_document_pdf(request, organization_id, period, *, honorarium=False):
    review_type = HonorariumReviewForm if honorarium else InvoiceReviewForm
    suggest = suggested_honorarium_fields if honorarium else suggested_fields
    save_document = save_reviewed_honorarium if honorarium else save_reviewed_invoice
    label = "boleta de honorarios" if honorarium else "factura"
    route = "honorarium-upload" if honorarium else "invoice-upload"
    salt = "documents.honorarium_pdf.review" if honorarium else PREVIEW_SALT
    org = authorized_organization(request.user, organization_id, roles=IMPORT_ROLES)
    try:
        month = date.fromisoformat(f"{period}-01")
    except ValueError as exc:
        raise Http404 from exc

    review_form = None
    upload_form = PdfUploadForm()
    pdf_has_text = True
    scope = [str(org.pk), str(request.user.pk), month.isoformat()]
    review_options = {"organization": org}
    if request.method == "POST" and request.POST.get("action") == "save":
        review_form = review_type(request.POST, **review_options)
        if review_form.is_valid():
            try:
                source = signing.loads(review_form.cleaned_data["preview"], salt=salt, max_age=3600)
                if source["scope"] != scope:
                    raise signing.BadSignature
                issue_month = review_form.cleaned_data["issue_date"].replace(day=1)
                document, created = save_document(
                    organization=org, actor=request.user, period=issue_month,
                    values=review_form.cleaned_data, source=source,
                )
            except signing.BadSignature:
                review_form.add_error(None, "La revisión caducó o no corresponde a este período. Vuelve a subir el PDF.")
            except ValidationError as exc:
                review_form.add_error(None, exc)
            else:
                messages.success(request, f"{label.capitalize()} guardada." if created else f"La {label} ya estaba registrada.")
                recorded_month = issue_month if honorarium else document.tax_period
                destination = redirect("organization-period", organization_id=org.pk, period=recorded_month.isoformat()[:7])
                if request.headers.get("X-PDF-Review") == "1":
                    return JsonResponse({"redirect": destination.url})
                return destination
    elif request.method == "POST":
        upload_form = PdfUploadForm(request.POST, request.FILES)
        if upload_form.is_valid():
            pdf = upload_form.cleaned_data["pdf"]
            data = pdf.read()
            try:
                pdf_text = read_pdf(data)
                if pdf_text and honorarium != is_honorarium_pdf(pdf_text):
                    if honorarium:
                        raise ValidationError("El PDF no se reconoce como una boleta de honorarios electrónica.")
                    raise ValidationError("Para una boleta de honorarios, usa Cargar boleta de honorarios.")
            except ValidationError as exc:
                upload_form.add_error("pdf", exc)
            else:
                pdf_has_text = bool(pdf_text)
                initial = suggest(pdf_text, org.tax_id)
                initial["preview"] = signing.dumps({
                    "scope": scope, "filename": pdf.name, "sha256": hashlib.sha256(data).hexdigest(),
                }, salt=salt)
                review_form = review_type(initial=initial, **review_options)

    return render(request, "documents/invoice_upload.html", {
        "organization": org, "period": month, "upload_form": upload_form,
        "review_form": review_form, "pdf_has_text": pdf_has_text,
        "document_label": label, "upload_route": route, "honorarium": honorarium,
    })
