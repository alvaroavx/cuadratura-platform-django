"""Consulta de antecedentes públicos de contribuyentes por RUT (ADR 0007).

La lógica de negocio depende solo de `ContributorInformationProvider` y del
resultado normalizado; Tupana es la implementación vigente y es sustituible
(SRE fue la primera y se retiró el 2026-11-03). Nada aquí escribe en la base de
datos: la consulta es de lectura y lo obtenido solo se persiste cuando una
persona lo confirma.

No se registran API keys, cuerpos de respuesta ni RUT consultados en los logs.
"""
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from .rut import InvalidRut, Rut, parse_rut

logger = logging.getLogger("cuadratura.contributor_info")

# Campos del modelo TaxpayerOrganization que una consulta puede proponer. El RUT no
# está aquí: es el identificador consultado, nunca un valor que la consulta reemplace.
MODEL_FIELDS = ("legal_name", "trade_name", "public_email")
MAX_ACTIVITIES = 50
MAX_ADDRESSES = 1000


class LookupStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    PROVIDER_LIMITED = "provider_limited"  # el proveedor limitó la frecuencia o el cupo
    DISABLED = "disabled"


@dataclass(frozen=True)
class EconomicActivity:
    code: str
    name: str = ""


@dataclass(frozen=True)
class RegisteredAddress:
    street: str
    district: str = ""  # comuna
    city: str = ""
    branch_code: str = ""  # código de sucursal SII, si existe
    phone: str = ""
    is_main_in_sii: bool = False
    reference: str = ""  # identificador opaco del proveedor (addr_…)

    @property
    def key(self):
        """Clave de comparación: el identificador del proveedor o, si no hay, calle + comuna normalizadas."""
        return self.reference or address_key(self.street, self.district)


def address_key(street, district):
    return "txt:" + "|".join(" ".join(str(v or "").upper().split()) for v in (street, district))


@dataclass(frozen=True)
class ContributorInfo:
    """Antecedentes normalizados, independientes del JSON del proveedor.

    Un texto vacío o una colección vacía significan «sin información», nunca «borrar».
    """

    tax_id: Rut
    source: str
    fetched_at: datetime
    legal_name: str = ""
    trade_name: str = ""
    public_email: str = ""
    activities: tuple = ()  # EconomicActivity
    addresses: tuple = ()  # RegisteredAddress
    provider_reference: str = ""  # identificador opaco del proveedor (p. ej. ment_…)
    addresses_truncated: bool = False

    def model_values(self):
        """Valores no vacíos para campos existentes del modelo (para autocompletar o comparar)."""
        return {name: getattr(self, name) for name in MODEL_FIELDS if getattr(self, name)}

    def compare(self, current):
        """Compara con los valores registrados (`{campo: valor}`) para los campos del modelo.

        La acción sugerida es «update» solo si el proveedor trae un valor no vacío y distinto;
        un vacío del proveedor siempre sugiere «keep» y nunca se ofrece como cambio.
        """
        rows = []
        for name in MODEL_FIELDS:
            consulted = getattr(self, name)
            registered = current.get(name) or ""
            changed = bool(consulted) and " ".join(registered.split()) != consulted
            rows.append({"field": name, "current": registered, "consulted": consulted, "changed": changed,
                         "suggested_action": "update" if changed else "keep"})
        return rows

    def collections(self):
        """Colecciones normalizadas para la vista previa y la comparación."""
        return {
            "activities": [{"code": a.code, "name": a.name} for a in self.activities],
            "addresses": [address_payload(a) for a in self.addresses],
            "addresses_truncated": self.addresses_truncated,
        }


def address_payload(address):
    return {
        "key": address.key, "street": address.street, "district": address.district, "city": address.city,
        "branch_code": address.branch_code, "phone": address.phone, "is_main_in_sii": address.is_main_in_sii,
        "reference": address.reference,
    }


@dataclass(frozen=True)
class LookupResult:
    status: LookupStatus
    info: ContributorInfo | None = None
    detail: str = field(default="", compare=False)  # solo para diagnóstico interno


class ContributorInformationProvider(Protocol):
    name: str

    def lookup(self, rut: Rut) -> LookupResult: ...


class DisabledContributorInformationProvider:
    name = "disabled"

    def lookup(self, rut):
        return LookupResult(LookupStatus.DISABLED)


