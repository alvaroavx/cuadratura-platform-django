"""Importa documentos tributarios desde CSV del RCV del SII (ADR 0009, docs/importacion-rcv-sii.md).

Simula por defecto. Solo escribe con --apply y únicamente si el plan no tiene errores bloqueantes.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from documents import rcv
from documents.rcv_import import (
    CONFLICT, EXISTING, NEW, REF_LINKABLE, REF_LINKED, REF_PENDING, PlanBlocked, apply_plan, build_plan, economic_summary,
)

DIRECTIONS = {"compras": rcv.PURCHASE, "ventas": rcv.SALE}
LABEL = {rcv.PURCHASE: "Compras", rcv.SALE: "Ventas"}
STATUS = {NEW: "nuevo", EXISTING: "ya registrado", CONFLICT: "CONFLICTO"}
REFERENCE = {REF_LINKED: "vinculada", REF_LINKABLE: "se vincula", REF_PENDING: "original pendiente"}


def clp(value):
    return "$" + f"{value:,}".replace(",", ".")


class Command(BaseCommand):
    help = "Importa documentos tributarios desde CSV del Registro de Compras y Ventas del SII. Simula salvo --apply."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Simula sin escribir (comportamiento por defecto).")
        mode.add_argument("--apply", action="store_true", help="Registra los documentos nuevos en una sola transacción.")
        parser.add_argument("--path", default=str(settings.BASE_DIR / "data"), help="Carpeta con los CSV (no se recorre recursivamente).")
        parser.add_argument("--file", action="append", dest="files", metavar="NOMBRE", help="Procesa solo este archivo de --path (repetible).")
        parser.add_argument("--period", action="append", dest="periods", metavar="AAAAMM", help="Filtra por período tributario (repetible).")
        parser.add_argument("--direction", choices=sorted(DIRECTIONS), help="Filtra compras o ventas.")
        parser.add_argument("--rut", help="RUT del contribuyente, obligatorio si el nombre del archivo no trae un RUT válido.")

    def handle(self, *args, **options):
        try:
            periods = {rcv.parse_period(text) for text in options["periods"] or []}
        except rcv.RcvError as exc:
            raise CommandError(f"--period: {exc}") from exc
        plan = build_plan(
            options["path"], files=options["files"], periods=periods or None,
            direction=DIRECTIONS.get(options["direction"]), rut=options["rut"],
        )
        apply = options["apply"]
        self.stdout.write(f"Importación RCV — modo {'APLICAR' if apply else 'SIMULACIÓN (sin escrituras)'}")
        self.stdout.write(f"Carpeta: {options['path']} · archivos no RCV ignorados: {plan.ignored_files}")
        for message in plan.warnings:
            self.stdout.write(self.style.WARNING(f"Aviso: {message}"))
        for message in plan.errors:
            self.stdout.write(self.style.ERROR(f"Error: {message}"))
        if not plan.files and not plan.errors:
            self.stdout.write("No hay archivos de detalle RCV para procesar.")
        for file_plan in plan.files:
            self._report_file(file_plan)
        for summary, messages in plan.orphan_summaries:
            self.stdout.write(f"\nResumen {summary.file_name.name}: " + " ".join(messages))
        if plan.blocking:
            raise CommandError("Hay errores bloqueantes; no se importó nada. Corrija o seleccione archivos con --file/--period.")
        if not apply:
            self.stdout.write(self.style.SUCCESS("\nSimulación terminada. Nada fue escrito. Use --apply para registrar."))
            return
        try:
            results = apply_plan(plan)
        except (PlanBlocked, DatabaseError) as exc:
            raise CommandError(f"Importación revertida completa: {exc}") from exc
        self.stdout.write("")
        for result in results:
            self.stdout.write(self.style.SUCCESS(
                f"{result.name}: {result.created} creados, {result.existing} ya registrados, "
                f"{result.conflicts} conflictos, {result.linked} referencias vinculadas."
            ))

    def _report_file(self, plan):
        org = plan.organization
        self.stdout.write(f"\n== {plan.name}")
        self.stdout.write(f"   {LABEL[plan.direction]} · período {plan.period:%Y-%m} · formato {plan.parsed.file_name.format.code}")
        if org is not None:
            self.stdout.write(f"   Contribuyente: {org.legal_name} ({org.tax_id})")
        for message in plan.warnings:
            self.stdout.write(self.style.WARNING(f"   Aviso: {message}"))
        for message in plan.errors:
            self.stdout.write(self.style.ERROR(f"   Error: {message}"))
        if plan.errors:
            self.stdout.write(self.style.ERROR(f"   Archivo bloqueado: {len(plan.parsed.documents)} válidos, {len(plan.parsed.errors)} errores de lectura."))
            return
        self.stdout.write(f"   Documentos: {len(plan.documents)} · nuevos {plan.count(NEW)} · ya registrados {plan.count(EXISTING)} · conflictos {plan.count(CONFLICT)} · inválidos 0")
        for item in plan.documents:
            values = item.values
            line = (
                f"   L{item.document.line:<4} tipo {values['document_type']:<3} folio {values['folio']:<10} "
                f"{values['issue_date']:%d/%m/%Y}  {'emisor' if plan.direction == rcv.PURCHASE else 'cliente'} {values['counterparty_tax_id']:<11} "
                f"total {clp(values['total_amount']):>12}  {STATUS[item.status]}"
            )
            if values["reference_folio"] is not None:
                line += f"  ref. tipo {values['reference_document_type']} folio {values['reference_folio']} ({REFERENCE[item.reference_status]})"
            self.stdout.write(self.style.ERROR(line) if item.status == CONFLICT else line)
            if item.differences:
                self.stdout.write(self.style.ERROR(f"         difiere en: {', '.join(item.differences)} (se conserva lo registrado)"))
            if values["document_type"] == rcv.CREDIT_NOTE and values["reference_folio"] is None:
                self.stdout.write(self.style.WARNING("         nota de crédito sin documento de referencia en el RCV"))
        if plan.pending_links:
            self.stdout.write(f"   Referencias pendientes de importaciones anteriores que se completan: {plan.pending_links}")
        summary = economic_summary(plan.parsed.documents)
        for document_type, totals in sorted(summary["by_type"].items()):
            self.stdout.write(
                f"   Tipo {document_type} ({rcv.SUPPORTED_DOCUMENT_TYPES[document_type]}): {totals['count']} doc. · exento {clp(totals['exempt_amount'])} · "
                f"neto {clp(totals['net_amount'])} · IVA {clp(totals['vat_amount'])}"
                + (f" · IVA no rec. {clp(totals['vat_non_recoverable_amount'])} · IVA uso común {clp(totals['vat_common_use_amount'])}" if plan.direction == rcv.PURCHASE else "")
                + f" · total {clp(totals['total_amount'])}"
            )
        net = summary["net_effect"]
        self.stdout.write(
            f"   Facturas y notas de débito: {clp(summary['documents_total'])} · notas de crédito: {clp(summary['credit_notes_total'])} · "
            f"efecto neto: total {clp(net['total_amount'])} (neto {clp(net['net_amount'])}, exento {clp(net['exempt_amount'])}, IVA {clp(net['vat_amount'])})"
        )
        if plan.summary is None or plan.summary.errors:
            self.stdout.write(self.style.WARNING("   Conciliación con resumen: no realizada."))
        elif plan.reconciliation:
            self.stdout.write(self.style.WARNING(f"   Conciliación con {plan.summary.file_name.name}: {len(plan.reconciliation)} diferencias"))
            for message in plan.reconciliation:
                self.stdout.write(self.style.WARNING(f"     - {message}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"   Conciliación con {plan.summary.file_name.name}: cuadra (cantidad y montos por tipo)."))
