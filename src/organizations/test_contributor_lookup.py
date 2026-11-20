import io
import json
import socket
import urllib.error
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent
from .contributor_info import (
    ContributorInfo,
    DisabledContributorInformationProvider,
    FakeContributorInformationProvider,
    LookupResult,
    LookupStatus,
    EconomicActivity,
    RegisteredAddress,
    MAX_ADDRESSES,
    TupanaContributorInformationProvider,
    get_contributor_information_provider,
)
from .models import DataSource, OrganizationMembership, TaxpayerActivity, TaxpayerAddress, TaxpayerOrganization
from .rut import InvalidRut, Rut, parse_rut

TOKEN = "token-de-prueba-no-real"
CODELCO = Rut(61704000, "K")  # RUT de empresa pública, válido por módulo 11


class RutTests(SimpleTestCase):
    def test_valid_formats(self):
        for value in ("61.704.000-K", "61704000-k", "61704000K", " 61 704 000-K "):
            self.assertEqual(parse_rut(value), CODELCO, value)
        self.assertEqual(CODELCO.formatted, "61.704.000-K")
        self.assertEqual(CODELCO.compact, "61704000-K")
        self.assertEqual(parse_rut("11.111.111-1").formatted, "11.111.111-1")

    def test_invalid_values(self):
        # 77.111.222-3 es el RUT ficticio de pruebas: su dígito verificador no cumple módulo 11.
        for value in ("", "77.111.222-3", "61704000-1", "abc", "0-0", "123456789-0", "61704000-X", "6170@4000-K"):
            with self.assertRaises(InvalidRut, msg=value):
                parse_rut(value)


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, status=200, payload=None, raw=None, error=None):
        self.status, self.error = status, error
        self.raw = raw if raw is not None else json.dumps(payload or {}).encode()
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        if self.status != 200:
            raise urllib.error.HTTPError(request.full_url, self.status, "error", {}, io.BytesIO(self.raw))
        return FakeResponse(200, self.raw)


# Respuesta con la forma de `MasterEntity` de la OpenAPI v2 de Tupana; valores ficticios.
# Forma real observada el 2026-11-04 (ids opacos en cadena, sin `opaque_id`, banderas por
# dirección, nombres de actividad truncados a 40 caracteres). Todos los valores son ficticios.
TUPANA_OK = {
    "id": "ment_0000000000000000001",
    "tax_id": "61.704.000-K",
    "name": "  CORPORACION   FICTICIA DE PRUEBA ",
    "email": "Contacto@Ejemplo.cl",
    "addresses": [
        {"id": "addr_0000000000000000001", "address": "AV  EJEMPLO 100 OF 1", "district": {"id": "dist_1", "name": "LAS CONDES", "city": {"id": "city_1", "name": "SANTIAGO"}}, "phone": None, "sii_branch_code": None, "is_default": False, "is_main_in_sii": True},
        {"id": "addr_0000000000000000002", "address": "CALLE FICTICIA 200", "district": {"id": "dist_2", "name": "TEMUCO", "city": {"id": "city_2", "name": "TEMUCO"}}, "phone": "+56 2 0000 0000", "sii_branch_code": "123", "is_default": True, "is_main_in_sii": False},
    ],
    "activities": [
        {"id": "acti_0000000000000000001", "code": "620900", "name": "OTRAS ACTIVIDADES DE TECNOLOGIA DE LA IN"},
        {"id": "acti_0000000000000000002", "code": "040000", "name": "EXTRACCION Y PROCESAMIENTO DE COBRE"},
    ],
}
# RUT inexistente: 200 con nombre vacío e id nulo (observado el 2026-11-04).
TUPANA_EMPTY = {"id": None, "opaque_id": None, "name": "", "email": "", "addresses": [], "activities": [], "tax_id": "61.704.000-K"}
# Key inválida: 403 con error anidado (observado; difiere de la OpenAPI).
TUPANA_FORBIDDEN = {"error": {"type": "permission_error", "code": "forbidden", "message": "API key inválida o inexistente", "doc_url": "https://docs.tupana.ai/v2/errors#forbidden", "request_id": "req_0000000000000000"}}