class FakeContributorInformationProvider:
    """Proveedor determinista para pruebas y desarrollo sin red."""

    name = "fake"

    def __init__(self, results=None, default=None):
        self.results = results or {}
        self.default = default or LookupResult(LookupStatus.NOT_FOUND)
        self.calls = []

    def lookup(self, rut):
        self.calls.append(rut)
        return self.results.get(rut.compact, self.default)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # una redirección se trata como error: el destino lo fija el código


def _text(value, limit=500):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _email(value):
    text = _text(value, 254)
    if not text:
        return ""
    try:
        validate_email(text)
    except ValidationError:
        return ""
    return text.lower()


def _request_id(raw):
    """`request_id` del error de Tupana (útil para soporte; no contiene datos del contribuyente)."""
    try:
        error = json.loads(raw.decode("utf-8")).get("error")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return ""
    request_id = error.get("request_id") if isinstance(error, dict) else None
    return f":{request_id}" if isinstance(request_id, str) and re.fullmatch(r"req_[A-Za-z0-9]{1,64}", request_id) else ""


def _list(value, limit=100):
    return [item for item in value[:limit] if isinstance(item, dict)] if isinstance(value, list) else []


class HttpJsonProvider:
    """Base común: GET/POST JSON a una URL fija, sin redirecciones, con timeout y límite de tamaño."""

    name = "http"
    MAX_RESPONSE_BYTES = 1024 * 1024  # una empresa grande informa cientos de direcciones (~140 KiB)

    def __init__(self, *, timeout_seconds, opener=None):
        self._timeout = timeout_seconds
        self._opener = opener or urllib.request.build_opener(_NoRedirect)

    def __repr__(self):
        return f"<{type(self).__name__} timeout={self._timeout}>"  # nunca credenciales

    def _fetch(self, request):
        """Devuelve (status_http | None, bytes, detalle_error)."""
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.status, response.read(self.MAX_RESPONSE_BYTES + 1), ""
        except urllib.error.HTTPError as exc:
            return exc.code, (exc.read(self.MAX_RESPONSE_BYTES + 1) if exc.fp else b""), ""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return None, b"", f"network:{type(exc).__name__}"

    def _log(self, result, http_status, started):
        elapsed_ms = int((time.monotonic() - started) * 1000)
        level = logging.INFO if result.status in (LookupStatus.FOUND, LookupStatus.NOT_FOUND) else logging.WARNING
        logger.log(
            level,
            "contributor_lookup provider=%s status=%s http=%s detail=%s ms=%s",
            self.name, result.status.value, http_status, result.detail or "-", elapsed_ms,
        )


