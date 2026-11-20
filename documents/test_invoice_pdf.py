import shutil
from datetime import date
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditEvent
from organizations.models import OrganizationMembership, TaxpayerOrganization
from organizations.rut import verifier_digit
from reports.models import MonthlyClose

from .models import TaxDocument

PERIOD = "2026-11"
MONTH = date(2026, 11, 1)
ISSUER = f"76123456-{verifier_digit(76123456)}"
RECIPIENT = f"77222333-{verifier_digit(77222333)}"
INVOICE_LINES = [
    f"R.U.T.: {ISSUER}",
    "FACTURA ELECTRONICA",
    "Nº 42",
    "SEÑOR(ES): Cliente Ficticio SpA",
    f"RUT: {RECIPIENT}",
    "Fecha de Emision: 19 de noviembre del 2026",
    "MONTO NETO $100.000",
    "I.V.A. (19%) $19.000",
    "IMPUESTO ADICIONAL $0",
    "TOTAL $119.000",
]


def text_pdf(lines):
    """PDF de una página con texto ficticio, sin dependencias de generación."""
    commands = [b"BT /F1 12 Tf 50 760 Td"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({escaped}) Tj 0 -20 Td".encode("latin1"))
    stream = b"\n".join([*commands, b"ET\n"])
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(b"%d 0 obj\n" % index + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        data.extend(b"%010d 00000 n \n" % offset)
    data.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref)
    return bytes(data)


class InvoiceWorkflowTests(TestCase):
    def setUp(self):
        self.org = TaxpayerOrganization.objects.create(tax_id=ISSUER, legal_name="Emisor ficticio")
        self.other_org = TaxpayerOrganization.objects.create(tax_id=RECIPIENT, legal_name="Receptor ficticio")
        self.user = get_user_model().objects.create_user(username="cuadratura-contabilidad")
        for org in (self.org, self.other_org):
            OrganizationMembership.objects.create(
                organization=org, user=self.user, role=OrganizationMembership.Role.ACCOUNTANT,
            )
        self.client.force_login(self.user)
        self.upload = reverse("invoice-upload", args=[self.org.pk, PERIOD])
        self.dashboard = reverse("organization-period", args=[self.org.pk, PERIOD])

    def review(self, url=None, lines=INVOICE_LINES):
        with patch("documents.views.read_pdf", return_value="\n".join(lines)):
            response = self.client.post(url or self.upload, {
                "pdf": SimpleUploadedFile("factura.pdf", text_pdf(lines), content_type="application/pdf"),
            })
        values = response.context["review_form"].initial.copy()
        if values.get("issue_date"):
            values["issue_date"] = values["issue_date"].isoformat()
        return {"action": "save", **{name: value for name, value in values.items() if value is not None}}

    @skipUnless(shutil.which("pdftotext"), "Requiere Poppler")
    def test_pdf_real_completa_formulario(self):
        response = self.client.post(self.upload, {
            "pdf": SimpleUploadedFile("factura.pdf", text_pdf(INVOICE_LINES), content_type="application/pdf"),
        })
        self.assertContains(response, "Revisar factura")
        fields = response.context["review_form"].initial.copy()
        self.assertEqual(fields["folio"], "42")
        self.assertEqual(fields["counterparty_name"], "Cliente Ficticio SpA")
        self.assertEqual((fields["net_amount"], fields["vat_amount"], fields["total_amount"]), (100000, 19000, 119000))
        fields["issue_date"] = fields["issue_date"].isoformat()
        self.assertRedirects(self.client.post(self.upload, {"action": "save", **fields}), self.dashboard)

    def test_revision_guarda_una_vez_y_alimenta_ppm_sin_iva(self):
        values = self.review()
        self.assertFalse(TaxDocument.objects.exists())
        self.assertRedirects(self.client.post(self.upload, values), self.dashboard)
        self.assertRedirects(self.client.post(self.upload, values), self.dashboard)
        self.assertEqual(TaxDocument.objects.get().direction, TaxDocument.Direction.SALE)
        self.assertEqual(AuditEvent.objects.filter(action="tax_document.invoice_pdf_saved").count(), 1)
        dashboard = self.client.get(self.dashboard, {"ppm_rate": "1,5"})
        self.assertEqual(dashboard.context["ppm_estimate"], Decimal("1500"))
        self.assertEqual(dashboard.context["tax"]["sales_vat"], 19000)
        self.assertEqual(dashboard.context["tax"]["result"], 100000)

    def test_compra_exenta_reduce_resultado_y_no_base_ppm(self):
        sales = self.review()
        self.client.post(self.upload, sales)
        lines = [
            f"RUT: {RECIPIENT}", "FACTURA NO AFECTA O EXENTA ELECTRÓNICA", "Folio: 15",
            f"RUT: {ISSUER}", "Fecha de Emisión: 19/11/2026", "Monto Exento $30.000", "TOTAL $30.000",
        ]
        values = self.review(lines=lines)
        self.assertRedirects(self.client.post(self.upload, values), self.dashboard)
        self.assertTrue(TaxDocument.objects.filter(direction=TaxDocument.Direction.PURCHASE, document_type=34).exists())
        dashboard = self.client.get(self.dashboard, {"ppm_rate": "1"})
        self.assertEqual(dashboard.context["tax"]["result"], 70000)
        self.assertEqual(dashboard.context["ppm_estimate"], Decimal("1000"))

    def test_rechaza_total_invalido_y_revision_de_otro_tenant(self):
        values = self.review()
        response = self.client.post(self.upload, {**values, "total_amount": "1"})
        self.assertIn("total_amount", response.context["review_form"].errors)
        other = reverse("invoice-upload", args=[self.other_org.pk, PERIOD])
        response = self.client.post(other, values)
        self.assertTrue(response.context["review_form"].non_field_errors())
        self.assertFalse(TaxDocument.objects.exists())

    def test_periodo_cerrado_bloquea_confirmacion_previa(self):
        values = self.review()
        MonthlyClose.objects.create(organization=self.org, period=MONTH, version=1, status=MonthlyClose.Status.CLOSED)
        self.assertEqual(self.client.post(self.upload, values).status_code, 403)
        self.assertFalse(TaxDocument.objects.exists())

    def test_factura_rcv_existente_no_se_duplica(self):
        TaxDocument.objects.create(
            organization=self.org, direction=TaxDocument.Direction.SALE, document_type=33,
            issuer_tax_id=ISSUER, counterparty_tax_id=RECIPIENT, folio=42,
            issue_date=date(2026, 11, 19), tax_period=MONTH, net_amount=100000,
            vat_amount=19000, total_amount=119000, source_format="sii-rcv-venta-v1", source_line=1,
        )
        self.assertRedirects(self.client.post(self.upload, self.review()), self.dashboard)
        self.assertEqual(TaxDocument.objects.count(), 1)
        self.assertFalse(AuditEvent.objects.filter(action="tax_document.invoice_pdf_saved").exists())