class TupanaProviderTests(SimpleTestCase):
    def lookup(self, rut=CODELCO, **opener_kwargs):
        opener = FakeOpener(**opener_kwargs)
        provider = TupanaContributorInformationProvider(api_key=TOKEN, timeout_seconds=4, opener=opener)
        return provider.lookup(rut), opener

    def test_found_is_normalized(self):
        result, opener = self.lookup(payload=TUPANA_OK)
        self.assertEqual(result.status, LookupStatus.FOUND)
        info = result.info
        self.assertEqual(info.legal_name, "CORPORACION FICTICIA DE PRUEBA")
        self.assertEqual(info.trade_name, "")  # Tupana no informa nombre de fantasía
        self.assertEqual(info.public_email, "contacto@ejemplo.cl")
        self.assertEqual(info.activities, (EconomicActivity("620900", "OTRAS ACTIVIDADES DE TECNOLOGIA DE LA IN"), EconomicActivity("040000", "EXTRACCION Y PROCESAMIENTO DE COBRE")))
        self.assertEqual(info.addresses, (
            RegisteredAddress("AV EJEMPLO 100 OF 1", "LAS CONDES", "SANTIAGO", is_main_in_sii=True, reference="addr_0000000000000000001"),
            RegisteredAddress("CALLE FICTICIA 200", "TEMUCO", "TEMUCO", "123", "+56 2 0000 0000", reference="addr_0000000000000000002"),
        ))  # `is_default` (preferencia de la plataforma Tupana) se descarta
        self.assertEqual((info.source, info.provider_reference, info.addresses_truncated), ("Tupana", "ment_0000000000000000001", False))
        self.assertEqual(info.model_values(), {"legal_name": "CORPORACION FICTICIA DE PRUEBA", "public_email": "contacto@ejemplo.cl"})
        request, timeout = opener.requests[0]
        self.assertEqual((request.get_method(), timeout), ("GET", 4))
        self.assertEqual(request.full_url, "https://api.tupana.ai/v2/master-entities/?rut=61704000-K")
        self.assertEqual(request.get_header("Authorization"), f"Api-Key {TOKEN}")
        self.assertNotIn(TOKEN, request.full_url)  # la key viaja en la cabecera, no en la URL
        self.assertIsNone(request.data)

    def test_incomplete_or_odd_values_are_dropped(self):
        payload = {"tax_id": "61704000-K", "name": "Empresa Parcial SpA", "email": "no-es-correo",
                   "activities": [{"code": "12a"}, {"name": "sin código"}, "620200", {"code": "620200"}, {"code": "620200", "name": "dup"}],
                   "addresses": [{"address": ""}, {"address": "SOLO CALLE 1", "district": "texto"}, None]}
        result, _ = self.lookup(payload=payload)
        info = result.info
        self.assertEqual((result.status, info.legal_name, info.public_email), (LookupStatus.FOUND, "Empresa Parcial SpA", ""))
        self.assertEqual(info.activities, (EconomicActivity("620200", ""),))
        self.assertEqual(info.addresses, (RegisteredAddress("SOLO CALLE 1"),))

    def test_not_found_variants(self):
        for kwargs in ({"status": 404, "payload": {"error": "NOT_FOUND", "message": "Entidad no encontrada"}}, {"raw": b""}, {"raw": b"{}"}, {"raw": b"[]"}, {"payload": {"name": ""}}, {"payload": TUPANA_EMPTY}):
            result, _ = self.lookup(**kwargs)
            self.assertEqual(result.status, LookupStatus.NOT_FOUND, kwargs)
            self.assertIsNone(result.info)

    def test_rut_outside_provider_pattern_is_not_sent(self):
        result, opener = self.lookup(rut=parse_rut("1-9"))
        self.assertEqual(result.status, LookupStatus.NOT_FOUND)
        self.assertEqual(opener.requests, [])

    def test_auth_and_rate_limit(self):
        for status in (401, 403):
            result, _ = self.lookup(status=status, payload={"error": "AUTHENTICATION_ERROR", "message": "API Key inválida o faltante"})
            self.assertEqual(result.status, LookupStatus.UNAVAILABLE)
        real, _ = self.lookup(status=403, payload=TUPANA_FORBIDDEN)
        self.assertEqual((real.status, real.detail), (LookupStatus.UNAVAILABLE, "credentials_rejected:req_0000000000000000"))
        limited, _ = self.lookup(status=429, raw=b"")
        self.assertEqual(limited.status, LookupStatus.PROVIDER_LIMITED)

    def test_http_network_and_payload_errors_are_unavailable(self):
        cases = [
            {"status": 500, "payload": {"error": "INTERNAL_ERROR", "message": "x"}},
            {"status": 502, "raw": b"<html>bad gateway</html>"},
            {"status": 400, "payload": {"error": "VALIDATION_ERROR", "message": "rut"}},
            {"status": 302, "raw": b""},  # redirecciones deshabilitadas
            {"raw": b"no es json"},
            {"raw": b'"texto"'},
            {"payload": [TUPANA_OK, TUPANA_OK]},  # esquema inesperado
            {"raw": b"x" * (TupanaContributorInformationProvider.MAX_RESPONSE_BYTES + 10)},
            {"payload": {**TUPANA_OK, "tax_id": "11.111.111-1"}},  # responde por otro RUT
            {"error": TimeoutError("timed out")},
            {"error": socket.timeout("timed out")},
            {"error": urllib.error.URLError("dns")},
        ]
        for kwargs in cases:
            result, _ = self.lookup(**kwargs)
            self.assertEqual(result.status, LookupStatus.UNAVAILABLE, kwargs.keys())

    def test_logs_do_not_include_key_rut_or_body(self):
        with self.assertLogs("cuadratura.contributor_info", level="INFO") as logs:
            self.lookup(payload=TUPANA_OK)
            self.lookup(status=401, payload={"error": "AUTHENTICATION_ERROR", "message": "API Key inválida"})
        text = "\n".join(logs.output)
        for secret in (TOKEN, "61704000", "61.704.000", "CORPORACION", "contacto@", "EJEMPLO"):
            self.assertNotIn(secret, text)
        self.assertNotIn(TOKEN, repr(TupanaContributorInformationProvider(api_key=TOKEN, timeout_seconds=1)))


