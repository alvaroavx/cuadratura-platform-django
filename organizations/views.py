from datetime import date, datetime
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from django.views.decorators.http import require_http_methods
from audit.services import record_event
from . import public_data
from .contributor_info import MAX_ACTIVITIES, MAX_ADDRESSES, MODEL_FIELDS, LookupStatus, get_contributor_information_provider, lookup_enabled, provider_display_name
from .rut import InvalidRut, parse_rut, same_rut_text
from .access import ADMIN_ROLES, authorized_organization, scoped_organizations
from .forms import ArchiveOrganizationForm, ControlledUserForm, InvitationForm, MembershipForm, OrganizationForm
from .models import OrganizationMembership, TaxpayerOrganization

@login_required
def organization_list(request):
    archived_count=TaxpayerOrganization.objects.filter(archived_at__isnull=False).count() if request.user.is_platform_admin else 0
    return render(request,"organizations/list.html",{"organizations":scoped_organizations(request.user).order_by("legal_name"),"can_create":request.user.is_platform_admin,"archived_count":archived_count})

@login_required
def organization_archive_list(request):
    """Archivo de contribuyentes eliminados: solo lectura, solo administradores de plataforma (ADR 0008)."""
    if not request.user.is_platform_admin: raise PermissionDenied
    archived=list(TaxpayerOrganization.objects.filter(archived_at__isnull=False).select_related("archived_by").order_by("-archived_at"))
    for org in archived:
        org.preserved={"ventas":org.sales.count(),"pagos":org.payments.count(),"boletas":org.receipts.count(),"cierres":org.monthly_closes.count()}
    return render(request,"organizations/archive_list.html",{"archived":archived})

# Proveedores cuya procedencia se acepta en auditoría (SRE se retiró el 2026-11-03, ADR 0007).
LOOKUP_SOURCES={"Tupana"}

@login_required
@require_http_methods(["GET","POST"])
def organization_create(request):
    if not request.user.is_platform_admin: raise PermissionDenied
    form=OrganizationForm(request.POST or None)
    if request.method=="POST" and form.is_valid():
        # Procedencia declarada por el formulario: solo con ella se aceptan las colecciones de la vista previa.
        source=request.POST.get("lookup_source","")
        activities,addresses={},{}
        if source in LOOKUP_SOURCES:
            try:
                activities=public_data.parse_activities(request.POST.get("lookup_activities",""))
                addresses=public_data.parse_addresses(request.POST.get("lookup_addresses",""))
            except public_data.InvalidSubmission as exc:
                form.add_error(None,f"No se pudieron incorporar los antecedentes consultados. {exc}")
        if not form.errors:
            with transaction.atomic():
                org=form.save(commit=False)
                metadata=None
                if source in LOOKUP_SOURCES:
                    org.external_source=source; org.external_synced_at=timezone.now()
                    org.external_reference=" ".join(request.POST.get("lookup_reference","").split())[:64]
                org.save()
                if source in LOOKUP_SOURCES:
                    summary=public_data.create_collections(org,provider=source,activities=activities,addresses=addresses,reported_at=org.external_synced_at)
                    metadata={"prefilled_from":source,**summary}
                record_event(organization=org,actor=request.user,action="organization.created",target=org,metadata=metadata)
            messages.success(request,"Contribuyente creado."); return redirect("organization-list")
    return render(request,"organizations/form.html",{"form":form,"title":"Nuevo contribuyente","organization_form":True,"contributor_lookup_enabled":lookup_enabled(),"contributor_lookup_source":provider_display_name()})

LOOKUP_HTTP_STATUS={LookupStatus.FOUND:200,LookupStatus.NOT_FOUND:200,LookupStatus.UNAVAILABLE:503,LookupStatus.PROVIDER_LIMITED:503,LookupStatus.DISABLED:503}

def _lookup_rate_limited(user):
    """Límite por usuario y minuto en la caché de Django (por proceso con la caché local por defecto)."""
    limit=settings.CONTRIBUTOR_INFO_RATE_LIMIT_PER_MINUTE
    if limit<=0: return False
    key=f"contributor-lookup:{user.pk}:{int(timezone.now().timestamp())//60}"
    cache.add(key,0,timeout=90)
    try: count=cache.incr(key)
    except ValueError: cache.set(key,1,timeout=90); count=1
    return count>limit

