"""Pruebas de la importación RCV con datos ficticios en formato SII (ADR 0009)."""
import tempfile
import threading
from datetime import date
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase

from audit.models import AuditEvent
from documents import rcv
from documents.models import ElectronicReceipt, TaxDocument
from documents.rcv_import import CONFLICT, EXISTING, NEW, REF_PENDING, apply_plan, build_plan, economic_summary
from organizations.models import TaxpayerOrganization
from payments.models import Payment
from reports.models import MonthlyClose
from sales.models import Sale

ORG_RUT, CUSTOMER, SUPPLIER, OTHER = "76123456-0", "77222333-1", "96555444-0", "11111111-1"
SALE_FILE = f"RCV_VENTA_{ORG_RUT}_202610.csv"
PURCHASE_FILE = f"RCV_COMPRA_REGISTRO_{ORG_RUT}_202605.csv"
SALE_SUMMARY = "RCV_RESUMEN_VENTA_76123456_202610.csv"
PURCHASE_SUMMARY = "RCV_RESUMEN_COMPRA_REGISTRO_76123456_202605.csv"


def sale_row(nro, doc_type, folio, total, *, net=None, vat=None, exempt=0, ref=("0", ""), day="14", customer=CUSTOMER, **overrides):
    if net is None:
        net, vat = (round(total / 1.19), total - round(total / 1.19)) if doc_type != 34 else (0, 0)
    values = dict.fromkeys(rcv.SALE_DETAIL_HEADER, "")
    values.update({
        "Nro": str(nro), "Tipo Doc": str(doc_type), "Tipo Venta": "Del Giro", "Rut cliente": customer,
        "Razon Social": "Cliente Ficticio SpA", "Folio": str(folio), "Fecha Docto": f"{day}/10/2026",
        "Fecha Recepcion": f"{day}/10/2026 14:34:29", "Monto Exento": str(exempt), "Monto Neto": str(net),
        "Monto IVA": str(vat), "Monto total": str(total), "RUT Emisor Liquid. Factura": "-",
        "Tipo Docto. Referencia": ref[0], "Folio Docto. Referencia": ref[1], "Indicador Venta sin Costo": "1",
        "Codigo Sucursal": "12345678",
    })
    values.update(overrides)
    return ";".join(values[column] for column in rcv.SALE_DETAIL_HEADER) + ";"  # delimitador sobrante del SII


def purchase_row(nro, doc_type, folio, total, *, exempt=0, net=0, vat=0, **overrides):
    values = dict.fromkeys(rcv.PURCHASE_DETAIL_HEADER, "")
    values.update({
        "Nro": str(nro), "Tipo Doc": str(doc_type), "Tipo Compra": "Del Giro", "RUT Proveedor": SUPPLIER,
        "Razon Social": "Proveedor Ficticio Ltda.", "Folio": str(folio), "Fecha Docto": "04/05/2026",
        "Fecha Recepcion": "05/05/2026 08:31:04", "Monto Exento": str(exempt), "Monto Neto": str(net),
        "Monto IVA Recuperable": str(vat), "Monto Total": str(total), "IVA Activo Fijo": "0",
    })
    values.update(overrides)
    return ";".join(values[column] for column in rcv.PURCHASE_DETAIL_HEADER) + ";"


def csv_text(header, rows):
    return "\n".join([";".join(header), *rows]) + "\n"


SALE_ROWS = [
    sale_row(1, 33, 15, 119000, net=100000, vat=19000),
    sale_row(2, 33, 16, 238000, net=200000, vat=38000, day="15"),
    sale_row(3, 61, 1, 119000, net=100000, vat=19000, ref=("33", "15"), day="15"),
]
SALE_SUMMARY_TEXT = csv_text(rcv.SALE_SUMMARY_HEADER, [
    "Factura Electrónica(33);2;0;300000;57000;357000",
    "Nota de Crédito Electrónica(61);1;0;100000;19000;119000",
])
PURCHASE_ROWS = [purchase_row(1, 34, 1270, 19980, exempt=19980)]
PURCHASE_SUMMARY_TEXT = csv_text(rcv.PURCHASE_SUMMARY_HEADER, ["Factura no Afecta o Exenta Electrónica(34);1;19980;0;0;0;0;19980"])