class TupanaLargeResponseTests(SimpleTestCase):
    def test_many_addresses_are_capped_and_flagged(self):
        many = [{"id": f"addr_{i}", "address": f"CALLE {i}", "district": {"name": "SANTIAGO", "city": {"name": "SANTIAGO"}}, "is_main_in_sii": i == 0} for i in range(MAX_ADDRESSES + 5)]
        opener = FakeOpener(payload={**TUPANA_OK, "addresses": many})
        info = TupanaContributorInformationProvider(api_key=TOKEN, timeout_seconds=4, opener=opener).lookup(CODELCO).info
        self.assertEqual((len(info.addresses), info.addresses_truncated), (MAX_ADDRESSES, True))


class ProviderFactoryTests(SimpleTestCase):
    def test_disabled_by_default_and_without_key(self):
        with self.settings(CONTRIBUTOR_INFO_PROVIDER="disabled", TUPANA_API_KEY=TOKEN):
            self.assertIsInstance(get_contributor_information_provider(), DisabledContributorInformationProvider)
        with self.settings(CONTRIBUTOR_INFO_PROVIDER="tupana", TUPANA_API_KEY=""):
            self.assertIsInstance(get_contributor_information_provider(), DisabledContributorInformationProvider)
        with self.settings(CONTRIBUTOR_INFO_PROVIDER="tupana", TUPANA_API_KEY=TOKEN, CONTRIBUTOR_INFO_TIMEOUT_SECONDS=3):
            self.assertIsInstance(get_contributor_information_provider(), TupanaContributorInformationProvider)

    def test_sre_is_no_longer_a_provider(self):
        with self.settings(CONTRIBUTOR_INFO_PROVIDER="sre", TUPANA_API_KEY=TOKEN):
            self.assertIsInstance(get_contributor_information_provider(), DisabledContributorInformationProvider)
        import organizations.contributor_info as module
        self.assertFalse(hasattr(module, "SreContributorInformationProvider"))


def found(legal_name="Corporación Ficticia SpA", trade_name=""):
    return LookupResult(LookupStatus.FOUND, ContributorInfo(
        tax_id=CODELCO, source="Tupana", fetched_at=timezone.now(), legal_name=legal_name, trade_name=trade_name,
        public_email="contacto@ejemplo.cl",
        activities=(EconomicActivity("620200", "CONSULTORES INFORMATICOS"), EconomicActivity("702000", "CONSULTORIA")),
        addresses=(RegisteredAddress("CALLE FICTICIA 1", "PROVIDENCIA", "SANTIAGO", is_main_in_sii=True, reference="addr_1"), RegisteredAddress("CALLE FICTICIA 2", "TEMUCO", "TEMUCO", "9", reference="addr_2")),
    ))


@override_settings(CONTRIBUTOR_INFO_PROVIDER="tupana", TUPANA_API_KEY=TOKEN, CONTRIBUTOR_INFO_RATE_LIMIT_PER_MINUTE=10)
class LookupEndpointTests(TestCase):
    url = reverse("organization-tax-id-lookup")

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(username="admin-plataforma", password="x", is_superuser=True)
        cls.member = User.objects.create_user(username="admin-org", password="x")
        cls.org = TaxpayerOrganization.objects.create(tax_id="77.111.222-3", legal_name="Taller Ficticio SpA")
        OrganizationMembership.objects.create(organization=cls.org, user=cls.member, role=OrganizationMembership.Role.ORGANIZATION_ADMIN)

    def setUp(self):
        cache.clear()
        self.provider = FakeContributorInformationProvider({"61704000-K": found()})
        patcher = mock.patch("organizations.views.get_contributor_information_provider", return_value=self.provider)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, tax_id="61.704.000-K", user=None):
        self.client.force_login(user or self.admin)
        return self.client.post(self.url, {"tax_id": tax_id})

    def test_found_returns_preview_without_writing(self):
        counts = (TaxpayerOrganization.objects.count(), AuditEvent.objects.count())
        response = self.post("61704000k")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["status"], data["tax_id"], data["source"]), ("found", "61.704.000-K", "Tupana"))
        self.assertEqual(data["fields"], {"legal_name": "Corporación Ficticia SpA", "public_email": "contacto@ejemplo.cl"})  # vacíos (nombre de fantasía) no viajan
        self.assertEqual([a["code"] for a in data["activities"]], ["620200", "702000"])
        self.assertEqual([(a["district"], a["is_main_in_sii"], a["key"]) for a in data["addresses"]], [("PROVIDENCIA", True, "addr_1"), ("TEMUCO", False, "addr_2")])
        self.assertIn("fetched_at", data)
        self.assertNotIn(TOKEN, response.content.decode())
        self.assertEqual((TaxpayerOrganization.objects.count(), AuditEvent.objects.count()), counts)

    def test_invalid_rut_is_rejected_before_provider(self):
        for value in ("77.111.222-3", "abc", ""):
            response = self.post(value)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["status"], "invalid_tax_id")
        self.assertEqual(self.provider.calls, [])

    def test_provider_states(self):
        for status, http in ((LookupStatus.NOT_FOUND, 200), (LookupStatus.UNAVAILABLE, 503), (LookupStatus.PROVIDER_LIMITED, 503), (LookupStatus.DISABLED, 503)):
            self.provider.results = {"61704000-K": LookupResult(status)}
            response = self.post()
            self.assertEqual((response.status_code, response.json()["status"]), (http, status.value))
            self.assertNotIn("fields", response.json())

    def test_already_registered_does_not_call_provider(self):
        existing = TaxpayerOrganization.objects.create(tax_id="61704000-K", legal_name="Ya existe")
        response = self.post("61.704.000-K")
        self.assertEqual(response.json(), {"status": "already_registered", "tax_id": "61.704.000-K", "organization_url": reverse("organization-edit", args=[existing.id])})
        TaxpayerOrganization.objects.filter(pk=existing.pk).update(archived_at=timezone.now(), is_active=False)
        self.assertNotIn("organization_url", self.post("61704000-K").json())  # archivado: sin enlace
        self.assertEqual(self.provider.calls, [])

    @override_settings(CONTRIBUTOR_INFO_RATE_LIMIT_PER_MINUTE=2)
    def test_rate_limit_per_user(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().status_code, 200)
        response = self.post()
        self.assertEqual((response.status_code, response.json()["status"]), (429, "rate_limited"))
        self.assertEqual(len(self.provider.calls), 2)

    def test_only_platform_admin_and_post(self):
        self.assertEqual(self.post(user=self.member).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {"tax_id": "61704000-K"}).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url, {"tax_id": "61704000-K"}).status_code, 405)
        self.assertEqual(self.provider.calls, [])

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url, {"tax_id": "61704000-K"}).status_code, 403)
        self.assertEqual(self.provider.calls, [])


class CreateFormLookupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(username="admin-plataforma", password="x", is_superuser=True)
        cls.org = TaxpayerOrganization.objects.create(tax_id="77.111.222-3", legal_name="Taller Ficticio SpA")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_button_only_when_provider_configured_and_only_on_create(self):
        create = reverse("organization-create")
        with self.settings(CONTRIBUTOR_INFO_PROVIDER="disabled"):
            response = self.client.get(create)
            self.assertNotContains(response, "Consultar datos")
            self.assertContains(response, "Guardar")  # el trabajo manual sigue disponible
        with self.settings(CONTRIBUTOR_INFO_PROVIDER="tupana", TUPANA_API_KEY=TOKEN):
            response = self.client.get(create)
            self.assertContains(response, "Consultar datos")
            self.assertContains(response, "contributor-lookup.js")
            self.assertNotContains(response, TOKEN)
            edit = self.client.get(reverse("organization-edit", args=[self.org.id]))
            self.assertContains(edit, "Actualizar desde Tupana")
            self.assertContains(edit, reverse("organization-update-lookup", args=[self.org.id]))
            self.assertNotContains(edit, reverse("organization-tax-id-lookup"))

    def test_create_with_confirmed_lookup_data_uses_normal_flow(self):
        response = self.client.post(reverse("organization-create"), {
            "tax_id": "61.704.000-K", "legal_name": "Corporación Ficticia SpA", "trade_name": "Ficticia",
            "is_active": "on", "lookup_source": "Tupana",
        })
        self.assertRedirects(response, reverse("organization-list"), fetch_redirect_response=False)
        org = TaxpayerOrganization.objects.get(tax_id="61.704.000-K")
        self.assertEqual((org.legal_name, org.trade_name), ("Corporación Ficticia SpA", "Ficticia"))
        event = AuditEvent.objects.get(organization=org, action="organization.created")
        self.assertEqual(event.metadata, {"prefilled_from": "Tupana", "activities_added": [], "addresses_added": 0})

    def test_manual_creation_and_unknown_source_are_not_attributed(self):
        self.client.post(reverse("organization-create"), {"tax_id": "11.111.111-1", "legal_name": "Manual SpA", "is_active": "on", "lookup_source": "otro"})
        org = TaxpayerOrganization.objects.get(tax_id="11.111.111-1")
        self.assertEqual(AuditEvent.objects.get(organization=org, action="organization.created").metadata, {})


