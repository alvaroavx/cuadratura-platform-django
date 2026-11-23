from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("ingresar/", views.LocalLoginView.as_view(), name="login"),
    path("salir/", LogoutView.as_view(), name="logout"),
    path("ingresar/con/<slug:provider>/", views.federated_login, name="federated-login"),
    path("mi-cuenta/accesos/", views.login_methods, name="login-methods"),
    path("mi-cuenta/accesos/<slug:provider>/", views.update_login_method, name="update-login-method"),
    path("accounts/google/login/callback/", views.google_callback, name="google_callback"),
]
