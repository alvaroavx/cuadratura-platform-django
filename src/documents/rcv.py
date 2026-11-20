"""Lectura de CSV del Registro de Compras y Ventas (RCV) descargados del SII.

Módulo puro: no accede a la base de datos ni a la red. Reconoce solo los formatos
declarados aquí (nombre de archivo y encabezado exactos) y rechaza cualquier
variación con un error explícito. Los valores del CSV se tratan siempre como datos.
Ver `docs/importacion-rcv-sii.md` y ADR 0009.
"""
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from organizations.rut import InvalidRut, parse_rut

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_DATA_ROWS = 20000

PURCHASE, SALE = "PURCHASE", "SALE"
SUPPORTED_DOCUMENT_TYPES = {33: "Factura electrónica", 34: "Factura exenta electrónica", 56: "Nota de débito electrónica", 61: "Nota de crédito electrónica"}
EXEMPT_INVOICE, CREDIT_NOTE = 34, 61
AMOUNT_FIELDS = ("exempt_amount", "net_amount", "vat_amount", "vat_non_recoverable_amount", "vat_common_use_amount", "total_amount")


@dataclass(frozen=True)
class RcvFormat:
    code: str
    direction: str
    is_summary: bool
    filename: re.Pattern
    header: tuple


PURCHASE_DETAIL_HEADER = (
    "Nro", "Tipo Doc", "Tipo Compra", "RUT Proveedor", "Razon Social", "Folio", "Fecha Docto",
    "Fecha Recepcion", "Fecha Acuse", "Monto Exento", "Monto Neto", "Monto IVA Recuperable",
    "Monto Iva No Recuperable", "Codigo IVA No Rec.", "Monto Total", "Monto Neto Activo Fijo",
    "IVA Activo Fijo", "IVA uso Comun", "Impto. Sin Derecho a Credito", "IVA No Retenido",
    "Tabacos Puros", "Tabacos Cigarrillos", "Tabacos Elaborados", "NCE o NDE sobre Fact. de Compra",
    "Codigo Otro Impuesto", "Valor Otro Impuesto", "Tasa Otro Impuesto",
)
SALE_DETAIL_HEADER = (
    "Nro", "Tipo Doc", "Tipo Venta", "Rut cliente", "Razon Social", "Folio", "Fecha Docto",
    "Fecha Recepcion", "Fecha Acuse Recibo", "Fecha Reclamo", "Monto Exento", "Monto Neto",
    "Monto IVA", "Monto total", "IVA Retenido Total", "IVA Retenido Parcial", "IVA no retenido",
    "IVA propio", "IVA Terceros", "RUT Emisor Liquid. Factura", "Neto Comision Liquid. Factura",
    "Exento Comision Liquid. Factura", "IVA Comision Liquid. Factura", "IVA fuera de plazo",
    "Tipo Docto. Referencia", "Folio Docto. Referencia", "Num. Ident. Receptor Extranjero",
    "Nacionalidad Receptor Extranjero", "Credito empresa constructora", "Impto. Zona Franca (Ley 18211)",
    "Garantia Dep. Envases", "Indicador Venta sin Costo", "Indicador Servicio Periodico",
    "Monto No facturable", "Total Monto Periodo", "Venta Pasajes Transporte Nacional",
    "Venta Pasajes Transporte Internacional", "Numero Interno", "Codigo Sucursal",
    "NCE o NDE sobre Fact. de Compra", "Codigo Otro Imp.", "Valor Otro Imp.", "Tasa Otro Imp.",
)
PURCHASE_SUMMARY_HEADER = (
    "Tipo Documento", "Total Documentos", "Monto Exento", "Monto Neto", "IVA Recuperable",
    "IVA Uso Comun", "IVA No Recuperable", "Monto Total",
)
SALE_SUMMARY_HEADER = ("Tipo Documento", "Total Documentos", "Monto Exento", "Monto Neto", "Monto IVA", "Monto Total")

FORMATS = (
    RcvFormat("sii-rcv-compra-registro-v1", PURCHASE, False,
              re.compile(r"^RCV_COMPRA_REGISTRO_(?P<rut>\d{1,8}-[0-9Kk])_(?P<period>\d{6})\.csv$"), PURCHASE_DETAIL_HEADER),
    RcvFormat("sii-rcv-venta-v1", SALE, False,
              re.compile(r"^RCV_VENTA_(?P<rut>\d{1,8}-[0-9Kk])_(?P<period>\d{6})\.csv$"), SALE_DETAIL_HEADER),
    RcvFormat("sii-rcv-resumen-compra-registro-v1", PURCHASE, True,
              re.compile(r"^RCV_RESUMEN_COMPRA_REGISTRO_(?P<rut_body>\d{1,8})_(?P<period>\d{6})\.csv$"), PURCHASE_SUMMARY_HEADER),
    RcvFormat("sii-rcv-resumen-venta-v1", SALE, True,
              re.compile(r"^RCV_RESUMEN_VENTA_(?P<rut_body>\d{1,8})_(?P<period>\d{6})\.csv$"), SALE_SUMMARY_HEADER),
)