@override_settings(CONTRIBUTOR_INFO_PROVIDER="tupana", TUPANA_API_KEY=TOKEN, CONTRIBUTOR_INFO_RATE_LIMIT_PER_MINUTE=10)
class UpdateFromRutTests(TestCase):
    """Parte 2: «Actualizar desde Tupana» en la edición."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(username="admin-plataforma", password="x", is_superuser=True)
        cls.member = User.objects.create_user(username="admin-org", password="x")
        cls.org = TaxpayerOrganization.objects.create(
            tax_id="61.704.000-K", legal_name="Empresa ABC Ltda.", trade_name="ABC", dte_41_enabled=True
        )
        cls.other = TaxpayerOrganization.objects.create(tax_id="77.111.222-3", legal_name="Taller Ficticio SpA")
        OrganizationMembership.objects.create(organization=cls.org, user=cls.member, role=OrganizationMembership.Role.ORGANIZATION_ADMIN)

    def setUp(self):
        cache.clear()
        self.provider = FakeContributorInformationProvider({"61704000-K": found("Empresa ABC SpA")})
        patcher = mock.patch("organizations.views.get_contributor_information_provider", return_value=self.provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.admin)
        self.lookup_url = reverse("organization-update-lookup", args=[self.org.id])
        self.apply_url = reverse("organization-update-from-lookup", args=[self.org.id])

    def lookup(self, **data):
        return self.client.post(self.lookup_url, data)

    def apply(self, **overrides):
        self.org.refresh_from_db()
        data = {"version": self.org.updated_at.isoformat(), "source": "Tupana", "fetched_at": "2026-11-03T22:10:00-03:00", "confirm": "yes"}
        data.update(overrides)
        return self.client.post(self.apply_url, data)

    def snapshot(self):
        self.org.refresh_from_db()
        return (self.org.tax_id, self.org.legal_name, self.org.trade_name, self.org.is_active, self.org.dte_39_enabled, self.org.dte_41_enabled, self.org.updated_at)

    def message_texts(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_lookup_compares_using_stored_rut_without_writing(self):
        before, events = self.snapshot(), AuditEvent.objects.count()
        response = self.lookup(tax_id="11.111.111-1")  # se ignora: manda el RUT registrado
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(self.provider.calls, [CODELCO])
        rows = {row["field"]: row for row in data["comparison"]}
        self.assertEqual(set(rows), {"legal_name", "trade_name", "public_email"})  # el RUT nunca se propone
        self.assertEqual(rows["legal_name"], {"field": "legal_name", "label": "Razón social", "current": "Empresa ABC Ltda.", "consulted": "Empresa ABC SpA", "changed": True, "suggested_action": "update"})
        # El proveedor no informa nombre de fantasía: no se ofrece como cambio ni borra el actual.
        self.assertEqual((rows["trade_name"]["changed"], rows["trade_name"]["suggested_action"]), (False, "keep"))
        self.assertEqual(data["version"], self.org.updated_at.isoformat())
        self.assertEqual((self.snapshot(), AuditEvent.objects.count()), (before, events))

    def test_lookup_without_differences(self):
        TaxpayerOrganization.objects.filter(pk=self.org.pk).update(public_email="contacto@ejemplo.cl")
        self.provider.results = {"61704000-K": found("Empresa ABC Ltda.")}
        rows = self.lookup().json()["comparison"]
        self.assertFalse(any(row["changed"] for row in rows))

    def test_lookup_rejects_invalid_stored_rut(self):
        response = self.client.post(reverse("organization-update-lookup", args=[self.other.id]))
        self.assertEqual((response.status_code, response.json()["status"]), (400, "invalid_tax_id"))
        self.assertEqual(self.provider.calls, [])

    def test_lookup_and_apply_permissions(self):
        before = self.snapshot()
        self.client.force_login(self.member)  # admin de la organización, no de plataforma
        self.assertEqual(self.lookup().status_code, 403)
        self.assertEqual(self.client.post(self.apply_url, {"confirm": "yes", "source": "Tupana", "action_legal_name": "update", "value_legal_name": "X"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.lookup().status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.lookup_url).status_code, 405)
        self.assertEqual(self.client.get(self.apply_url).status_code, 405)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_archived_organization_is_not_found(self):
        TaxpayerOrganization.objects.filter(pk=self.org.pk).update(archived_at=timezone.now(), is_active=False)
        self.assertEqual(self.lookup().status_code, 404)
        self.assertEqual(self.client.post(self.apply_url, {"confirm": "yes"}).status_code, 404)

    def test_partial_update_applies_only_selected_fields_and_audits(self):
        response = self.apply(action_legal_name="update", value_legal_name="Empresa ABC SpA", action_trade_name="keep", value_trade_name="Otra marca")
        self.assertRedirects(response, reverse("organization-edit", args=[self.org.id]), fetch_redirect_response=False)
        tax_id, legal_name, trade_name, active, dte39, dte41, _ = self.snapshot()
        self.assertEqual((tax_id, legal_name, trade_name, active, dte39, dte41), ("61.704.000-K", "Empresa ABC SpA", "ABC", True, False, True))
        event = AuditEvent.objects.get(organization=self.org, action="organization.updated")
        self.assertEqual(event.metadata, {"changed_fields": ["legal_name"], "source": "Tupana", "fetched_at": "2026-11-03T22:10-03:00",
            "activities_added": [], "activities_updated": [], "addresses_added": 0, "addresses_updated": 0, "activities_not_reported": [], "addresses_not_reported": 0})
        self.assertIn("Cambios confirmados: Razón social. Los demás datos se conservaron; nada se eliminó.", self.message_texts(response))

    def test_selected_and_discarded_fields(self):
        self.apply(action_legal_name="keep", value_legal_name="Empresa ABC SpA", action_trade_name="update", value_trade_name="Nueva marca")
        self.assertEqual(self.snapshot()[1:3], ("Empresa ABC Ltda.", "Nueva marca"))

    def test_empty_provider_values_never_erase(self):
        before = self.snapshot()
        response = self.apply(action_legal_name="update", value_legal_name="   ", action_trade_name="update", value_trade_name="")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(AuditEvent.objects.filter(action="organization.updated").exists())
        self.assertIn("No seleccionaste cambios; los datos se mantienen.", self.message_texts(response))

    def test_requires_explicit_confirmation_and_known_source(self):
        before = self.snapshot()
        for overrides in ({"confirm": ""}, {"confirm": "no"}, {"source": "otro"}):
            self.apply(action_legal_name="update", value_legal_name="Empresa ABC SpA", **overrides)
            self.assertEqual(self.snapshot(), before, overrides)
        self.assertFalse(AuditEvent.objects.filter(action="organization.updated").exists())

    def test_keep_everything_is_a_cancel_without_changes(self):
        before = self.snapshot()
        self.apply(action_legal_name="keep", value_legal_name="Empresa ABC SpA")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(AuditEvent.objects.filter(action="organization.updated").exists())

    def test_tax_id_cannot_be_replaced(self):
        self.apply(action_tax_id="update", value_tax_id="11.111.111-1", tax_id="11.111.111-1", action_legal_name="update", value_legal_name="Empresa ABC SpA")
        self.assertEqual(self.snapshot()[:2], ("61.704.000-K", "Empresa ABC SpA"))

    def test_invalid_value_is_rejected(self):
        before = self.snapshot()
        response = self.apply(action_legal_name="update", value_legal_name="x" * 201)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(any(text.startswith("No se aplicaron cambios. Razón social") for text in self.message_texts(response)))

    def test_concurrent_edit_rejects_assisted_update(self):
        stale = self.org.updated_at.isoformat()
        TaxpayerOrganization.objects.filter(pk=self.org.pk).update(trade_name="Cambiado por otra persona", updated_at=timezone.now())
        response = self.client.post(self.apply_url, {"version": stale, "source": "Tupana", "confirm": "yes", "action_legal_name": "update", "value_legal_name": "Empresa ABC SpA"})
        self.assertEqual(self.snapshot()[1:3], ("Empresa ABC Ltda.", "Cambiado por otra persona"))
        self.assertTrue(any("modificado por otra persona" in text for text in self.message_texts(response)))
        self.assertFalse(AuditEvent.objects.filter(action="organization.updated").exists())


class EditConcurrencyTests(TestCase):
    """Guardado normal de edición con concurrencia optimista (compatible sin versión)."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(username="admin-plataforma", password="x", is_superuser=True)
        cls.org = TaxpayerOrganization.objects.create(tax_id="61.704.000-K", legal_name="Empresa ABC Ltda.", trade_name="ABC")

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("organization-edit", args=[self.org.id])

    def form_data(self, **overrides):
        data = {"tax_id": "61.704.000-K", "legal_name": "Empresa ABC Ltda.", "trade_name": "ABC", "is_active": "on"}
        data.update(overrides)
        return data

    def test_form_renders_version(self):
        self.assertContains(self.client.get(self.url), f'name="version" value="{self.org.updated_at.isoformat()}"')

    def test_current_version_saves_and_audits_changed_fields(self):
        self.org.refresh_from_db()
        response = self.client.post(self.url, self.form_data(trade_name="Nueva", version=self.org.updated_at.isoformat()))
        self.assertRedirects(response, reverse("organization-list"), fetch_redirect_response=False)
        self.org.refresh_from_db()
        self.assertEqual(self.org.trade_name, "Nueva")
        event = AuditEvent.objects.get(organization=self.org, action="organization.updated")
        self.assertEqual(event.metadata["changed_fields"], ["trade_name"])
        self.assertEqual((event.metadata["active"], event.metadata["dte_39"]), (True, False))

    def test_stale_version_is_rejected(self):
        stale = self.org.updated_at.isoformat()
        TaxpayerOrganization.objects.filter(pk=self.org.pk).update(legal_name="Cambio concurrente", updated_at=timezone.now())
        response = self.client.post(self.url, self.form_data(trade_name="Mía", version=stale))
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Otra persona modificó este contribuyente", status_code=409)
        self.assertContains(response, f'name="version" value="{stale}"', status_code=409)  # reenviar sigue rechazado
        self.org.refresh_from_db()
        self.assertEqual((self.org.legal_name, self.org.trade_name), ("Cambio concurrente", "ABC"))
        self.assertFalse(AuditEvent.objects.filter(action="organization.updated").exists())

    def test_post_without_version_keeps_previous_behavior(self):
        response = self.client.post(self.url, self.form_data(trade_name="Sin versión"))
        self.assertEqual(response.status_code, 302)
        self.org.refresh_from_db()
        self.assertEqual(self.org.trade_name, "Sin versión")