class RcvTestMixin:
    def make_dir(self, files):
        directory = tempfile.TemporaryDirectory(prefix="cuadratura-rcv-")
        self.addCleanup(directory.cleanup)
        for name, content in files.items():
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            (Path(directory.name) / name).write_bytes(data)
        return directory.name

    def standard_dir(self, **extra):
        files = {
            SALE_FILE: csv_text(rcv.SALE_DETAIL_HEADER, SALE_ROWS), SALE_SUMMARY: SALE_SUMMARY_TEXT,
            PURCHASE_FILE: csv_text(rcv.PURCHASE_DETAIL_HEADER, PURCHASE_ROWS), PURCHASE_SUMMARY: PURCHASE_SUMMARY_TEXT,
            "boletas_honorarios_privadas.csv": "no;es;rcv\n", "planilla.xlsx": b"PK\x03\x04",
        }
        files.update(extra)
        return self.make_dir(files)

    def run_command(self, *args):
        out = StringIO()
        call_command("importar_rcv", *args, stdout=out, stderr=StringIO())
        return out.getvalue()


class RcvParserTests(TestCase):
    def parse(self, name, text):
        return rcv.parse_file(rcv.classify_filename(name), text.encode("utf-8") if isinstance(text, str) else text)

    def test_filename_patterns_and_ignored_files(self):
        sale = rcv.classify_filename(SALE_FILE)
        self.assertEqual((sale.format.code, sale.rut_text, sale.period), ("sii-rcv-venta-v1", ORG_RUT, date(2026, 10, 1)))
        summary = rcv.classify_filename(PURCHASE_SUMMARY)
        self.assertEqual((summary.format.direction, summary.rut_body), (rcv.PURCHASE, 76123456))
        self.assertIsNone(rcv.classify_filename("boletas_honorarios_elementos_2025_2026.csv"))
        self.assertIsNone(rcv.classify_filename("Finanzas.xlsx"))
        with self.assertRaisesMessage(rcv.RcvError, "no soportado"):
            rcv.classify_filename(f"RCV_COMPRA_PENDIENTE_{ORG_RUT}_202605.csv")
        with self.assertRaises(rcv.RcvError):
            rcv.classify_filename(f"RCV_VENTA_{ORG_RUT}_202613.csv")

    def test_purchase_detail_exempt_invoice(self):
        parsed = self.parse(PURCHASE_FILE, csv_text(rcv.PURCHASE_DETAIL_HEADER, PURCHASE_ROWS))
        self.assertEqual(parsed.errors, [])
        doc = parsed.documents[0]
        self.assertEqual((doc.document_type, doc.folio, doc.counterparty_tax_id, doc.issue_date), (34, 1270, SUPPLIER, date(2026, 5, 4)))
        self.assertEqual((doc.exempt_amount, doc.net_amount, doc.vat_amount, doc.total_amount), (19980, 0, 0, 19980))
        self.assertEqual(doc.vat_non_recoverable_amount, 0)  # campo vacío = 0

    def test_sale_detail_with_trailing_delimiter_and_reference(self):
        parsed = self.parse(SALE_FILE, csv_text(rcv.SALE_DETAIL_HEADER, SALE_ROWS))
        self.assertEqual(parsed.errors, [])
        self.assertEqual([(d.document_type, d.folio) for d in parsed.documents], [(33, 15), (33, 16), (61, 1)])
        self.assertEqual((parsed.documents[0].reference_document_type, parsed.documents[0].reference_folio), (None, None))
        self.assertEqual((parsed.documents[2].reference_document_type, parsed.documents[2].reference_folio), (33, 15))
        self.assertEqual(parsed.documents[0].sii_received_at.hour, 14)

    def test_rows_without_trailing_delimiter_and_bom_are_accepted(self):
        rows = [row[:-1] for row in SALE_ROWS]
        parsed = self.parse(SALE_FILE, "﻿" + csv_text(rcv.SALE_DETAIL_HEADER, rows))
        self.assertEqual((parsed.errors, len(parsed.documents)), ([], 3))

    def test_extra_columns_do_not_shift(self):
        parsed = self.parse(SALE_FILE, csv_text(rcv.SALE_DETAIL_HEADER, [SALE_ROWS[0] + "x;"]))
        self.assertIn("se esperaban 43 columnas", parsed.errors[0])

    def test_invalid_dates(self):
        for value in ("2026-10-14", "31/02/2026", "14/10/26", ""):
            parsed = self.parse(SALE_FILE, csv_text(rcv.SALE_DETAIL_HEADER, [sale_row(1, 33, 15, 119000, net=100000, vat=19000, **{"Fecha Docto": value})]))
            self.assertIn("Fecha Docto", parsed.errors[0], value)
        parsed = self.parse(SALE_FILE, csv_text(rcv.SALE_DETAIL_HEADER, [sale_row(1, 33, 15, 119000, net=100000, vat=19000, **{"Fecha Recepcion": "14/10/2026"})]))
        self.assertIn("Fecha Recepcion", parsed.errors[0])

    def test_clp_amounts_are_plain_integers(self):
        for value in ("119.000", "119000,5", "-119000", "1e5", "$119000"):
            parsed = self.parse(SALE_FILE, csv_text(rcv.SALE_DETAIL_HEADER, [sale_row(1, 33, 15, 119000, net=100000, vat=19000, **{"Monto total": value})]))
            self.assertIn("monto CLP entero", parsed.errors[0], value)
        parsed = self.parse(SALE_FILE, csv_text(rcv.SALE_DETAIL_HEADER, [sale_row(1, 33, 15, 119001, net=100000, vat=19000)]))
        self.assertIn("no coincide con el total", parsed.errors[0])

    def test_exempt_invoice_cannot_carry_vat(self):
        parsed = self.parse(PURCHASE_FILE, csv_text(rcv.PURCHASE_DETAIL_HEADER, [purchase_row(1, 34, 9, 119000, net=100000, vat=19000)]))
        self.assertIn("factura exenta (34)", parsed.errors[0])

    def test_unsupported_tax_components_type_and_claim_are_rejected(self):
        cases = [
            (PURCHASE_FILE, rcv.PURCHASE_DETAIL_HEADER, purchase_row(1, 33, 9, 119000, net=100000, vat=19000, **{"Valor Otro Impuesto": "500"}), "Valor Otro Impuesto"),
            (PURCHASE_FILE, rcv.PURCHASE_DETAIL_HEADER, purchase_row(1, 46, 9, 100, exempt=100), "no soportado"),
            (SALE_FILE, rcv.SALE_DETAIL_HEADER, sale_row(1, 33, 9, 119000, net=100000, vat=19000, **{"IVA Retenido Total": "19000"}), "IVA Retenido Total"),
            (SALE_FILE, rcv.SALE_DETAIL_HEADER, sale_row(1, 33, 9, 119000, net=100000, vat=19000, **{"Fecha Reclamo": "20/10/2026 10:00:00"}), "reclamo"),
            (SALE_FILE, rcv.SALE_DETAIL_HEADER, sale_row(1, 33, 9, 119000, net=100000, vat=19000, customer="11111111-2"), "Rut cliente"),
        ]
        for name, header, row, message in cases:
            parsed = self.parse(name, csv_text(header, [row]))
            self.assertIn(message, parsed.errors[0])

    def test_duplicate_document_in_file(self):
        parsed = self.parse(SALE_FILE, csv_text(rcv.SALE_DETAIL_HEADER, [SALE_ROWS[0], SALE_ROWS[0].replace("1;", "9;", 1)]))
        self.assertIn("repite el documento", parsed.errors[0])

    def test_corrupt_files(self):
        self.assertIn("UTF-8", self.parse(SALE_FILE, "Nro;Tipo\n".encode("utf-8") + b"\xff\xfe").errors[0])
        self.assertIn("bytes nulos", self.parse(SALE_FILE, b"Nro\x00;").errors[0])
        self.assertIn("vacío", self.parse(SALE_FILE, b"   \n").errors[0])
        self.assertIn("Encabezado no reconocido", self.parse(SALE_FILE, csv_text(rcv.PURCHASE_DETAIL_HEADER, PURCHASE_ROWS)).errors[0])
        renamed = ("Numero",) + rcv.SALE_DETAIL_HEADER[1:]
        self.assertIn("columna 1", self.parse(SALE_FILE, csv_text(renamed, SALE_ROWS)).errors[0])
        self.assertIn("no contiene documentos", self.parse(SALE_FILE, csv_text(rcv.SALE_DETAIL_HEADER, [])).errors[0])

    def test_economic_summary_subtracts_credit_notes(self):
        parsed = self.parse(SALE_FILE, csv_text(rcv.SALE_DETAIL_HEADER, SALE_ROWS))
        summary = economic_summary(parsed.documents)
        self.assertEqual((summary["documents_total"], summary["credit_notes_total"]), (357000, 119000))
        self.assertEqual((summary["net_effect"]["total_amount"], summary["net_effect"]["net_amount"], summary["net_effect"]["vat_amount"]), (238000, 200000, 38000))