# Columnas tributarias que este importador no modela. Si traen un valor distinto de
# vacío o cero, la fila se rechaza en vez de perder ese componente.
PURCHASE_UNSUPPORTED = (
    "Monto Neto Activo Fijo", "IVA Activo Fijo", "Impto. Sin Derecho a Credito", "IVA No Retenido",
    "Tabacos Puros", "Tabacos Cigarrillos", "Tabacos Elaborados", "NCE o NDE sobre Fact. de Compra",
    "Codigo Otro Impuesto", "Valor Otro Impuesto", "Tasa Otro Impuesto",
)
SALE_UNSUPPORTED = (
    "IVA Retenido Total", "IVA Retenido Parcial", "IVA no retenido", "IVA propio", "IVA Terceros",
    "RUT Emisor Liquid. Factura", "Neto Comision Liquid. Factura", "Exento Comision Liquid. Factura",
    "IVA Comision Liquid. Factura", "IVA fuera de plazo", "Credito empresa constructora",
    "Impto. Zona Franca (Ley 18211)", "Garantia Dep. Envases", "Monto No facturable",
    "Venta Pasajes Transporte Nacional", "Venta Pasajes Transporte Internacional",
    "NCE o NDE sobre Fact. de Compra", "Codigo Otro Imp.", "Valor Otro Imp.", "Tasa Otro Imp.",
)


class RcvError(ValueError):
    pass


@dataclass(frozen=True)
class RcvFileName:
    name: str
    format: RcvFormat
    period: date
    rut_text: str = ""   # detalle: "12345678-9" tal como viene en el nombre
    rut_body: int = 0    # resumen: el nombre trae solo el cuerpo del RUT


@dataclass
class RcvDocument:
    line: int
    document_type: int
    folio: int
    counterparty_tax_id: str
    counterparty_name: str
    transaction_type: str
    issue_date: date
    sii_received_at: datetime | None
    exempt_amount: int
    net_amount: int
    vat_amount: int
    vat_non_recoverable_amount: int
    vat_non_recoverable_code: str
    vat_common_use_amount: int
    total_amount: int
    reference_document_type: int | None = None
    reference_folio: int | None = None


@dataclass
class RcvSummaryRow:
    line: int
    document_type: int
    label: str
    count: int
    amounts: dict


@dataclass
class RcvFile:
    file_name: RcvFileName
    sha256: str
    documents: list = field(default_factory=list)
    summary_rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def classify_filename(name):
    """Devuelve `RcvFileName`, `None` si no es un archivo RCV, o lanza `RcvError` si es un RCV no soportado."""
    for fmt in FORMATS:
        match = fmt.filename.match(name)
        if match:
            period = parse_period(match.group("period"))
            if fmt.is_summary:
                return RcvFileName(name, fmt, period, rut_body=int(match.group("rut_body")))
            return RcvFileName(name, fmt, period, rut_text=match.group("rut").upper())
    if name.upper().startswith("RCV_"):
        raise RcvError("Archivo RCV con nombre no soportado (solo COMPRA_REGISTRO, VENTA y sus resúmenes).")
    return None


def parse_period(text):
    if not re.fullmatch(r"\d{6}", text or ""):
        raise RcvError("Período inválido; se espera AAAAMM.")
    year, month = int(text[:4]), int(text[4:])
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise RcvError("Período inválido; se espera AAAAMM.")
    return date(year, month, 1)


def file_owner_rut(file_name):
    """RUT del titular desde el nombre de un detalle; lanza `InvalidRut` si el DV no corresponde."""
    return parse_rut(file_name.rut_text)


def decode(content):
    if len(content) > MAX_FILE_BYTES:
        raise RcvError(f"El archivo supera {MAX_FILE_BYTES // (1024 * 1024)} MiB.")
    if b"\x00" in content:
        raise RcvError("El archivo contiene bytes nulos; no parece un CSV de texto.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RcvError(f"El archivo no está en UTF-8 (byte inválido en la posición {exc.start}).") from exc
    if not text.strip():
        raise RcvError("El archivo está vacío.")
    return text


def _rows(text):
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=";", quotechar='"', strict=True))
    except csv.Error as exc:
        raise RcvError(f"CSV mal formado: {exc}.") from exc