ACTIVITIES_JSON = json.dumps([{"code": "620200", "name": "CONSULTORES INFORMATICOS"}, {"code": "702000", "name": "CONSULTORIA"}])
ADDRESSES_JSON = json.dumps([
    {"key": "addr_1", "reference": "addr_1", "street": "CALLE FICTICIA 1", "district": "PROVIDENCIA", "city": "SANTIAGO", "branch_code": "", "phone": "", "is_main_in_sii": True},
])


class CreateWithCollectionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(username="admin-plataforma", password="x", is_superuser=True)

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("organization-create")

    def post(self, **extra):
        data = {"tax_id": "61.704.000-K", "legal_name": "Corporación Ficticia SpA", "public_email": "contacto@ejemplo.cl", "is_active": "on"}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_accepted_collections_are_saved_with_provenance(self):
        response = self.post(lookup_source="Tupana", lookup_reference="ment_1", lookup_activities=ACTIVITIES_JSON, lookup_addresses=ADDRESSES_JSON)
        self.assertEqual(response.status_code, 302)
        org = TaxpayerOrganization.objects.get(tax_id="61.704.000-K")
        self.assertEqual((org.public_email, org.external_source, org.external_reference), ("contacto@ejemplo.cl", "Tupana", "ment_1"))
        self.assertIsNotNone(org.external_synced_at)
        self.assertEqual(list(org.activities.values_list("code", "source")), [("620200", DataSource.TUPANA), ("702000", DataSource.TUPANA)])
        address = org.addresses.get()
        self.assertEqual((address.street, address.district, address.is_main_in_sii, address.external_reference, address.source), ("CALLE FICTICIA 1", "PROVIDENCIA", True, "addr_1", DataSource.TUPANA))
        event = AuditEvent.objects.get(organization=org, action="organization.created")
        self.assertEqual(event.metadata, {"prefilled_from": "Tupana", "activities_added": ["620200", "702000"], "addresses_added": 1})

    def test_collections_without_declared_source_are_ignored(self):
        self.post(lookup_activities=ACTIVITIES_JSON, lookup_addresses=ADDRESSES_JSON)
        org = TaxpayerOrganization.objects.get(tax_id="61.704.000-K")
        self.assertEqual((org.activities.count(), org.addresses.count(), org.external_source), (0, 0, ""))

    def test_invalid_collections_block_creation_without_partial_writes(self):
        for bad in ('[{"code": "12a"}]', "no-json", json.dumps([{"code": "620200"}] * 60)):
            response = self.post(lookup_source="Tupana", lookup_activities=bad)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "No se pudieron incorporar los antecedentes consultados")
        self.assertFalse(TaxpayerOrganization.objects.filter(tax_id="61.704.000-K").exists())
        self.assertFalse(TaxpayerActivity.objects.exists())


