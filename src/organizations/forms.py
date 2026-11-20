import hashlib
import secrets
from datetime import timedelta
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from .models import OrganizationMembership, TaxpayerOrganization, UserInvitation
from accounts.models import User

class OrganizationForm(forms.ModelForm):
    class Meta:
        model=TaxpayerOrganization
        fields=("tax_id","legal_name","trade_name","public_email","is_active","dte_39_enabled","dte_41_enabled")
        labels={
            "tax_id":"RUT",
            "legal_name":"Razón social",
            "trade_name":"Nombre de fantasía",
            "public_email":"Correo de contacto público",
            "is_active":"Contribuyente activo",
            "dte_39_enabled":"DTE 39 — Boleta electrónica afecta",
            "dte_41_enabled":"DTE 41 — Boleta electrónica exenta",
        }
        help_texts={
            "dte_39_enabled":"Habilita el registro histórico de boletas afectas para esta organización.",
            "dte_41_enabled":"Habilita el registro histórico de boletas exentas para esta organización.",
            "is_active":"Las personas asignadas solo pueden trabajar en contribuyentes activos.",
        }

def _normalize_tax_id(value):
    return value.replace(".", "").replace(" ", "").strip().upper()

class ArchiveOrganizationForm(forms.Form):
    reason=forms.CharField(label="Motivo", widget=forms.Textarea(attrs={"rows":3}), max_length=500,
        help_text="Queda registrado en la auditoría del contribuyente.")
    confirm_tax_id=forms.CharField(label="Escribe el RUT del contribuyente para confirmar", max_length=20)
    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs); self.organization=organization
        self.fields["confirm_tax_id"].help_text=f"Debe coincidir con {organization.tax_id}."
    def clean_reason(self):
        reason=self.cleaned_data["reason"].strip()
        if not reason: raise forms.ValidationError("Indica el motivo.")
        return reason
    def clean_confirm_tax_id(self):
        value=self.cleaned_data["confirm_tax_id"]
        if _normalize_tax_id(value)!=_normalize_tax_id(self.organization.tax_id):
            raise forms.ValidationError("El RUT no coincide con el del contribuyente.")
        return value

class InvitationForm(forms.ModelForm):
    class Meta:
        model=UserInvitation
        fields=("email","role")
    def save_for(self, organization, actor):
        raw=secrets.token_urlsafe(32)
        invitation=self.save(commit=False); invitation.organization=organization; invitation.invited_by=actor
        invitation.token_digest=hashlib.sha256(raw.encode()).hexdigest(); invitation.expires_at=timezone.now()+timedelta(days=7); invitation.save()
        return invitation, raw

class MembershipForm(forms.ModelForm):
    class Meta:
        model=OrganizationMembership
        fields=("user","role","is_active")

class ControlledUserForm(forms.Form):
    username=forms.CharField(label="Nombre de usuario",max_length=150)
    email=forms.EmailField(label="Correo")
    first_name=forms.CharField(label="Nombre",max_length=150,required=False)
    last_name=forms.CharField(label="Apellidos",max_length=150,required=False)
    password=forms.CharField(label="Contraseña inicial",widget=forms.PasswordInput,help_text="Entréguela por un canal seguro y solicite cambiarla al primer ingreso.")
    role=forms.ChoiceField(label="Rol en la organización",choices=OrganizationMembership.Role.choices)
    def clean_username(self):
        value=self.cleaned_data["username"]
        if User.objects.filter(username=value).exists(): raise forms.ValidationError("Ya existe ese nombre de usuario.")
        return value
    def clean_email(self):
        value=self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=value).exists(): raise forms.ValidationError("Ya existe un usuario con ese correo.")
        return value
    def clean(self):
        cleaned=super().clean()
        if cleaned.get("password"):
            candidate=User(username=cleaned.get("username",""),email=cleaned.get("email",""),first_name=cleaned.get("first_name",""),last_name=cleaned.get("last_name",""))
            validate_password(cleaned["password"],candidate)
        return cleaned
