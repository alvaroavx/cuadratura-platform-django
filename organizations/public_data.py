"""Antecedentes públicos del contribuyente: actividades y direcciones (ADR 0007).

Reglas:
- Solo se agregan o actualizan elementos que una persona seleccionó explícitamente.
- Nada se elimina: lo que la fuente deja de informar se marca con `not_reported_since`.
- Los valores llegan desde el navegador (vista previa o comparación) y se validan aquí
  con las mismas reglas que el adaptador; solo un administrador de plataforma los envía.
"""
import json
import re

from .contributor_info import MAX_ACTIVITIES, MAX_ADDRESSES, address_key
from .models import DataSource, TaxpayerActivity, TaxpayerAddress

SOURCE_BY_PROVIDER = {"Tupana": DataSource.TUPANA}


class InvalidSubmission(ValueError):
    pass


def _text(value, limit):
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _json_list(raw, limit, label):
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise InvalidSubmission(f"{label}: formato inválido.") from exc
    if not isinstance(data, list) or len(data) > limit:
        raise InvalidSubmission(f"{label}: formato inválido o demasiados elementos.")
    return data


def parse_activities(raw):
    """Lista JSON `[{code, name}]` → `{code: name}` validado."""
    result = {}
    for item in _json_list(raw, MAX_ACTIVITIES, "Actividades"):
        if not isinstance(item, dict):
            raise InvalidSubmission("Actividades: formato inválido.")
        code = _text(item.get("code"), 6)
        if not re.fullmatch(r"\d{4,6}", code):
            raise InvalidSubmission("Actividades: código inválido.")
        result[code] = _text(item.get("name"), 200)
    return result


def parse_addresses(raw):
    """Lista JSON de direcciones → `{key: campos}` validado (la clave se recalcula en servidor)."""
    result = {}
    for item in _json_list(raw, MAX_ADDRESSES, "Direcciones"):
        if not isinstance(item, dict):
            raise InvalidSubmission("Direcciones: formato inválido.")
        street = _text(item.get("street"), 300)
        if not street:
            raise InvalidSubmission("Direcciones: falta la dirección.")
        reference = _text(item.get("reference"), 64)
        if reference and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", reference):
            raise InvalidSubmission("Direcciones: identificador inválido.")
        fields = {
            "street": street,
            "district": _text(item.get("district"), 120),
            "city": _text(item.get("city"), 120),
            "sii_branch_code": _text(item.get("branch_code"), 40),
            "phone": _text(item.get("phone"), 40),
            "is_main_in_sii": item.get("is_main_in_sii") is True,
            "external_reference": reference,
        }
        result[reference or address_key(street, fields["district"])] = fields
    return result


def parse_selection(raw, limit):
    keys = _json_list(raw, limit, "Selección")
    if not all(isinstance(key, str) and len(key) <= 500 for key in keys):
        raise InvalidSubmission("Selección: formato inválido.")
    return set(keys)


def _address_key_of(address):
    return address.external_reference or address_key(address.street, address.district)


ADDRESS_FIELDS = ("street", "district", "city", "sii_branch_code", "phone", "is_main_in_sii")


def compare_collections(organization, consulted):
    """Clasifica cada elemento como `new`, `same`, `changed` o `not_reported` (solo lectura)."""
    current_activities = {a.code: a for a in organization.activities.all()}
    activities = []
    for item in consulted["activities"]:
        existing = current_activities.get(item["code"])
        state = "new" if not existing else ("changed" if item["name"] and existing.name != item["name"] else "same")
        activities.append({**item, "state": state, "current_name": existing.name if existing else ""})
    consulted_codes = {item["code"] for item in consulted["activities"]}
    activities += [
        {"code": a.code, "name": "", "current_name": a.name, "state": "not_reported"}
        for code, a in current_activities.items() if code not in consulted_codes
    ]

    current_addresses = {_address_key_of(a): a for a in organization.addresses.all()}
    by_text = {address_key(a.street, a.district): a for a in current_addresses.values()}
    addresses, matched = [], set()
    for item in consulted["addresses"]:
        existing = current_addresses.get(item["key"]) or by_text.get(address_key(item["street"], item["district"]))
        if existing:
            matched.add(existing.pk)
            proposed = {"street": item["street"], "district": item["district"], "city": item["city"],
                        "sii_branch_code": item["branch_code"], "phone": item["phone"], "is_main_in_sii": item["is_main_in_sii"]}
            # Un vacío del proveedor no cuenta como diferencia (nunca borra).
            differs = any(value not in ("", None) and getattr(existing, name) != value for name, value in proposed.items() if name != "is_main_in_sii") \
                or existing.is_main_in_sii != item["is_main_in_sii"]
            state = "changed" if differs else "same"
            current = str(existing)
        else:
            state, current = "new", ""
        addresses.append({**item, "state": state, "current": current})
    addresses += [
        {"key": _address_key_of(a), "street": a.street, "district": a.district, "city": a.city, "state": "not_reported", "current": str(a)}
        for a in current_addresses.values() if a.pk not in matched
    ]
    return {"activities": activities, "addresses": addresses}