@override_settings(CONTRIBUTOR_INFO_PROVIDER="tupana", TUPANA_API_KEY=TOKEN, CONTRIBUTOR_INFO_RATE_LIMIT_PER_MINUTE=10)
class UpdateCollectionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(username="admin-plataforma", password="x", is_superuser=True)
        cls.org = TaxpayerOrganization.objects.create(tax_id="61.704.000-K", legal_name="Corporación Ficticia SpA", public_email="contacto@ejemplo.cl")
        cls.other = TaxpayerOrganization.objects.create(tax_id="11.111.111-1", legal_name="Otra SpA")
        # Estado registrado: una actividad igual, una con nombre distinto, una de Tupana que ya no viene y una manual.
        TaxpayerActivity.objects.create(organization=cls.org, code="620200", name="CONSULTORES INFORMATICOS", source=DataSource.TUPANA)
        TaxpayerActivity.objects.create(organization=cls.org, code="702000", name="NOMBRE ANTIGUO", source=DataSource.TUPANA)
        TaxpayerActivity.objects.create(organization=cls.org, code="111111", name="YA NO INFORMADA", source=DataSource.TUPANA)
        TaxpayerActivity.objects.create(organization=cls.org, code="222222", name="AGREGADA A MANO", source=DataSource.MANUAL)
        TaxpayerAddress.objects.create(organization=cls.org, street="CALLE FICTICIA 1", district="PROVIDENCIA", city="SANTIAGO", phone="+56 2 1111 1111", source=DataSource.MANUAL)
        TaxpayerAddress.objects.create(organization=cls.org, street="CALLE CERRADA 9", district="NUNOA", external_reference="addr_9", source=DataSource.TUPANA)
        TaxpayerActivity.objects.create(organization=cls.other, code="620200", name="DE OTRA ORGANIZACION")

    def setUp(self):
        cache.clear()
        info = ContributorInfo(
            tax_id=CODELCO, source="Tupana", fetched_at=timezone.now(), legal_name="Corporación Ficticia SpA", public_email="contacto@ejemplo.cl",
            activities=(EconomicActivity("620200", "CONSULTORES INFORMATICOS"), EconomicActivity("702000", "CONSULTORIA"), EconomicActivity("731001", "PUBLICIDAD")),
            addresses=(RegisteredAddress("CALLE FICTICIA 1", "PROVIDENCIA", "SANTIAGO", is_main_in_sii=True, reference="addr_1"),
                       RegisteredAddress("CALLE NUEVA 2", "TEMUCO", "TEMUCO", reference="addr_2"),
                       RegisteredAddress("CALLE NUEVA 3", "OSORNO", "OSORNO", reference="addr_3")),
        )
        self.provider = FakeContributorInformationProvider({"61704000-K": LookupResult(LookupStatus.FOUND, info)})
        patcher = mock.patch("organizations.views.get_contributor_information_provider", return_value=self.provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.admin)

    def lookup(self):
        return self.client.post(reverse("organization-update-lookup", args=[self.org.id])).json()

    def test_lookup_classifies_new_existing_changed_and_not_reported(self):
        before = (TaxpayerActivity.objects.count(), TaxpayerAddress.objects.count())
        data = self.lookup()
        activities = {a["code"]: a["state"] for a in data["collections"]["activities"]}
        self.assertEqual(activities, {"620200": "same", "702000": "changed", "731001": "new", "111111": "not_reported", "222222": "not_reported"})
        addresses = {a["street"]: a["state"] for a in data["collections"]["addresses"]}
        # La manual coincide por texto; cambia porque la fuente la marca como casa matriz.
        self.assertEqual(addresses, {"CALLE FICTICIA 1": "changed", "CALLE NUEVA 2": "new", "CALLE NUEVA 3": "new", "CALLE CERRADA 9": "not_reported"})
        self.assertEqual((TaxpayerActivity.objects.count(), TaxpayerAddress.objects.count()), before)

    def apply(self, data, selected_activities, selected_addresses, **extra):
        self.org.refresh_from_db()
        return self.client.post(reverse("organization-update-from-lookup", args=[self.org.id]), {
            "version": data["version"], "source": "Tupana", "fetched_at": data["fetched_at"], "confirm": "yes",
            "provider_reference": "ment_1",
            "consulted_activities": json.dumps(data["activities"]), "consulted_addresses": json.dumps(data["addresses"]),
            "selected_activities": json.dumps(selected_activities), "selected_addresses": json.dumps(selected_addresses), **extra,
        })

    def test_selected_items_apply_and_nothing_is_deleted(self):
        data = self.lookup()
        self.apply(data, ["731001"], ["addr_2"])
        codes = dict(self.org.activities.values_list("code", "name"))
        # 731001 agregada; 702000 no seleccionada conserva su nombre; nada se borra.
        self.assertEqual(codes, {"620200": "CONSULTORES INFORMATICOS", "702000": "NOMBRE ANTIGUO", "731001": "PUBLICIDAD", "111111": "YA NO INFORMADA", "222222": "AGREGADA A MANO"})
        streets = set(self.org.addresses.values_list("street", flat=True))
        self.assertEqual(streets, {"CALLE FICTICIA 1", "CALLE CERRADA 9", "CALLE NUEVA 2"})  # CALLE NUEVA 3 descartada
        # Lo de Tupana que no vino queda marcado; lo manual no se marca.
        self.assertIsNotNone(self.org.activities.get(code="111111").not_reported_since)
        self.assertIsNone(self.org.activities.get(code="222222").not_reported_since)
        self.assertIsNotNone(self.org.addresses.get(street="CALLE CERRADA 9").not_reported_since)
        manual = self.org.addresses.get(street="CALLE FICTICIA 1")
        self.assertEqual((manual.is_main_in_sii, manual.phone, manual.source), (False, "+56 2 1111 1111", DataSource.MANUAL))
        event = AuditEvent.objects.get(organization=self.org, action="organization.updated")
        self.assertEqual((event.metadata["activities_added"], event.metadata["addresses_added"], event.metadata["activities_not_reported"], event.metadata["addresses_not_reported"]), (["731001"], 1, ["111111"], 1))
        self.assertEqual(self.other.activities.get().name, "DE OTRA ORGANIZACION")  # otra organización intacta

    def test_changed_items_update_without_erasing_manual_values(self):
        data = self.lookup()
        self.apply(data, ["702000"], ["addr_1"])
        self.assertEqual(self.org.activities.get(code="702000").name, "CONSULTORIA")
        address = self.org.addresses.get(street="CALLE FICTICIA 1")
        # Se aplica la marca de casa matriz y la referencia; el teléfono manual no se borra (la fuente no lo trae).
        self.assertEqual((address.is_main_in_sii, address.phone, address.external_reference), (True, "+56 2 1111 1111", "addr_1"))

    def test_nothing_selected_changes_nothing(self):
        data = self.lookup()
        before = list(TaxpayerActivity.objects.order_by("id").values()) + list(TaxpayerAddress.objects.order_by("id").values())
        response = self.apply(data, [], [])
        self.assertEqual(list(TaxpayerActivity.objects.order_by("id").values()) + list(TaxpayerAddress.objects.order_by("id").values()), before)
        self.assertFalse(AuditEvent.objects.filter(action="organization.updated").exists())
        self.assertIn("No seleccionaste cambios; los datos se mantienen.", [str(m) for m in get_messages(response.wsgi_request)])

    def test_selection_outside_consulted_items_is_ignored(self):
        data = self.lookup()
        self.apply(data, ["999999"], ["addr_inventada"])
        self.assertFalse(self.org.activities.filter(code="999999").exists())
        self.assertEqual(self.org.addresses.count(), 2)

    def test_concurrent_change_rejects_collection_update(self):
        data = self.lookup()
        TaxpayerOrganization.objects.filter(pk=self.org.pk).update(updated_at=timezone.now())
        self.apply(data, ["731001"], ["addr_2"])
        self.assertFalse(self.org.activities.filter(code="731001").exists())
        self.assertFalse(self.org.addresses.filter(street="CALLE NUEVA 2").exists())

    def test_invalid_submitted_collections_are_rejected(self):
        data = self.lookup()
        self.apply(data, ["731001"], [], consulted_activities='[{"code": "73-1001", "name": "x"}]')
        self.assertFalse(self.org.activities.filter(code="731001").exists())


class MigrationOnExistingDataTests(TransactionTestCase):
    """Regresión: la migración 0003 es aditiva y conserva los contribuyentes existentes."""

    def test_existing_organizations_survive_migration(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("organizations", "0002_organization_archive")])
        old_apps = executor.loader.project_state([("organizations", "0002_organization_archive")]).apps
        Old = old_apps.get_model("organizations", "TaxpayerOrganization")
        old = Old.objects.create(tax_id="77.111.222-3", legal_name="Taller Ficticio SpA", trade_name="Taller", dte_41_enabled=True)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        org = TaxpayerOrganization.objects.get(pk=old.pk)
        self.assertEqual((org.tax_id, org.legal_name, org.trade_name, org.dte_41_enabled), ("77.111.222-3", "Taller Ficticio SpA", "Taller", True))
        self.assertEqual((org.public_email, org.external_source, org.external_reference, org.external_synced_at), ("", "", "", None))
        self.assertEqual((org.activities.count(), org.addresses.count()), (0, 0))
