from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.providers.base import AuthProcess
from allauth.socialaccount.providers.google.views import oauth2_callback
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .federation import change_identity
from .forms import LoginForm


class LocalLoginView(LoginView):
    authentication_form = LoginForm

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            **kwargs, federated_providers=settings.FEDERATED_LOGIN_PROVIDERS,
        )


@never_cache
@require_POST
def federated_login(request, provider):
    require_enabled(provider)
    if request.user.is_authenticated:
        return redirect("login-methods")

    next_url = request.POST.get("next")
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
        next_url = None
    return get_adapter().get_provider(request, provider).redirect(
        request, process=AuthProcess.LOGIN, next_url=next_url,
    )


@login_required
@never_cache
@require_GET
def login_methods(request):
    linked = set(request.user.federated_identities.values_list("provider", flat=True))
    providers = [
        (key, name, key in linked)
        for key, name in settings.FEDERATED_LOGIN_PROVIDERS.items()
    ]
    return render(request, "registration/login_methods.html", {"providers": providers})


@login_required
@never_cache
@require_POST
def update_login_method(request, provider):
    require_enabled(provider)
    if not request.user.check_password(request.POST.get("password", "")):
        messages.error(request, "La contraseña de Cuadratura no es correcta.")
        return redirect("login-methods")

    action = request.POST.get("action")
    if action == "connect":
        return get_adapter().get_provider(request, provider).redirect(
            request, process=AuthProcess.CONNECT, data={"user_id": str(request.user.pk)},
        )
    if action != "disconnect":
        raise Http404
    try:
        change_identity(user_id=request.user.pk, provider=provider)
    except PermissionDenied:
        messages.error(request, "No fue posible desvincular la cuenta.")
    else:
        messages.success(request, "Cuenta desvinculada.")
    return redirect("login-methods")


@never_cache
@require_GET
def google_callback(request):
    require_enabled("google")
    response = oauth2_callback(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def require_enabled(provider):
    if provider not in settings.FEDERATED_LOGIN_PROVIDERS:
        raise Http404
