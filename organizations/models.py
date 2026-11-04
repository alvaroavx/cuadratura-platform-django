import uuid
from django.conf import settings
from django.db import models

class TaxpayerOrganization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tax_id = models.CharField("RUT", max_length=12, unique=True)
    legal_name = models.CharField("Razón social", max_length=200)
    trade_name = models.CharField("Nombre de fantasía", max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    dte_39_enabled = models.BooleanField("DTE 39 declarado", default=False)
    dte_41_enabled = models.BooleanField("DTE 41 declarado", default=False)
    archived_at = models.DateTimeField("Eliminado el", null=True, blank=True)
    archived_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="Eliminado por")
    archive_reason = models.TextField("Motivo de eliminación", blank=True)
    # Antecedentes públicos (ADR 0007). Editables a mano; un proveedor solo los cambia con confirmación.
    public_email = models.EmailField("Correo de contacto público", blank=True)
    external_source = models.CharField("Fuente externa de antecedentes", max_length=32, blank=True)
    external_reference = models.CharField("Identificador en la fuente externa", max_length=64, blank=True)
    external_synced_at = models.DateTimeField("Antecedentes confirmados desde la fuente el", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self): return f"{self.legal_name} ({self.tax_id})"
    @property
    def is_archived(self): return self.archived_at is not None

class DataSource(models.TextChoices):
    MANUAL = "MANUAL", "Ingreso manual"
    TUPANA = "TUPANA", "Tupana"


class TaxpayerActivity(models.Model):
    """Actividad económica del contribuyente (código SII). Nunca se elimina por ausencia en la fuente."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(TaxpayerOrganization, on_delete=models.PROTECT, related_name="activities")
    code = models.CharField("Código", max_length=6)
    name = models.CharField("Actividad", max_length=200, blank=True)
    source = models.CharField("Procedencia", max_length=16, choices=DataSource.choices, default=DataSource.MANUAL)
    last_reported_at = models.DateTimeField("Última vez informada por la fuente", null=True, blank=True)
    not_reported_since = models.DateTimeField("No informada por la fuente desde", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ("code",)
        verbose_name = "actividad económica"
        verbose_name_plural = "actividades económicas"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uq_taxpayer_activity_org_code"),
            models.CheckConstraint(condition=models.Q(code__regex=r"^[0-9]{4,6}$"), name="ck_taxpayer_activity_code"),
        ]
    def __str__(self): return f"{self.code} {self.name}".strip()


class TaxpayerAddress(models.Model):
    """Dirección registrada del contribuyente. Nunca se elimina por ausencia en la fuente."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(TaxpayerOrganization, on_delete=models.PROTECT, related_name="addresses")
    street = models.CharField("Dirección", max_length=300)
    district = models.CharField("Comuna", max_length=120, blank=True)
    city = models.CharField("Ciudad", max_length=120, blank=True)
    sii_branch_code = models.CharField("Código de sucursal SII", max_length=40, blank=True)
    phone = models.CharField("Teléfono", max_length=40, blank=True)
    is_main_in_sii = models.BooleanField("Casa matriz según SII", default=False)
    source = models.CharField("Procedencia", max_length=16, choices=DataSource.choices, default=DataSource.MANUAL)
    external_reference = models.CharField("Identificador en la fuente", max_length=64, blank=True)
    last_reported_at = models.DateTimeField("Última vez informada por la fuente", null=True, blank=True)
    not_reported_since = models.DateTimeField("No informada por la fuente desde", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ("-is_main_in_sii", "district", "street")
        verbose_name = "dirección registrada"
        verbose_name_plural = "direcciones registradas"
        constraints = [
            models.UniqueConstraint(fields=["organization", "external_reference"], condition=~models.Q(external_reference=""), name="uq_taxpayer_address_org_ref"),
        ]
    def __str__(self): return ", ".join(part for part in (self.street, self.district, self.city) if part)


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN", "Administrador de organización"
        ACCOUNTANT = "ACCOUNTANT", "Contadora/or"
        OPERATOR = "OPERATOR", "Operador/a"
        VIEWER = "VIEWER", "Solo lectura"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(TaxpayerOrganization, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="organization_memberships")
    role = models.CharField(max_length=32, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "user"], name="uq_membership_org_user")]

class UserInvitation(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        ACCEPTED = "ACCEPTED", "Aceptada"
        EXPIRED = "EXPIRED", "Expirada"
        REVOKED = "REVOKED", "Revocada"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(TaxpayerOrganization, on_delete=models.PROTECT, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=32, choices=OrganizationMembership.Role.choices)
    token_digest = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_invitations")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "email"], condition=models.Q(status="PENDING"), name="uq_pending_invitation_org_email")]