@login_required
@require_http_methods(["POST"])
def organization_tax_id_lookup(request):
    """Consulta de lectura para el formulario de creación: no escribe en la base de datos.

    Solo puede crear contribuyentes el administrador de plataforma, así que solo él consulta.
    """
    if not request.user.is_platform_admin: raise PermissionDenied
    try: rut=parse_rut(request.POST.get("tax_id",""))
    except InvalidRut as exc: return JsonResponse({"status":"invalid_tax_id","message":str(exc)},status=400)
    existing=next((org for org in TaxpayerOrganization.objects.only("id","tax_id","archived_at") if same_rut_text(rut.compact,org.tax_id)),None)
    if existing:
        payload={"status":"already_registered","tax_id":rut.formatted}
        if existing.archived_at is None: payload["organization_url"]=reverse("organization-edit",args=[existing.id])
        return JsonResponse(payload)
    return _lookup_response(request.user,rut)

def _lookup_response(user, rut, extend=None):
    """Consulta común a creación y edición: límite, proveedor y respuesta normalizada.

    `extend(info)` agrega datos propios del flujo (p. ej. la comparación en edición).
    """
    if _lookup_rate_limited(user):
        return JsonResponse({"status":"rate_limited"},status=429,headers={"Retry-After":"60"})
    result=get_contributor_information_provider().lookup(rut)
    payload={"status":result.status.value,"tax_id":rut.formatted}
    if result.info:
        info=result.info
        payload.update({
            "source":info.source,
            "fetched_at":timezone.localtime(info.fetched_at).isoformat(timespec="minutes"),
            "provider_reference":info.provider_reference,
            "fields":info.model_values(),
            **info.collections(),
        })
        if extend: payload.update(extend(info))
    return JsonResponse(payload,status=LOOKUP_HTTP_STATUS[result.status])

def _version_token(org):
    """Token de concurrencia optimista: `updated_at` exacto del registro (sin migración)."""
    return org.updated_at.isoformat()

def _field_label(name):
    return str(TaxpayerOrganization._meta.get_field(name).verbose_name)

@login_required
@require_http_methods(["POST"])
def organization_update_lookup(request, organization_id):
    """Consulta para «Actualizar datos desde RUT»: usa el RUT registrado y no escribe en la base."""
    if not request.user.is_platform_admin: raise PermissionDenied
    org=authorized_organization(request.user,organization_id,active_only=False)
    try: rut=parse_rut(org.tax_id)
    except InvalidRut: return JsonResponse({"status":"invalid_tax_id","message":"El RUT registrado no tiene un formato o dígito verificador válido; no se puede consultar."},status=400)
    def comparison(info):
        current={name:getattr(org,name) for name in MODEL_FIELDS}
        rows=info.compare(current)
        for row in rows: row["label"]=_field_label(row["field"])
        return {"comparison":rows,"version":_version_token(org),"collections":public_data.compare_collections(org,info.collections())}
    return _lookup_response(request.user,rut,extend=comparison)

def _parse_fetched_at(value):
    try: parsed=datetime.fromisoformat(str(value)[:40])
    except ValueError: return None
    return parsed.isoformat(timespec="minutes")