def _trim_trailing(cells, width):
    """Tolera exactamente un delimitador sobrante al final (celda vacía extra)."""
    if len(cells) == width + 1 and cells[-1].strip() == "":
        return cells[:-1]
    return cells


def _check_header(cells, expected):
    header = tuple(cell.strip() for cell in _trim_trailing(cells, len(expected)))
    if header == expected:
        return
    if len(header) != len(expected):
        raise RcvError(f"Encabezado no reconocido: se esperaban {len(expected)} columnas y hay {len(header)}.")
    index = next(i for i, (got, want) in enumerate(zip(header, expected)) if got != want)
    raise RcvError(f"Encabezado no reconocido en la columna {index + 1}: se esperaba «{expected[index]}».")


def parse_amount(value, column):
    value = value.strip()
    if value == "":
        return 0
    if not re.fullmatch(r"\d{1,15}", value):
        raise RcvError(f"«{column}» debe ser un monto CLP entero sin separadores.")
    return int(value)


def parse_positive_int(value, column):
    value = value.strip()
    if not re.fullmatch(r"\d{1,18}", value) or int(value) == 0:
        raise RcvError(f"«{column}» debe ser un entero positivo.")
    return int(value)


def parse_date(value, column):
    value = value.strip()
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
        raise RcvError(f"«{column}» debe tener formato DD/MM/AAAA.")
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError as exc:
        raise RcvError(f"«{column}» no es una fecha válida.") from exc


def parse_optional_datetime(value, column):
    value = value.strip()
    if value == "":
        return None
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", value):
        raise RcvError(f"«{column}» debe tener formato DD/MM/AAAA HH:MM:SS.")
    try:
        return datetime.strptime(value, "%d/%m/%Y %H:%M:%S")
    except ValueError as exc:
        raise RcvError(f"«{column}» no es una fecha y hora válida.") from exc


def _text(value, column, max_length):
    value = value.strip()
    if len(value) > max_length:
        raise RcvError(f"«{column}» supera {max_length} caracteres.")
    return value


def _is_zero_or_empty(value):
    # El SII escribe «-» en columnas de RUT sin valor (p. ej. «RUT Emisor Liquid. Factura»).
    return value.strip() in ("", "0", "-")


def _document(row, line, fmt):
    is_sale = fmt.direction == SALE
    doc_type_text = row["Tipo Doc"].strip()
    if not re.fullmatch(r"\d{1,4}", doc_type_text) or int(doc_type_text) not in SUPPORTED_DOCUMENT_TYPES:
        raise RcvError(f"Tipo de documento SII «{doc_type_text[:10]}» no soportado (soportados: {', '.join(map(str, SUPPORTED_DOCUMENT_TYPES))}).")
    document_type = int(doc_type_text)
    unsupported = SALE_UNSUPPORTED if is_sale else PURCHASE_UNSUPPORTED
    present = [column for column in unsupported if not _is_zero_or_empty(row[column])]
    if present:
        raise RcvError("Componentes no soportados por este importador: " + ", ".join(f"«{c}»" for c in present) + ".")
    if is_sale and row["Fecha Reclamo"].strip():
        raise RcvError("El documento tiene un reclamo registrado; requiere revisión manual.")
    rut_column = "Rut cliente" if is_sale else "RUT Proveedor"
    try:
        counterparty = parse_rut(row[rut_column]).compact
    except InvalidRut as exc:
        raise RcvError(f"«{rut_column}» inválido: {exc}") from exc
    if is_sale:
        amounts = {
            "exempt_amount": parse_amount(row["Monto Exento"], "Monto Exento"),
            "net_amount": parse_amount(row["Monto Neto"], "Monto Neto"),
            "vat_amount": parse_amount(row["Monto IVA"], "Monto IVA"),
            "vat_non_recoverable_amount": 0, "vat_common_use_amount": 0,
            "total_amount": parse_amount(row["Monto total"], "Monto total"),
        }
        code = ""
    else:
        amounts = {
            "exempt_amount": parse_amount(row["Monto Exento"], "Monto Exento"),
            "net_amount": parse_amount(row["Monto Neto"], "Monto Neto"),
            "vat_amount": parse_amount(row["Monto IVA Recuperable"], "Monto IVA Recuperable"),
            "vat_non_recoverable_amount": parse_amount(row["Monto Iva No Recuperable"], "Monto Iva No Recuperable"),
            "vat_common_use_amount": parse_amount(row["IVA uso Comun"], "IVA uso Comun"),
            "total_amount": parse_amount(row["Monto Total"], "Monto Total"),
        }
        code = row["Codigo IVA No Rec."].strip()
        if code and not re.fullmatch(r"\d{1,4}", code):
            raise RcvError("«Codigo IVA No Rec.» debe ser numérico.")
    components = sum(value for key, value in amounts.items() if key != "total_amount")
    if components != amounts["total_amount"]:
        raise RcvError(f"Exento + neto + IVA ({components}) no coincide con el total ({amounts['total_amount']}).")
    if document_type == EXEMPT_INVOICE and (amounts["net_amount"] or amounts["vat_amount"] or amounts["vat_non_recoverable_amount"] or amounts["vat_common_use_amount"]):
        raise RcvError("Una factura exenta (34) no puede informar neto ni IVA.")
    reference_type = reference_folio = None
    if is_sale:
        ref_type_text, ref_folio_text = row["Tipo Docto. Referencia"].strip(), row["Folio Docto. Referencia"].strip()
        if not _is_zero_or_empty(ref_type_text) or not _is_zero_or_empty(ref_folio_text):
            reference_type = parse_positive_int(ref_type_text, "Tipo Docto. Referencia")
            reference_folio = parse_positive_int(ref_folio_text, "Folio Docto. Referencia")
            if reference_type > 32767:
                raise RcvError("«Tipo Docto. Referencia» fuera de rango.")
    return RcvDocument(
        line=line,
        document_type=document_type,
        folio=parse_positive_int(row["Folio"], "Folio"),
        counterparty_tax_id=counterparty,
        counterparty_name=_text(row["Razon Social"], "Razon Social", 200),
        transaction_type=_text(row["Tipo Venta" if is_sale else "Tipo Compra"], "Tipo de operación", 40),
        issue_date=parse_date(row["Fecha Docto"], "Fecha Docto"),
        sii_received_at=parse_optional_datetime(row["Fecha Recepcion"], "Fecha Recepcion"),
        vat_non_recoverable_code=code,
        reference_document_type=reference_type,
        reference_folio=reference_folio,
        **amounts,
    )


