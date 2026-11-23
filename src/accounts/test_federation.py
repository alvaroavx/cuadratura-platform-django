from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from allauth.socialaccount.models import SocialAccount, SocialToken
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import FederatedIdentity

PASSWORD = "clave-local-de-prueba"
GOOGLE = {
    "APPS": [{"client_id": "test-client", "secret": "test-secret"}],
    "OAUTH_PKCE_ENABLED": True,
    "SCOPE": ["openid", "email"],
}


@override_settings(
    FEDERATED_LOGIN_PROVIDERS={"google": "Google"},
    SOCIALACCOUNT_PROVIDERS={"google": GOOGLE},
)
class FederatedLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cajera", email="cajera@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user("otra", password=PASSWORD)

    def begin(self, *, connect=False, next_url=""):
        route = "update-login-method" if connect else "federated-login"
        data = {"password": PASSWORD, "action": "connect"} if connect else {"next": next_url}
        response = self.client.post(reverse(route, args=["google"]), data)
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["http://testserver/accounts/google/login/callback/"])
        return query["state"][0]

    def callback(self, state, subject="google-sub", verified=True):
        # Simula el canje y las claims verificadas; usa el callback y estado reales de allauth.
        with (
            patch.object(GoogleOAuth2Adapter, "get_access_token_data", return_value={"access_token": "mock", "id_token": "mock"}),
            patch.object(GoogleOAuth2Adapter, "_decode_id_token", return_value={
                "sub": subject, "email": self.user.email, "email_verified": verified,
            }),
        ):
            return self.client.get(reverse("google_callback"), {"state": state, "code": "mock"})

    def test_link_then_log_in_with_existing_user_and_safe_next(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("login-methods")), "Vincular Google")
        response = self.callback(self.begin(connect=True))
        self.assertRedirects(response, reverse("login-methods"), fetch_redirect_response=False)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(FederatedIdentity.objects.get().user, self.user)
        self.assertContains(self.client.get(reverse("login-methods")), "Cuenta vinculada.")

        self.client.post(reverse("logout"))
        target = reverse("organization-create")
        response = self.callback(self.begin(next_url=target))
        self.assertRedirects(response, target, fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertFalse(SocialAccount.objects.exists())
        self.assertFalse(SocialToken.objects.exists())

    def test_matching_email_never_grants_access_without_link(self):
        response = self.callback(self.begin(), verified=True)
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertFalse(FederatedIdentity.objects.exists())

    def test_invalid_state_is_rejected_before_token_exchange_and_valid_state_is_consumed(self):
        state = self.begin()
        with patch.object(GoogleOAuth2Adapter, "get_access_token_data") as exchange:
            response = self.client.get(reverse("google_callback"), {"state": "incorrecto", "code": "mock"})
            self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
            exchange.assert_not_called()
        self.callback(state)
        with patch.object(GoogleOAuth2Adapter, "get_access_token_data") as exchange:
            self.client.get(reverse("google_callback"), {"state": state, "code": "mock"})
            exchange.assert_not_called()

    def test_rejects_unverified_or_already_linked_identity(self):
        FederatedIdentity.objects.create(provider="google", subject="ocupada", user=self.other)
        self.client.force_login(self.user)
        self.callback(self.begin(connect=True), verified=False)
        self.callback(self.begin(connect=True), subject="ocupada")
        self.assertEqual(FederatedIdentity.objects.count(), 1)
        self.assertEqual(FederatedIdentity.objects.get().user, self.other)

    def test_second_identity_requires_disconnecting_and_disconnect_revokes_login(self):
        FederatedIdentity.objects.create(provider="google", subject="google-sub", user=self.user)
        self.client.force_login(self.user)
        self.callback(self.begin(connect=True), subject="otra-identidad")
        self.assertEqual(FederatedIdentity.objects.get().subject, "google-sub")

        url = reverse("update-login-method", args=["google"])
        self.client.post(url, {"action": "disconnect", "password": "incorrecta"})
        self.assertTrue(FederatedIdentity.objects.exists())
        self.client.post(url, {"action": "disconnect", "password": PASSWORD})
        self.assertFalse(FederatedIdentity.objects.exists())

        self.client.post(reverse("logout"))
        self.callback(self.begin())
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_inactive_user_and_external_redirect(self):
        FederatedIdentity.objects.create(provider="google", subject="google-sub", user=self.user)
        response = self.callback(self.begin(next_url="https://otro.example/"))
        self.assertRedirects(response, reverse("organization-list"), fetch_redirect_response=False)
        self.client.post(reverse("logout"))

        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.callback(self.begin())
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_requires_post_csrf_and_local_password_to_begin(self):
        url = reverse("federated-login", args=["google"])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url).status_code, 403)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("update-login-method", args=["google"]),
            {"action": "connect", "password": "incorrecta"},
        )
        self.assertRedirects(response, reverse("login-methods"), fetch_redirect_response=False)
        self.assertNotIn("socialaccount_states", self.client.session)

    def test_connect_state_cannot_be_used_after_changing_local_user(self):
        self.client.force_login(self.user)
        state = self.begin(connect=True)
        # Incluso conservando el estado en la sesión, el callback exige el mismo UUID.
        pending = self.client.session["socialaccount_states"]
        self.client.force_login(self.other)
        session = self.client.session
        session["socialaccount_states"] = pending
        session.save()
        self.callback(state)
        self.assertFalse(FederatedIdentity.objects.exists())

    @override_settings(FEDERATED_LOGIN_PROVIDERS={})
    def test_disabled_provider_has_no_entry_points(self):
        self.assertNotContains(self.client.get(reverse("login")), "Ingresar con Google")
        self.assertEqual(self.client.post(reverse("federated-login", args=["google"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("google_callback")).status_code, 404)