@login_required
@require_http_methods(["POST"])
def organization_update_from_lookup(request, organization_id):
    """Aplica solo los campos seleccionados tras confirmación explícita (ADR 0007).

    Los valores vienen del navegador; el servidor solo acepta campos de `MODEL_FIELDS`,
    nunca el RUT, ignora vacíos y rechaza la operación si el registro cambió desde la consulta.
    """
    if not request.user.is_platform_admin: raise PermissionDenied
    org=authorized_organization(request.user,organization_id,active_only=False)
    edit_url=redirect("organization-edit",organization_id=org.id)
    source=request.POST.get("source","")
    if request.POST.get("confirm")!="yes" or source not in LOOKUP_SOURCES:
        messages.error(request,"No se aplicaron cambios: falta la confirmación explícita."); return edit_url
    proposed={}
    for name in MODEL_FIELDS:
        value=" ".join(request.POST.get(f"value_{name}","").split())
        if request.POST.get(f"action_{name}")=="update" and value: proposed[name]=value
    errors=[]
    for name,value in proposed.items():
        try: TaxpayerOrganization._meta.get_field(name).clean(value,org)
        except ValidationError as exc: errors.append(f"{_field_label(name)}: {' '.join(exc.messages)}")
    try:
        activities=public_data.parse_activities(request.POST.get("consulted_activities",""))
        addresses=public_data.parse_addresses(request.POST.get("consulted_addresses",""))
        selected_activities=public_data.parse_selection(request.POST.get("selected_activities",""),MAX_ACTIVITIES)&set(activities)
        selected_addresses=public_data.parse_selection(request.POST.get("selected_addresses",""),MAX_ADDRESSES)&set(addresses)
    except public_data.InvalidSubmission as exc:
        errors.append(str(exc))
    if errors:
        messages.error(request,"No se aplicaron cambios. "+" ".join(errors)); return edit_url
    if not proposed and not selected_activities and not selected_addresses:
        messages.info(request,"No seleccionaste cambios; los datos se mantienen."); return edit_url
    with transaction.atomic():
        locked=get_object_or_404(TaxpayerOrganization.objects.select_for_update(),pk=org.pk,archived_at__isnull=True)
        if request.POST.get("version")!=_version_token(locked):
            messages.error(request,"El contribuyente fue modificado por otra persona después de la consulta. No se aplicaron cambios: revisa los datos actuales y vuelve a consultar."); return edit_url
        changed=[name for name,value in proposed.items() if getattr(locked,name)!=value]
        now=timezone.now()
        summary=public_data.apply_collections(locked,provider=source,activities=activities,addresses=addresses,
            selected_activities=selected_activities,selected_addresses=selected_addresses,reported_at=now)
        collection_changes=bool(summary["activities_added"] or summary["activities_updated"] or summary["addresses_added"] or summary["addresses_updated"])
        if not changed and not collection_changes:
            transaction.set_rollback(True)
            messages.info(request,"Sin diferencias: los datos registrados ya coinciden."); return edit_url
        for name in changed: setattr(locked,name,proposed[name])
        locked.external_source=source; locked.external_synced_at=now
        reference=" ".join(request.POST.get("provider_reference","").split())[:64]
        if reference and not locked.external_reference: locked.external_reference=reference
        locked.save(update_fields=[*changed,"external_source","external_synced_at","external_reference","updated_at"])
        record_event(organization=locked,actor=request.user,action="organization.updated",target=locked,metadata={
            "changed_fields":changed,"source":source,"fetched_at":_parse_fetched_at(request.POST.get("fetched_at","")),**summary,
        })
    parts=[_field_label(n) for n in changed]
    if summary["activities_added"] or summary["activities_updated"]: parts.append(f"{len(summary['activities_added'])} actividades nuevas y {len(summary['activities_updated'])} actualizadas")
    if summary["addresses_added"] or summary["addresses_updated"]: parts.append(f"{summary['addresses_added']} direcciones nuevas y {summary['addresses_updated']} actualizadas")
    messages.success(request,"Cambios confirmados: "+"; ".join(parts)+". Los demás datos se conservaron; nada se eliminó.")
    return edit_url

@login_required
@require_http_methods(["GET","POST"])
def organization_edit(request, organization_id):
    if not request.user.is_platform_admin: raise PermissionDenied
    org=authorized_organization(request.user,organization_id,active_only=False)
    form=OrganizationForm(request.POST or None,instance=org)
    if request.method=="POST" and form.is_valid():
        # Concurrencia optimista: si el formulario trae versión y no coincide, no se sobrescribe.
        # Sin versión (POST ajeno a la plantilla) se conserva la conducta anterior.
        version=request.POST.get("version")
        with transaction.atomic():
            locked=get_object_or_404(TaxpayerOrganization.objects.select_for_update(),pk=org.pk,archived_at__isnull=True)
            conflict=version is not None and version!=_version_token(locked)
            if not conflict:
                changed=list(form.changed_data)
                org=form.save(commit=False); org.save(update_fields=[*OrganizationForm.Meta.fields,"updated_at"])
                record_event(organization=org,actor=request.user,action="organization.updated",target=org,metadata={"active":org.is_active,"dte_39":org.dte_39_enabled,"dte_41":org.dte_41_enabled,"changed_fields":changed})
        if not conflict:
            messages.success(request,"Contribuyente actualizado."); return redirect("organization-list")
        form.add_error(None,"Otra persona modificó este contribuyente mientras lo editabas. No se guardó nada: recarga la página para ver los datos actuales.")
        # Se conserva la versión enviada: reenviar el mismo formulario vuelve a ser rechazado.
        return render(request,"organizations/form.html",{"form":form,"organization":locked,"title":"Configurar contribuyente","organization_form":True,"organization_version":version,"contributor_lookup_enabled":lookup_enabled(),"contributor_lookup_source":provider_display_name()},status=409)
    return render(request,"organizations/form.html",{"form":form,"organization":org,"title":"Configurar contribuyente","organization_form":True,"organization_version":request.POST.get("version") or _version_token(org),"contributor_lookup_enabled":lookup_enabled(),"contributor_lookup_source":provider_display_name()})