def _summary_row(row, line, fmt):
    label = row["Tipo Documento"].strip()
    match = re.fullmatch(r".*\((\d{1,4})\)", label)
    if not match:
        raise RcvError("«Tipo Documento» debe terminar con el código SII entre paréntesis.")
    columns = (
        {"exempt_amount": "Monto Exento", "net_amount": "Monto Neto", "vat_amount": "Monto IVA", "total_amount": "Monto Total"}
        if fmt.direction == SALE else
        {"exempt_amount": "Monto Exento", "net_amount": "Monto Neto", "vat_amount": "IVA Recuperable",
         "vat_common_use_amount": "IVA Uso Comun", "vat_non_recoverable_amount": "IVA No Recuperable", "total_amount": "Monto Total"}
    )
    amounts = {key: parse_amount(row[column], column) for key, column in columns.items()}
    count_text = row["Total Documentos"].strip()
    if not re.fullmatch(r"\d{1,9}", count_text):
        raise RcvError("«Total Documentos» debe ser un entero.")
    return RcvSummaryRow(line=line, document_type=int(match.group(1)), label=label[:80], count=int(count_text), amounts=amounts)


def parse_file(file_name, content):
    """Lee un archivo RCV completo. Los errores de archivo o de fila quedan en `errors` (con número de línea)."""
    result = RcvFile(file_name=file_name, sha256=hashlib.sha256(content).hexdigest())
    fmt = file_name.format
    try:
        rows = _rows(decode(content))
        _check_header(rows[0], fmt.header)
    except RcvError as exc:
        result.errors.append(str(exc))
        return result
    data = [(number, cells) for number, cells in enumerate(rows[1:], start=2) if any(cell.strip() for cell in cells)]
    if len(data) > MAX_DATA_ROWS:
        result.errors.append(f"El archivo supera {MAX_DATA_ROWS} filas de datos.")
        return result
    if not data and not fmt.is_summary:
        result.errors.append("El archivo no contiene documentos.")
    seen = {}
    for line, cells in data:
        cells = _trim_trailing(cells, len(fmt.header))
        if len(cells) != len(fmt.header):
            result.errors.append(f"Línea {line}: se esperaban {len(fmt.header)} columnas y hay {len(cells)}.")
            continue
        row = dict(zip(fmt.header, cells))
        try:
            if fmt.is_summary:
                item = _summary_row(row, line, fmt)
                key = item.document_type
                container = result.summary_rows
            else:
                item = _document(row, line, fmt)
                key = (item.document_type, item.counterparty_tax_id if fmt.direction == PURCHASE else "", item.folio)
                container = result.documents
        except RcvError as exc:
            result.errors.append(f"Línea {line}: {exc}")
            continue
        if key in seen:
            result.errors.append(f"Línea {line}: repite el documento de la línea {seen[key]}.")
            continue
        seen[key] = line
        container.append(item)
    return result