def create_collections(organization, *, provider, activities, addresses, reported_at):
    """Creación: guarda solo lo que la persona aceptó en la vista previa."""
    source = SOURCE_BY_PROVIDER[provider]
    TaxpayerActivity.objects.bulk_create([
        TaxpayerActivity(organization=organization, code=code, name=name, source=source, last_reported_at=reported_at)
        for code, name in activities.items()
    ])
    TaxpayerAddress.objects.bulk_create([
        TaxpayerAddress(organization=organization, source=source, last_reported_at=reported_at, **fields)
        for fields in addresses.values()
    ])
    return {"activities_added": sorted(activities), "addresses_added": len(addresses)}


def apply_collections(organization, *, provider, activities, addresses, selected_activities, selected_addresses, reported_at):
    """Edición: aplica solo lo seleccionado, conserva todo lo demás y marca lo no informado.

    `organization` debe venir bloqueada (`select_for_update`) por quien llama.
    """
    source = SOURCE_BY_PROVIDER[provider]
    summary = {"activities_added": [], "activities_updated": [], "addresses_added": 0, "addresses_updated": 0,
               "activities_not_reported": [], "addresses_not_reported": 0}

    current = {a.code: a for a in organization.activities.select_for_update()}
    for code, name in activities.items():
        existing = current.get(code)
        if existing:
            existing.last_reported_at, existing.not_reported_since = reported_at, None
            fields = ["last_reported_at", "not_reported_since", "updated_at"]
            if code in selected_activities and name and existing.name != name:
                existing.name = name
                fields.append("name")
                summary["activities_updated"].append(code)
            existing.save(update_fields=fields)
        elif code in selected_activities:
            TaxpayerActivity.objects.create(organization=organization, code=code, name=name, source=source, last_reported_at=reported_at)
            summary["activities_added"].append(code)
    for code, existing in current.items():
        if code not in activities and existing.source == source and existing.not_reported_since is None:
            existing.not_reported_since = reported_at
            existing.save(update_fields=["not_reported_since", "updated_at"])
            summary["activities_not_reported"].append(code)

    current_addresses = list(organization.addresses.select_for_update())
    by_key = {_address_key_of(a): a for a in current_addresses}
    by_text = {address_key(a.street, a.district): a for a in current_addresses}
    seen = set()
    for key, fields in addresses.items():
        existing = by_key.get(key) or by_text.get(address_key(fields["street"], fields["district"]))
        if existing:
            seen.add(existing.pk)
            existing.last_reported_at, existing.not_reported_since = reported_at, None
            update = ["last_reported_at", "not_reported_since", "updated_at"]
            if key in selected_addresses:
                for name in ADDRESS_FIELDS:
                    value = fields[name]
                    if (value not in ("", None) or name == "is_main_in_sii") and getattr(existing, name) != value:
                        setattr(existing, name, value)
                        update.append(name)
                if not existing.external_reference and fields["external_reference"]:
                    existing.external_reference = fields["external_reference"]
                    update.append("external_reference")
                if len(update) > 3:
                    summary["addresses_updated"] += 1
            existing.save(update_fields=update)
        elif key in selected_addresses:
            TaxpayerAddress.objects.create(organization=organization, source=source, last_reported_at=reported_at, **fields)
            summary["addresses_added"] += 1
    for existing in current_addresses:
        if existing.pk not in seen and existing.source == source and existing.not_reported_since is None:
            existing.not_reported_since = reported_at
            existing.save(update_fields=["not_reported_since", "updated_at"])
            summary["addresses_not_reported"] += 1
    return summary
