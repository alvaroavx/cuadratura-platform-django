"""Lectura local y acotada de representaciones PDF de facturas chilenas."""

import re
import subprocess
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

from django.core.exceptions import ValidationError

from organizations.rut import same_rut_text

PDF_SOURCE = "invoice-pdf-v1"
MAX_PDF_BYTES = 5 * 1024 * 1024
RUT = r"\d{1,2}(?:\.\d{3}){2}[ \t]*-[ \t]*[\dk]|\d{1,8}[ \t]*-[ \t]*[\dk]"
MONTHS = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()


def read_pdf(data):
    if not data.startswith(b"%PDF-"):
        raise ValidationError("El archivo debe ser un PDF válido.")
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "factura.pdf"
        source.write_bytes(data)
        try:
            result = subprocess.run(
                ["pdftotext", "-f", "1", "-l", "5", "-layout", "-enc", "UTF-8", str(source), "-"],
                capture_output=True, timeout=8, check=True,
            )
        except FileNotFoundError as exc:
            raise ValidationError("El servidor necesita Poppler (pdftotext) para leer documentos PDF.") from exc
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            raise ValidationError("No se pudo leer este PDF. Revisa que abra y no tenga contraseña.") from exc
    return result.stdout.decode("utf-8", errors="replace").strip()


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


def _issue_date(text, label=r"fecha\s+(?:de\s+)?emision"):
    match = re.search(
        rf"{label}[^\S\n]*:?[\s]*(\d{{1,2}})"
        r"(?:[./-](\d{1,2})[./-]|\s+(?:de\s+)?([a-z]+)\s+(?:de(?:l)?\s+)?)(\d{4})",
        text,
    )
    if match:
        day, numeric_month, month_name, year = match.groups()
        try:
            month = int(numeric_month) if numeric_month else MONTHS.index(month_name) + 1
            return date(int(year), month, int(day))
        except ValueError:
            return None
    return None


def _amount(text, label):
    matches = re.findall(
        rf"\b(?:{label})[^\S\n]*:?[^\S\n]*\$?[^\S\n]*(\d{{1,3}}(?:\.\d{{3}})+|\d+)(?![\d.,]|[^\S\n]*%)",
        text, re.MULTILINE,
    )
    if len(set(matches)) == 1:
        return int(matches[0].replace(".", ""))
    return None


def suggested_fields(pdf_text, organization_tax_id):
    text = _fold(pdf_text)
    fields = {"issue_date": _issue_date(text)}
    kind = re.search(r"\bfactura\s+(?:(?:no\s+afecta\s+o\s+)?exenta\s+)?electronica\b", text)
    if kind:
        fields["document_type"] = 34 if "exenta" in kind.group() else 33
        header = "\n".join(text[kind.end():].splitlines()[:5])
        folio = re.search(r"\b(?:folio|n[ \t]*[°ºo])[^\S\n]*[:.]?[^\S\n]*(\d+)\b", header)
        if folio:
            fields["folio"] = folio.group(1)
    ids = re.findall(rf"\br\.?\s*u\.?\s*t\.?\s*:?[^\S\n]*({RUT})\b", text)
    if len(ids) >= 2:
        fields["issuer_tax_id"], fields["recipient_tax_id"] = ids[:2]
        recipient_name = re.search(r"(?im)^[^\S\n]*(?:señor\s*\(es\)|raz[oó]n social|receptor)[^\S\n]*:[^\S\n]*([^\n]+)", pdf_text)
        if same_rut_text(ids[0], organization_tax_id) and recipient_name:
            fields["counterparty_name"] = re.split(r"\s{2,}", recipient_name.group(1))[0].strip()
    for field, label in {
        "net_amount": r"(?:monto\s+)?neto",
        "exempt_amount": r"(?:monto\s+)?exento",
        "vat_amount": r"(?:i\.?v\.?a\.?)(?:[^\S\n]*\(?19[^\S\n]*%\)?)?",
        "total_amount": r"(?:monto\s+)?total(?:\s+(?:a\s+pagar|factura))?",
    }.items():
        fields[field] = _amount(text, label)
    # Ceros sugeridos únicamente si los importes encontrados cuadran con el total.
    components = ("net_amount", "exempt_amount", "vat_amount")
    if fields["total_amount"] and sum(fields[key] or 0 for key in components) == fields["total_amount"]:
        for key in components:
            fields[key] = fields[key] or 0
    return fields
