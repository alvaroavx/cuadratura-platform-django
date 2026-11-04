from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

INVALID_MESSAGE = "El nombre de usuario o la contraseña no son correctos."


class LoginPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="cajera", password="clave-de-prueba-1")

    def test_get_renders_standalone_login(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usa tu nombre de usuario, no tu correo.")
        self.assertContains(response, 'autocomplete="username"')
        self.assertContains(response, 'autocomplete="current-password"')
        # Plantilla independiente: sin breadcrumb ni enlaces a vistas protegidas.
        self.assertNotContains(response, "Mis organizaciones")
        self.assertNotContains(response, "Olvidaste")

    def test_invalid_credentials_show_single_message_and_keep_username(self):
        response = self.client.post(reverse("login"), {"username": "cajera", "password": "incorrecta"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, INVALID_MESSAGE, count=1)
        self.assertContains(response, 'value="cajera"')
        self.assertContains(response, "<title>Error · Ingresar · Cuadratura</title>", html=False)

    def test_inactive_user_gets_same_message(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(reverse("login"), {"username": "cajera", "password": "clave-de-prueba-1"})
        self.assertContains(response, INVALID_MESSAGE, count=1)

    def test_empty_fields_show_spanish_messages(self):
        response = self.client.post(reverse("login"), {"username": "", "password": ""})
        self.assertContains(response, "Ingresa tu nombre de usuario.")
        self.assertContains(response, "Ingresa tu contraseña.")

    def test_valid_login_redirects_to_safe_next(self):
        target = reverse("organization-create")
        response = self.client.post(
            reverse("login") + f"?next={target}",
            {"username": "cajera", "password": "clave-de-prueba-1", "next": target},
        )
        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_external_next_is_ignored(self):
        response = self.client.post(
            reverse("login"),
            {"username": "cajera", "password": "clave-de-prueba-1", "next": "https://ejemplo.invalid/"},
        )
        self.assertRedirects(response, reverse("organization-list"), fetch_redirect_response=False)
