from django import forms

from organizations.rut import InvalidRut, parse_rut, same_rut_text

from .invoice_pdf import MAX_PDF_BYTES
from .models import TaxDocument


class PdfUploadForm(forms.Form):
    pdf = forms.FileField(
        label="Documento en PDF",
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,application/pdf"}),
        help_text="Un archivo de hasta 5 MB.",
    )

    def clean_pdf(self):
        pdf = self.cleaned_data["pdf"]
        if pdf.size > MAX_PDF_BYTES:
            raise forms.ValidationError("El archivo supera los 5 MB.")
        return pdf


class PdfReviewForm(forms.Form):
    folio = forms.IntegerField(label="Folio", min_value=1, max_value=2**63 - 1)
    issue_date = forms.DateField(
        label="Fecha de emisión", input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    issuer_tax_id = forms.CharField(label="RUT emisor", max_length=20)
    recipient_tax_id = forms.CharField(label="RUT receptor", max_length=20)
    counterparty_name = forms.CharField(label="Razón social de la contraparte", max_length=200, required=False)
    preview = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization

    def clean_issuer_tax_id(self):
        return self._rut("issuer_tax_id")

    def clean_recipient_tax_id(self):
        return self._rut("recipient_tax_id")

    def _rut(self, field):
        try:
            return parse_rut(self.cleaned_data[field]).compact
        except InvalidRut as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean(self):
        values = super().clean()
        if self.errors:
            return values
        issuer, recipient = values["issuer_tax_id"], values["recipient_tax_id"]
        own_issuer = same_rut_text(issuer, self.organization.tax_id)
        own_recipient = same_rut_text(recipient, self.organization.tax_id)
        if own_issuer == own_recipient:
            raise forms.ValidationError("El RUT del contribuyente debe coincidir con el emisor o el receptor del documento.")
        return values


class InvoiceReviewForm(PdfReviewForm):
    document_type = forms.TypedChoiceField(
        label="Tipo de factura", coerce=int,
        choices=[
            ("", "Selecciona el tipo"),
            (TaxDocument.DocumentType.INVOICE, TaxDocument.DocumentType.INVOICE.label),
            (TaxDocument.DocumentType.EXEMPT_INVOICE, TaxDocument.DocumentType.EXEMPT_INVOICE.label),
        ],
    )
    net_amount = forms.IntegerField(label="Neto afecto", min_value=0, max_value=2**63 - 1)
    exempt_amount = forms.IntegerField(label="Monto exento", min_value=0, max_value=2**63 - 1)
    vat_amount = forms.IntegerField(label="IVA indicado en la factura", min_value=0, max_value=2**63 - 1)
    total_amount = forms.IntegerField(label="Total", min_value=1, max_value=2**63 - 1)

    def clean(self):
        values = super().clean()
        if self.errors:
            return values
        if values["net_amount"] + values["exempt_amount"] + values["vat_amount"] != values["total_amount"]:
            self.add_error("total_amount", "El total debe ser igual al neto más el exento y el IVA.")
        if values["document_type"] == 34 and (values["net_amount"] or values["vat_amount"]):
            self.add_error("document_type", "En una factura exenta, el neto afecto y el IVA deben ser cero.")
        return values

    field_order = [
        "document_type", "folio", "issue_date", "issuer_tax_id", "recipient_tax_id",
        "counterparty_name", "net_amount", "exempt_amount", "vat_amount", "total_amount",
    ]


class HonorariumReviewForm(PdfReviewForm):
    gross_amount = forms.IntegerField(label="Honorarios brutos", min_value=1, max_value=2**63 - 1)
    withholding_amount = forms.IntegerField(
        label="Impuesto retenido por el receptor", min_value=0, max_value=2**63 - 1,
        help_text="Indica 0 si la boleta fue emitida sin retención del receptor.",
    )
    liquid_amount = forms.IntegerField(label="Líquido de la boleta", min_value=0, max_value=2**63 - 1)

    def clean(self):
        values = super().clean()
        if not self.errors and values["gross_amount"] - values["withholding_amount"] != values["liquid_amount"]:
            self.add_error("liquid_amount", "El líquido debe ser igual al bruto menos la retención.")
        return values


class PpmEstimateForm(forms.Form):
    ppm_rate = forms.DecimalField(
        label="Tasa PPM del mes (%)", min_value=0, max_value=100,
        max_digits=6, decimal_places=3, localize=True, required=False,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )
