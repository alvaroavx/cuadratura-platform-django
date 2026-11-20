import re

from organizations.rut import same_rut_text

from .invoice_pdf import RUT, _amount, _fold, _issue_date

HONORARIUM_HEADER = re.compile(r"\bboleta\s+de\s+honorarios\b")


def is_honorarium_pdf(text):
    return bool(HONORARIUM_HEADER.search(_fold(text)))


def suggested_honorarium_fields(pdf_text, organization_tax_id):
    text = _fold(pdf_text).replace("−", "-").replace("–", "-")
    heading = HONORARIUM_HEADER.search(text)
    if not heading:
        return {}
    header_text = text[heading.end():]
    folio = re.search(r"(?m)^[^\S\n]*(?:folio|n[^\S\n]*[°ºo])[^\S\n]*:?[ \t]*(\d+)\b", header_text)
    fields = {
        "folio": folio.group(1) if folio else None,
        "issue_date": _issue_date(text, r"\bfecha(?=[^\S\n]*:)"),
        "gross_amount": _amount(text, r"total[^\S\n]+honorarios[^\S\n]*:[^\S\n]*\$[^\S\n]*|total[^\S\n]+honorarios"),
        "withholding_amount": _amount(text, r"(?:impto\.?|impuesto)[^\S\n]+retenido|retencion"),
        "liquid_amount": _amount(text, r"total(?:[^\S\n]+liquido)?|liquido(?:[^\S\n]+a[^\S\n]+pagar)?"),
    }

    ids = re.findall(rf"\br\.?[ \t]*u\.?[ \t]*t\.?[ \t]*:[ \t]*({RUT})\b", text)
    if len(ids) >= 2:
        issuer, recipient = ids[:2]
        fields.update(issuer_tax_id=issuer, recipient_tax_id=recipient)
        if same_rut_text(issuer, organization_tax_id):
            name = re.search(r"(?im)señor\s*\(es\)\s*:[ \t]*(.+?)(?=[ \t]*r\.?u\.?t\.?[ \t]*:|$)", pdf_text)
        else:
            name = re.search(r"(?im)^[ \t]*([^\n]+?)[ \t]{2,}electr[oó]nica[ \t]*$", pdf_text)
        if name:
            fields["counterparty_name"] = name.group(1).strip()
    if fields["withholding_amount"] is None and fields["gross_amount"] == fields["liquid_amount"] and fields["gross_amount"]:
        if re.search(r"\b(?:emisor\s+de\s+esta\s+boleta|sin\s+retencion)", text):
            fields["withholding_amount"] = 0
    return fields
