"""Planificación y aplicación de la importación RCV (ADR 0009).

`build_plan` lee, valida, resuelve el contribuyente y clasifica cada documento contra la
base sin escribir. `apply_plan` repite la clasificación bajo bloqueo de cada
contribuyente y persiste todo en una sola transacción. Ambas funciones son reutilizables
desde una futura vista o integración; el comando `importar_rcv` solo las orquesta.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from audit.services import record_event
from organizations.models import TaxpayerOrganization
from organizations.rut import InvalidRut, parse_rut, same_rut_text
from reports.models import MonthlyClose

from . import rcv
from .models import TaxDocument

SII_TIMEZONE = ZoneInfo("America/Santiago")
NEW, EXISTING, CONFLICT = "NEW", "EXISTING", "CONFLICT"
REF_NONE, REF_LINKED, REF_LINKABLE, REF_PENDING = "", "LINKED", "LINKABLE", "PENDING"
COMPARED_FIELDS = (
    "counterparty_tax_id", "counterparty_name", "transaction_type", "issue_date", "tax_period", "sii_received_at",
    *rcv.AMOUNT_FIELDS, "vat_non_recoverable_code", "reference_document_type", "reference_folio",
)
POSITIVE_TYPES = (33, 34, 56)


@dataclass
class DocumentPlan:
    document: rcv.RcvDocument
    values: dict
    status: str = NEW
    differences: list = field(default_factory=list)
    reference_status: str = REF_NONE

    @property
    def key(self):
        return (self.values["document_type"], self.values["issuer_tax_id"], self.values["folio"])


@dataclass
class FilePlan:
    parsed: rcv.RcvFile
    organization: TaxpayerOrganization | None = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    summary: rcv.RcvFile | None = None
    reconciliation: list = field(default_factory=list)
    pending_links: int = 0  # documentos ya registrados cuya referencia se completaría

    @property
    def name(self): return self.parsed.file_name.name
    @property
    def direction(self): return self.parsed.file_name.format.direction
    @property
    def period(self): return self.parsed.file_name.period

    def count(self, status): return sum(1 for item in self.documents if item.status == status)


@dataclass
class ImportPlan:
    registry_ready: bool = True  # False: la migración de TaxDocument no está aplicada (solo simulación)
    files: list = field(default_factory=list)
    orphan_summaries: list = field(default_factory=list)  # (RcvFile, [mensajes])
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    ignored_files: int = 0

    @property
    def blocking(self):
        return bool(self.errors) or any(file.errors for file in self.files)


@dataclass
class FileResult:
    name: str
    created: int = 0
    existing: int = 0
    conflicts: int = 0
    linked: int = 0


# --- Lectura y selección -----------------------------------------------------------

def scan_directory(directory, *, files=None, periods=None, direction=None):
    """Devuelve ([(Path, RcvFileName)], ignorados, advertencias, errores). Nunca recorre subcarpetas."""
    base = Path(directory)
    if not base.is_dir():
        return [], 0, [], [f"La carpeta {directory} no existe."]
    selected, warnings, errors, ignored = [], [], [], 0
    if files:
        candidates = []
        for name in files:
            if Path(name).name != name:
                errors.append(f"{name}: indique solo el nombre del archivo dentro de --path.")
            elif not (base / name).is_file():
                errors.append(f"{name}: no existe en {directory}.")
            else:
                candidates.append(base / name)
    else:
        candidates = sorted(entry for entry in base.iterdir() if entry.is_file())
    for entry in candidates:
        try:
            file_name = rcv.classify_filename(entry.name)
        except rcv.RcvError as exc:
            (errors if files else warnings).append(f"{entry.name}: {exc}")
            continue
        if file_name is None:
            if files:
                errors.append(f"{entry.name}: no es un archivo RCV soportado.")
            ignored += 1
            continue
        if periods and file_name.period not in periods:
            continue
        if direction and file_name.format.direction != direction:
            continue
        selected.append((entry, file_name))
    return selected, ignored, warnings, errors


def _read(path):
    if path.stat().st_size > rcv.MAX_FILE_BYTES:
        raise rcv.RcvError(f"El archivo supera {rcv.MAX_FILE_BYTES // (1024 * 1024)} MiB.")
    with path.open("rb") as handle:
        return handle.read(rcv.MAX_FILE_BYTES + 1)


def _parse(path, file_name):
    try:
        return rcv.parse_file(file_name, _read(path))
    except (OSError, rcv.RcvError) as exc:
        parsed = rcv.RcvFile(file_name=file_name, sha256="")
        parsed.errors.append(f"No se pudo leer: {exc}")
        return parsed


# --- Contribuyente -------------------------------------------------------------------

def resolve_organization(rut):
    """Busca el contribuyente por RUT normalizado. Devuelve (organización, error)."""
    matches = [org for org in TaxpayerOrganization.objects.all() if same_rut_text(rut.compact, org.tax_id)]
    if not matches:
        return None, f"No existe un contribuyente con RUT {rut.formatted}; créelo antes de importar."
    if len(matches) > 1:
        return None, f"Hay {len(matches)} contribuyentes con RUT {rut.formatted}; corrija la duplicidad antes de importar."
    org = matches[0]
    if org.archived_at is not None:
        return None, f"El contribuyente {rut.formatted} está eliminado (archivado)."
    if not org.is_active:
        return None, f"El contribuyente {rut.formatted} está inactivo."
    return org, None


def _detail_owner(file_name, explicit):
    try:
        owner = rcv.file_owner_rut(file_name)
    except InvalidRut:
        owner = None
    if explicit and owner and owner != explicit:
        return None, None, f"El RUT del nombre ({owner.formatted}) no coincide con --rut {explicit.formatted}."
    if owner is None:
        if not explicit:
            return None, None, "El nombre no trae un RUT válido (dígito verificador); indique el contribuyente con --rut."
        return explicit, "El nombre no trae un RUT válido; se usa el contribuyente indicado con --rut.", None
    return owner, None, None


# --- Clasificación ---------------------------------------------------------------------

def _document_values(document, *, direction, issuer_tax_id, period):
    received = document.sii_received_at
    return {
        "direction": direction,
        "document_type": document.document_type,
        "issuer_tax_id": issuer_tax_id,
        "folio": document.folio,
        "counterparty_tax_id": document.counterparty_tax_id,
        "counterparty_name": document.counterparty_name,
        "transaction_type": document.transaction_type,
        "issue_date": document.issue_date,
        "tax_period": period,
        "sii_received_at": timezone.make_aware(received, SII_TIMEZONE) if received else None,
        **{name: getattr(document, name) for name in rcv.AMOUNT_FIELDS},
        "vat_non_recoverable_code": document.vat_non_recoverable_code,
        "reference_document_type": document.reference_document_type,
        "reference_folio": document.reference_folio,
    }


def _doc_key(obj):
    return (obj.document_type, obj.issuer_tax_id, obj.folio)


REQUIRED_MIGRATIONS = {("documents", "0002_tax_document"), ("documents", "0003_tax_document_guard")}


def registry_ready():
    """Indica si están aplicadas la tabla de documentos y su trigger de inmutabilidad."""
    return REQUIRED_MIGRATIONS <= set(MigrationRecorder(connection).applied_migrations())


def classify(plan, *, use_database=True):
    """Clasifica los documentos de un detalle contra la base (nuevo, existente idéntico o conflicto).

    Con `use_database=False` (tabla aún no migrada) todo documento se considera nuevo.
    """
    org, direction = plan.organization, plan.direction
    existing = {} if not use_database else {
        _doc_key(doc): doc
        for doc in TaxDocument.objects.filter(
            organization=org, direction=direction, folio__in={item.values["folio"] for item in plan.documents}
        )
    }
    for item in plan.documents:
        current = existing.get(item.key)
        item.differences = []
        if current is None:
            item.status = NEW
            continue
        item.differences = [name for name in COMPARED_FIELDS if getattr(current, name) != item.values[name]]
        item.status = CONFLICT if item.differences else EXISTING
    in_file = {item.key for item in plan.documents if item.status != CONFLICT}
    for item in plan.documents:
        values = item.values
        if values["reference_folio"] is None:
            item.reference_status = REF_NONE
            continue
        target = (values["reference_document_type"], values["issuer_tax_id"], values["reference_folio"])
        current = existing.get(item.key)
        if current is not None and current.referenced_document_id:
            item.reference_status = REF_LINKED
        elif target in in_file or (use_database and _target_exists(org, direction, target)):
            item.reference_status = REF_LINKABLE
        else:
            item.reference_status = REF_PENDING
    if not use_database:
        plan.pending_links = 0
        return
    in_file_ids = {doc.pk for doc in existing.values()}
    plan.pending_links = sum(
        1 for doc in _pending_references(org).filter(direction=direction)
        if doc.pk not in in_file_ids and (doc.reference_document_type, doc.issuer_tax_id, doc.reference_folio) in in_file
    )


def _target_exists(org, direction, key):
    document_type, issuer, folio = key
    return TaxDocument.objects.filter(organization=org, direction=direction, document_type=document_type, issuer_tax_id=issuer, folio=folio).exists()


def _pending_references(org):
    return TaxDocument.objects.filter(organization=org, reference_folio__isnull=False, referenced_document__isnull=True)


def _closed(org, period):
    return MonthlyClose.objects.filter(organization=org, period=period, status=MonthlyClose.Status.CLOSED).exists()


# --- Conciliación con resúmenes -------------------------------------------------------

def totals_by_type(items):
    """Agrupa por tipo SII: cantidad y componentes. Acepta `RcvDocument` o `TaxDocument`."""
    totals = defaultdict(lambda: {"count": 0, **{name: 0 for name in rcv.AMOUNT_FIELDS}})
    for item in items:
        bucket = totals[item.document_type]
        bucket["count"] += 1
        for name in rcv.AMOUNT_FIELDS:
            bucket[name] += getattr(item, name)
    return dict(totals)


def economic_summary(items):
    """Totales brutos (sin signo) y efecto económico: facturas y notas de débito suman, notas de crédito restan."""
    by_type = totals_by_type(items)
    positive = {name: sum(by_type.get(t, {}).get(name, 0) for t in POSITIVE_TYPES) for name in rcv.AMOUNT_FIELDS}
    credit = {name: by_type.get(rcv.CREDIT_NOTE, {}).get(name, 0) for name in rcv.AMOUNT_FIELDS}
    return {
        "by_type": by_type,
        "documents_total": positive["total_amount"],
        "credit_notes_total": credit["total_amount"],
        "net_effect": {name: positive[name] - credit[name] for name in rcv.AMOUNT_FIELDS},
    }


def reconcile(summary, documents):
    """Compara el resumen del SII con el detalle por tipo. Devuelve la lista de diferencias (vacía si cuadra)."""
    detail = totals_by_type(documents)
    differences = []
    declared = {row.document_type: row for row in summary.summary_rows}
    for document_type in sorted(set(declared) | set(detail)):
        row = declared.get(document_type)
        got = detail.get(document_type, {"count": 0, **{name: 0 for name in rcv.AMOUNT_FIELDS}})
        if row is None:
            differences.append(f"Tipo {document_type}: está en el detalle ({got['count']} documentos) y no en el resumen.")
            continue
        if row.count != got["count"]:
            differences.append(f"Tipo {document_type}: resumen {row.count} documentos, detalle {got['count']}.")
        for name, value in row.amounts.items():
            if value != got[name]:
                differences.append(f"Tipo {document_type}: {name} resumen {value}, detalle {got[name]}.")
    return differences


# --- Plan ---------------------------------------------------------------------------------

def build_plan(directory, *, files=None, periods=None, direction=None, rut=None):
    """Lee y valida sin escribir. `rut` (texto) identifica explícitamente al contribuyente."""
    plan = ImportPlan(registry_ready=registry_ready())
    if not plan.registry_ready:
        plan.warnings.append("Las migraciones documents.0002 y 0003 no están aplicadas: se simula como si no hubiera documentos registrados.")
    explicit = None
    if rut:
        try:
            explicit = parse_rut(rut)
        except InvalidRut as exc:
            plan.errors.append(f"--rut inválido: {exc}")
            return plan
    selected, plan.ignored_files, warnings, errors = scan_directory(directory, files=files, periods=periods, direction=direction)
    plan.warnings.extend(warnings)
    plan.errors.extend(errors)
    summaries = {}
    for path, file_name in selected:
        if file_name.format.is_summary:
            summaries[(file_name.format.direction, file_name.rut_body, file_name.period)] = _parse(path, file_name)
            continue
        file_plan = FilePlan(parsed=_parse(path, file_name))
        plan.files.append(file_plan)
        owner, warning, error = _detail_owner(file_name, explicit)
        if warning:
            file_plan.warnings.append(warning)
        if error:
            file_plan.errors.append(error)
            continue
        file_plan.organization, error = resolve_organization(owner)
        if error:
            file_plan.errors.append(error)
            continue
        if _closed(file_plan.organization, file_plan.period):
            file_plan.errors.append(f"El período {file_plan.period:%Y-%m} está cerrado; reábralo antes de importar.")
        file_plan.errors.extend(file_plan.parsed.errors)
        if file_plan.errors:
            continue
        issuer = owner.compact
        file_plan.documents = [
            DocumentPlan(
                document=document,
                values=_document_values(
                    document, direction=file_plan.direction, period=file_plan.period,
                    issuer_tax_id=issuer if file_plan.direction == rcv.SALE else document.counterparty_tax_id,
                ),
            )
            for document in file_plan.parsed.documents
        ]
        if file_plan.direction == rcv.SALE:
            for item in file_plan.documents:
                if same_rut_text(item.values["counterparty_tax_id"], issuer):
                    file_plan.errors.append(f"Línea {item.document.line}: el cliente es el mismo contribuyente emisor.")
        classify(file_plan, use_database=plan.registry_ready)
    for file_plan in plan.files:
        key = (file_plan.direction, _owner_body(file_plan), file_plan.period)
        summary = summaries.pop(key, None)
        if summary is None:
            file_plan.warnings.append("Sin resumen del SII para conciliar.")
            continue
        file_plan.summary = summary
        if summary.errors:
            file_plan.warnings.extend(f"Resumen {summary.file_name.name}: {error}" for error in summary.errors)
        elif not file_plan.parsed.errors:
            file_plan.reconciliation = reconcile(summary, file_plan.parsed.documents)
    for summary in summaries.values():
        messages = list(summary.errors)
        if explicit and summary.file_name.rut_body != explicit.body:
            messages.append(f"El RUT del nombre no coincide con --rut {explicit.formatted}.")
        messages.append("Resumen sin detalle seleccionado; no se concilia.")
        plan.orphan_summaries.append((summary, messages))
    return plan


def _owner_body(file_plan):
    try:
        return rcv.file_owner_rut(file_plan.parsed.file_name).body
    except InvalidRut:
        return int(file_plan.parsed.file_name.rut_text.split("-")[0])


# --- Aplicación -----------------------------------------------------------------------------

class PlanBlocked(Exception):
    pass


def apply_plan(plan):
    """Persiste los documentos nuevos y completa referencias en una sola transacción.

    Bloquea cada contribuyente (`select_for_update`) y vuelve a clasificar dentro del
    bloqueo, de modo que dos ejecuciones simultáneas no dupliquen ni pisen documentos;
    la restricción única de PostgreSQL es la última defensa. Los conflictos se informan
    y no modifican lo existente.
    """
    if plan.blocking:
        raise PlanBlocked("El plan tiene errores bloqueantes; no se importa nada.")
    if not plan.registry_ready:
        raise PlanBlocked("Faltan las migraciones documents.0002 y 0003; no se importa nada.")
    results = []
    with transaction.atomic():
        for org_id in sorted({file.organization.pk for file in plan.files}, key=str):
            TaxpayerOrganization.objects.select_for_update().get(pk=org_id)
        for file_plan in plan.files:
            if _closed(file_plan.organization, file_plan.period):
                raise PlanBlocked(f"{file_plan.name}: el período {file_plan.period:%Y-%m} se cerró durante la importación.")
            classify(file_plan)
            result = FileResult(name=file_plan.name, existing=file_plan.count(EXISTING), conflicts=file_plan.count(CONFLICT))
            for item in file_plan.documents:
                if item.status != NEW:
                    continue
                TaxDocument.objects.create(
                    organization=file_plan.organization,
                    source_format=file_plan.parsed.file_name.format.code,
                    source_filename=file_plan.name,
                    source_sha256=file_plan.parsed.sha256,
                    source_line=item.document.line,
                    **item.values,
                )
                result.created += 1
            results.append(result)
        linked_by_org = {org_id: link_pending_references(org_id) for org_id in {file.organization.pk for file in plan.files}}
        for file_plan, result in zip(plan.files, results):
            result.linked = linked_by_org[file_plan.organization.pk].get((file_plan.direction, file_plan.period), 0)
            if not (result.created or result.linked):
                continue  # una reejecución sin efecto no agrega eventos
            record_event(
                organization=file_plan.organization, actor=None, action="tax_documents.rcv_imported",
                target=file_plan.organization,
                metadata={
                    "via": "importar_rcv", "source_format": file_plan.parsed.file_name.format.code,
                    "source_sha256": file_plan.parsed.sha256, "period": f"{file_plan.period:%Y-%m}",
                    "created": result.created, "existing": result.existing, "conflicts": result.conflicts,
                    "references_linked": result.linked, "summary_reconciled": file_plan.summary is not None and not file_plan.reconciliation,
                },
            )
    return results


def link_pending_references(organization_id):
    """Completa `referenced_document` de los documentos cuyo original ya está registrado.

    El original debe ser del mismo contribuyente, dirección y emisor, con el tipo y folio
    referenciados. Devuelve {(dirección, período del documento que referencia): cantidad}.
    """
    linked = defaultdict(int)
    for doc in _pending_references(organization_id).select_for_update():
        target = TaxDocument.objects.filter(
            organization_id=organization_id, direction=doc.direction, document_type=doc.reference_document_type,
            issuer_tax_id=doc.issuer_tax_id, folio=doc.reference_folio,
        ).first()
        if target is None or target.pk == doc.pk:
            continue
        doc.referenced_document = target
        doc.save(update_fields=["referenced_document"])
        linked[(doc.direction, doc.tax_period)] += 1
    return dict(linked)