@login_required
@require_http_methods(["GET","POST"])
def organization_archive(request, organization_id):
    """Eliminación lógica: el contribuyente se oculta y bloquea, pero su historial se conserva.

    El borrado físico no es posible: todas las relaciones son PROTECT y la auditoría es append-only.
    """
    if not request.user.is_platform_admin: raise PermissionDenied
    org=authorized_organization(request.user,organization_id,active_only=False)
    form=ArchiveOrganizationForm(request.POST or None,organization=org)
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            org=get_object_or_404(TaxpayerOrganization.objects.select_for_update(),pk=org.pk,archived_at__isnull=True)
            org.archived_at=timezone.now(); org.archived_by=request.user; org.archive_reason=form.cleaned_data["reason"]; org.is_active=False
            org.save(update_fields=["archived_at","archived_by","archive_reason","is_active","updated_at"])
            record_event(organization=org,actor=request.user,action="organization.archived",target=org,metadata={"reason":org.archive_reason})
        messages.success(request,f"Contribuyente {org.legal_name} eliminado. Su historial se conserva.")
        return redirect("organization-list")
    return render(request,"organizations/archive.html",{"form":form,"organization":org})

@login_required
@require_http_methods(["GET","POST"])
def invite_user(request, organization_id):
    org=authorized_organization(request.user,organization_id,roles=ADMIN_ROLES)
    form=InvitationForm(request.POST or None)
    if request.method=="POST" and form.is_valid():
        invitation,_raw=form.save_for(org,request.user); record_event(organization=org,actor=request.user,action="invitation.created",target=invitation,metadata={"role":invitation.role}); messages.success(request,"Invitación creada. El envío de correo aún no está configurado."); return redirect("organization-period-current",organization_id=org.id)
    return render(request,"organizations/form.html",{"form":form,"organization":org,"title":"Invitar usuario"})

@login_required
@require_http_methods(["GET","POST"])
def membership_create(request, organization_id):
    org=authorized_organization(request.user,organization_id,roles=ADMIN_ROLES)
    form=MembershipForm(request.POST or None)
    if request.method=="POST" and form.is_valid():
        membership=form.save(commit=False); membership.organization=org; membership.save(); record_event(organization=org,actor=request.user,action="membership.created",target=membership,metadata={"role":membership.role}); messages.success(request,"Membresía guardada."); return redirect("organization-period-current",organization_id=org.id)
    return render(request,"organizations/form.html",{"form":form,"organization":org,"title":"Equipo y permisos"})

@login_required
@require_http_methods(["GET","POST"])
def controlled_user_create(request, organization_id):
    if not request.user.is_platform_admin: raise PermissionDenied
    org=authorized_organization(request.user,organization_id,active_only=False)
    form=ControlledUserForm(request.POST or None)
    if request.method=="POST" and form.is_valid():
        data=form.cleaned_data
        with transaction.atomic():
            user=User.objects.create_user(username=data["username"],email=data["email"],first_name=data["first_name"],last_name=data["last_name"],password=data["password"])
            membership=OrganizationMembership.objects.create(organization=org,user=user,role=data["role"])
            record_event(organization=org,actor=request.user,action="user.controlled_created",target=membership,metadata={"role":membership.role})
        messages.success(request,"Usuario y membresía creados. Entregue la contraseña inicial por un canal seguro.")
        return redirect("organization-period-current",organization_id=org.id)
    return render(request,"organizations/form.html",{"form":form,"organization":org,"title":"Crear usuario"})

@login_required
def organization_administration(request, organization_id, period):
    org=authorized_organization(request.user,organization_id,roles=ADMIN_ROLES)
    month=date.fromisoformat(f"{period}-01")
    return render(request,"organizations/administration.html",{"organization":org,"period":month,"is_platform_admin":request.user.is_platform_admin})
