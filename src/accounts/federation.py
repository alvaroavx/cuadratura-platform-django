import logging

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.providers.base import AuthProcess
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect

from .models import FederatedIdentity

logger = logging.getLogger("cuadratura.accounts")


def change_identity(*, user_id, provider, subject=None):
    """Serialize changes for an existing user and keep one identity per provider."""
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().filter(pk=user_id, is_active=True).first()
        if not user:
            raise PermissionDenied

        links = FederatedIdentity.objects.filter(user=user, provider=provider)
        if subject is None:
            if not user.has_usable_password():
                raise PermissionDenied
            changed, _ = links.delete()
            action = "unlink"
        else:
            if links.exclude(subject=subject).exists():
                raise PermissionDenied
            account, changed = FederatedIdentity.objects.get_or_create(
                provider=provider, subject=subject, defaults={"user": user},
            )
            if account.user_id != user.pk:
                raise PermissionDenied
            action = "link"

        if changed:
            transaction.on_commit(
                lambda: logger.info("federated_identity_%s provider=%s user=%s", action, provider, user_id)
            )


class FederatedAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return False

    def populate_user(self, request, sociallogin, data):
        # allauth updates SocialAccount during lookup, before pre_social_login.
        sociallogin.account.extra_data = {}
        return sociallogin.user

    def pre_social_login(self, request, sociallogin):
        provider = sociallogin.account.provider
        state = sociallogin.state
        data = state.get("data") or {}
        if provider not in settings.FEDERATED_LOGIN_PROVIDERS:
            self.reject(request, "El acceso con ese proveedor no está disponible.")

        if state.get("process") == AuthProcess.CONNECT:
            if not request.user.is_active or data.get("user_id") != str(request.user.pk):
                self.reject(request, "Vuelve a iniciar sesión para vincular tu cuenta.")
            if not any(address.verified for address in sociallogin.email_addresses):
                self.reject(request, "El proveedor debe entregar un correo verificado.")
            try:
                change_identity(user_id=request.user.pk, provider=provider, subject=sociallogin.account.uid)
            except PermissionDenied:
                self.reject(request, "No se pudo vincular esta identidad con tu cuenta.")
            messages.success(request, "Cuenta vinculada. Ya puedes usarla para ingresar.")
            raise ImmediateHttpResponse(redirect("login-methods"))

        if state.get("process") != AuthProcess.LOGIN or request.user.is_authenticated:
            self.reject(request, "Vuelve a iniciar el acceso desde Cuadratura.")
        identity = FederatedIdentity.objects.select_related("user").filter(
            provider=provider, subject=sociallogin.account.uid, user__is_active=True,
        ).first()
        if identity is None:
            self.reject(request, "Para ingresar con esta cuenta, primero vincúlala desde Cuadratura.")

        login(request, identity.user, backend="django.contrib.auth.backends.ModelBackend")
        raise ImmediateHttpResponse(redirect(state.get("next") or settings.LOGIN_REDIRECT_URL))

    def on_authentication_error(self, request, provider, error=None, exception=None, extra_context=None):
        self.reject(request, "No fue posible completar la autenticación. Inténtalo nuevamente.")

    @staticmethod
    def reject(request, message):
        messages.error(request, message)
        target = "login-methods" if request.user.is_authenticated else "login"
        raise ImmediateHttpResponse(redirect(target))