class TupanaContributorInformationProvider(HttpJsonProvider):
    """Cliente de `GET https://api.tupana.ai/v2/master-entities/?rut=` (nómina de empresas).

    Contrato verificado con llamadas reales el 2026-11-04 (ver docs/integrations/tupana.md):
    acepta el RUT con o sin puntos (se envía sin puntos, como pide la OpenAPI); un RUT
    inexistente responde **200** con `name: ""`, `id: null` y colecciones vacías; los ids son
    cadenas opacas (`ment_`, `addr_`, `acti_`); cada dirección trae `is_main_in_sii` e
    `is_default`; una key inválida responde 403 con `{"error": {"type", "code", "message",
    "request_id", …}}`, distinto del esquema de la OpenAPI.
    """

    name = "Tupana"
    ENDPOINT = "https://api.tupana.ai/v2/master-entities/"
    RUT_PATTERN = re.compile(r"^[0-9]{7,8}-[0-9K]$")

    def __init__(self, *, api_key, timeout_seconds, opener=None):
        super().__init__(timeout_seconds=timeout_seconds, opener=opener)
        self._api_key = api_key

    def lookup(self, rut):
        started = time.monotonic()
        if not self.RUT_PATTERN.match(rut.compact):
            result = LookupResult(LookupStatus.NOT_FOUND, detail="rut_outside_provider_pattern")
            self._log(result, None, started)
            return result
        request = urllib.request.Request(
            f"{self.ENDPOINT}?{urllib.parse.urlencode({'rut': rut.compact})}",
            method="GET",
            headers={"Authorization": f"Api-Key {self._api_key}", "Accept": "application/json", "User-Agent": "Cuadratura/1"},
        )
        status, raw, network_error = self._fetch(request)
        if network_error:
            result = LookupResult(LookupStatus.UNAVAILABLE, detail=network_error)
        else:
            result = self._interpret(status, raw, rut)
        self._log(result, status, started)
        return result

    def _interpret(self, status, raw, rut):
        if len(raw) > self.MAX_RESPONSE_BYTES:
            return LookupResult(LookupStatus.UNAVAILABLE, detail="response_too_large")
        if status == 404:
            return LookupResult(LookupStatus.NOT_FOUND, detail="http_404")
        if status in (401, 403):
            return LookupResult(LookupStatus.UNAVAILABLE, detail=f"credentials_rejected{_request_id(raw)}")
        if status == 429:
            return LookupResult(LookupStatus.PROVIDER_LIMITED, detail="http_429")
        if status != 200:
            return LookupResult(LookupStatus.UNAVAILABLE, detail=f"http_{status}{_request_id(raw)}")
        if not raw.strip():
            return LookupResult(LookupStatus.NOT_FOUND, detail="empty_body")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return LookupResult(LookupStatus.UNAVAILABLE, detail="invalid_json")
        if isinstance(data, list):  # tolerancia: algunas APIs devuelven una lista de resultados
            if not data:
                return LookupResult(LookupStatus.NOT_FOUND, detail="empty_list")
            data = data[0] if len(data) == 1 else None
        if not isinstance(data, dict):
            return LookupResult(LookupStatus.UNAVAILABLE, detail="unexpected_schema")
        legal_name = _text(data.get("name"))
        if not data or not legal_name:
            return LookupResult(LookupStatus.NOT_FOUND, detail="no_data")
        returned = data.get("tax_id")
        if returned:
            try:
                if parse_rut(returned) != rut:
                    return LookupResult(LookupStatus.UNAVAILABLE, detail="rut_mismatch")
            except InvalidRut:
                return LookupResult(LookupStatus.UNAVAILABLE, detail="rut_mismatch")
        info = ContributorInfo(
            tax_id=rut,
            source=self.name,
            fetched_at=timezone.now(),
            legal_name=legal_name,
            public_email=_email(data.get("email")),
            activities=self._activities(data.get("activities")),
            addresses=self._addresses(data.get("addresses")),
            addresses_truncated=isinstance(data.get("addresses"), list) and len(data["addresses"]) > MAX_ADDRESSES,
            provider_reference=_text(str(data.get("opaque_id") or data.get("id") or ""), 64),
        )
        return LookupResult(LookupStatus.FOUND, info=info)

    @staticmethod
    def _activities(value):
        activities, seen = [], set()
        for item in _list(value, MAX_ACTIVITIES):
            code = _text(str(item.get("code") or ""), 10)
            if re.fullmatch(r"\d{4,6}", code) and code not in seen:
                seen.add(code)
                activities.append(EconomicActivity(code=code, name=_text(item.get("name"), 200)))
        return tuple(activities)

    @staticmethod
    def _addresses(value):
        addresses, seen = [], set()
        for item in _list(value, MAX_ADDRESSES):
            street = _text(item.get("address"), 300)
            if not street:
                continue
            district = item.get("district") if isinstance(item.get("district"), dict) else {}
            city = district.get("city") if isinstance(district.get("city"), dict) else {}
            address = RegisteredAddress(
                street=street,
                district=_text(district.get("name"), 120),
                city=_text(city.get("name"), 120),
                branch_code=_text(item.get("sii_branch_code"), 40),
                phone=_text(item.get("phone"), 40),
                is_main_in_sii=item.get("is_main_in_sii") is True,
                reference=_text(str(item.get("id") or ""), 64),
            )
            # `is_default` es una preferencia de facturación de la plataforma Tupana, no un dato del contribuyente: no se usa.
            if address.key not in seen:
                seen.add(address.key)
                addresses.append(address)
        return tuple(addresses)


def get_contributor_information_provider():
    """Proveedor configurado por entorno. Sin configuración completa queda deshabilitado."""
    name = (settings.CONTRIBUTOR_INFO_PROVIDER or "disabled").strip().lower()
    if name == "tupana" and settings.TUPANA_API_KEY:
        return TupanaContributorInformationProvider(
            api_key=settings.TUPANA_API_KEY, timeout_seconds=settings.CONTRIBUTOR_INFO_TIMEOUT_SECONDS
        )
    if name not in ("disabled", ""):
        logger.warning("contributor_lookup provider=%s status=disabled detail=not_configured", name[:20])
    return DisabledContributorInformationProvider()


def lookup_enabled():
    return not isinstance(get_contributor_information_provider(), DisabledContributorInformationProvider)


def provider_display_name():
    return get_contributor_information_provider().name