class RcvImportTests(RcvTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = TaxpayerOrganization.objects.create(tax_id="76.123.456-0", legal_name="Contribuyente Ficticio SpA")

    def test_dry_run_reports_plan_without_writing(self):
        output = self.run_command("--path", self.standard_dir())
        self.assertIn("SIMULACIÓN", output)
        self.assertIn("nuevos 3", output)
        self.assertIn("ref. tipo 33 folio 15 (se vincula)", output)
        self.assertIn("notas de crédito: $119.000", output)
        self.assertIn("efecto neto: total $238.000", output)
        self.assertEqual(output.count("cuadra (cantidad y montos por tipo)"), 2)
        self.assertIn("ignorados: 2", output)
        self.assertNotIn("Proveedor Ficticio", output)  # no imprime razones sociales
        self.assertEqual((TaxDocument.objects.count(), AuditEvent.objects.count()), (0, 0))

    def test_apply_imports_links_credit_note_and_is_idempotent(self):
        directory = self.standard_dir()
        self.run_command("--path", directory, "--apply")
        self.assertEqual(TaxDocument.objects.filter(organization=self.org).count(), 4)
        sale_docs = TaxDocument.objects.filter(direction="SALE")
        self.assertTrue(all(doc.issuer_tax_id == ORG_RUT for doc in sale_docs))
        invoice = TaxDocument.objects.get(direction="SALE", document_type=33, folio=15)
        credit = TaxDocument.objects.get(direction="SALE", document_type=61, folio=1)
        self.assertEqual(credit.referenced_document, invoice)
        self.assertEqual(list(invoice.adjustments.all()), [credit])
        purchase = TaxDocument.objects.get(direction="PURCHASE")
        self.assertEqual((purchase.issuer_tax_id, purchase.counterparty_tax_id, purchase.document_type, purchase.vat_amount, purchase.exempt_amount),
                         (SUPPLIER, SUPPLIER, 34, 0, 19980))
        self.assertEqual((purchase.tax_period, purchase.source_filename, purchase.source_line), (date(2026, 5, 1), PURCHASE_FILE, 2))
        self.assertEqual(AuditEvent.objects.filter(action="tax_documents.rcv_imported").count(), 2)
        output = self.run_command("--path", directory, "--apply")
        self.assertIn("0 creados, 3 ya registrados, 0 conflictos", output)
        self.assertEqual(TaxDocument.objects.count(), 4)
        self.assertEqual(AuditEvent.objects.filter(action="tax_documents.rcv_imported").count(), 2)

    def test_import_creates_no_sales_payments_or_receipts(self):
        self.run_command("--path", self.standard_dir(), "--apply")
        self.assertEqual((Sale.objects.count(), Payment.objects.count(), ElectronicReceipt.objects.count()), (0, 0, 0))

    def test_credit_note_with_missing_original_is_linked_later(self):
        only_credit = self.make_dir({SALE_FILE: csv_text(rcv.SALE_DETAIL_HEADER, [SALE_ROWS[2]])})
        plan = build_plan(only_credit)
        self.assertEqual(plan.files[0].documents[0].reference_status, REF_PENDING)
        apply_plan(plan)
        credit = TaxDocument.objects.get(document_type=61)
        self.assertIsNone(credit.referenced_document)
        self.assertEqual((credit.reference_document_type, credit.reference_folio), (33, 15))
        november = f"RCV_VENTA_{ORG_RUT}_202611.csv"
        later = self.make_dir({november: csv_text(rcv.SALE_DETAIL_HEADER, [SALE_ROWS[0]])})
        output = self.run_command("--path", later, "--apply")
        self.assertIn("Referencias pendientes de importaciones anteriores que se completan: 1", output)
        credit.refresh_from_db()
        self.assertEqual(credit.referenced_document.folio, 15)

    def test_conflict_is_reported_and_existing_is_preserved(self):
        self.run_command("--path", self.standard_dir(), "--apply")
        changed = [sale_row(1, 33, 15, 238000, net=200000, vat=38000), SALE_ROWS[1], sale_row(4, 33, 20, 119000, net=100000, vat=19000)]
        directory = self.make_dir({SALE_FILE: csv_text(rcv.SALE_DETAIL_HEADER, changed)})
        plan = build_plan(directory)
        statuses = [item.status for item in plan.files[0].documents]
        self.assertEqual(statuses, [CONFLICT, EXISTING, NEW])
        self.assertIn("total_amount", plan.files[0].documents[0].differences)
        output = self.run_command("--path", directory, "--apply")
        self.assertIn("1 creados, 1 ya registrados, 1 conflictos", output)
        self.assertEqual(TaxDocument.objects.get(document_type=33, folio=15).total_amount, 119000)

    def test_summary_differences_are_reported(self):
        bad_summary = SALE_SUMMARY_TEXT.replace("Factura Electrónica(33);2;0;300000;57000;357000", "Factura Electrónica(33);3;0;300000;57000;400000")
        output = self.run_command("--path", self.standard_dir(**{SALE_SUMMARY: bad_summary}))
        self.assertIn("2 diferencias", output)
        self.assertIn("Tipo 33: resumen 3 documentos, detalle 2.", output)
        self.assertIn("total_amount resumen 400000, detalle 357000", output)

    def test_missing_summary_does_not_block(self):
        directory = self.make_dir({SALE_FILE: csv_text(rcv.SALE_DETAIL_HEADER, SALE_ROWS)})
        output = self.run_command("--path", directory, "--apply")
        self.assertIn("Sin resumen del SII", output)
        self.assertEqual(TaxDocument.objects.count(), 3)

    def test_unknown_organization_blocks(self):
        name = f"RCV_VENTA_{OTHER}_202610.csv"
        directory = self.make_dir({name: csv_text(rcv.SALE_DETAIL_HEADER, SALE_ROWS)})
        with self.assertRaisesMessage(CommandError, "errores bloqueantes"):
            self.run_command("--path", directory, "--apply")
        self.assertEqual(TaxDocument.objects.count(), 0)
        self.assertFalse(TaxpayerOrganization.objects.filter(tax_id__contains="11").exists())

    def test_invalid_rut_in_filename_requires_explicit_rut(self):
        name = "RCV_VENTA_76123456-9_202610.csv"
        directory = self.make_dir({name: csv_text(rcv.SALE_DETAIL_HEADER, SALE_ROWS)})
        self.assertIn("indique el contribuyente con --rut", build_plan(directory).files[0].errors[0])
        plan = build_plan(directory, rut="76.123.456-0")
        self.assertEqual((plan.blocking, plan.files[0].organization), (False, self.org))
        self.assertTrue(build_plan(self.standard_dir(), rut=OTHER).blocking)  # --rut distinto del nombre

    def test_archived_or_closed_period_blocks(self):
        MonthlyClose.objects.create(organization=self.org, period=date(2026, 10, 1), version=1, status=MonthlyClose.Status.CLOSED)
        self.assertIn("está cerrado", build_plan(self.standard_dir(), direction=rcv.SALE).files[0].errors[0])
        TaxpayerOrganization.objects.filter(pk=self.org.pk).update(is_active=False)
        self.assertIn("inactivo", build_plan(self.standard_dir(), direction=rcv.PURCHASE).files[0].errors[0])

    def test_invalid_row_blocks_whole_run(self):
        bad = csv_text(rcv.SALE_DETAIL_HEADER, [*SALE_ROWS, sale_row(4, 33, 30, 100, net=1, vat=1)])
        directory = self.standard_dir(**{SALE_FILE: bad})
        with self.assertRaises(CommandError):
            self.run_command("--path", directory, "--apply")
        self.assertEqual(TaxDocument.objects.count(), 0)  # tampoco se importa la compra válida

    def test_file_and_period_selection(self):
        directory = self.standard_dir()
        output = self.run_command("--path", directory, "--apply", "--file", PURCHASE_FILE)
        self.assertNotIn(SALE_FILE, output)
        self.assertEqual(TaxDocument.objects.get().direction, "PURCHASE")
        self.run_command("--path", directory, "--apply", "--period", "202610")
        self.assertEqual(TaxDocument.objects.count(), 4)
        with self.assertRaises(CommandError):
            self.run_command("--path", directory, "--file", "../secreto.csv")

    def test_failure_rolls_back_everything(self):
        plan = build_plan(self.standard_dir())
        with mock.patch("documents.rcv_import.record_event", side_effect=[None, DatabaseError("falla simulada")]):
            with self.assertRaises(DatabaseError):
                apply_plan(plan)
        self.assertEqual(TaxDocument.objects.count(), 0)

    def test_database_rejects_update_and_delete(self):
        self.run_command("--path", self.standard_dir(), "--apply")
        invoice = TaxDocument.objects.get(document_type=33, folio=15)
        with self.assertRaisesMessage(DatabaseError, "inmutable"), transaction.atomic():
            TaxDocument.objects.filter(pk=invoice.pk).update(total_amount=1)
        unreferenced = TaxDocument.objects.get(document_type=33, folio=16)
        with self.assertRaisesMessage(DatabaseError, "no pueden eliminarse"), transaction.atomic():
            TaxDocument.objects.filter(pk=unreferenced.pk).delete()
        credit = TaxDocument.objects.get(document_type=61)
        with self.assertRaisesMessage(DatabaseError, "inmutable"), transaction.atomic():
            TaxDocument.objects.filter(pk=credit.pk).update(referenced_document=TaxDocument.objects.get(folio=16))


class RcvConcurrencyTests(RcvTestMixin, TransactionTestCase):
    def test_concurrent_runs_do_not_duplicate(self):
        TaxpayerOrganization.objects.create(tax_id="76.123.456-0", legal_name="Contribuyente Ficticio SpA")
        directory = self.standard_dir()
        plans = [build_plan(directory), build_plan(directory)]
        barrier, results, errors = threading.Barrier(2), [], []

        def run(plan):
            close_old_connections()
            try:
                barrier.wait()
                results.append(sum(result.created for result in apply_plan(plan)))
            except Exception as exc:  # pragma: no cover - se informa en la aserción
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=run, args=(plan,)) for plan in plans]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [0, 4])
        self.assertEqual(TaxDocument.objects.count(), 4)
        self.assertEqual(TaxDocument.objects.get(document_type=61).referenced_document.folio, 15)
