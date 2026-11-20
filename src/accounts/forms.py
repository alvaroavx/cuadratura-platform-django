from django.contrib.auth.forms import AuthenticationForm


class LoginForm(AuthenticationForm):
    """Formulario de ingreso con mensajes en el tono de Cuadratura.

    Solo cambia textos; la autenticación sigue a cargo de AuthenticationForm.
    Cuenta inactiva y credenciales inválidas comparten mensaje para no revelar
    si la cuenta existe (ModelBackend ya rechaza inactivos como inválidos).
    """

    error_messages = {
        "invalid_login": (
            "El nombre de usuario o la contraseña no son correctos. "
            "Recuerda que el nombre de usuario no es tu correo."
        ),
        "inactive": (
            "El nombre de usuario o la contraseña no son correctos. "
            "Recuerda que el nombre de usuario no es tu correo."
        ),
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.fields["username"].error_messages["required"] = "Ingresa tu nombre de usuario."
        self.fields["password"].error_messages["required"] = "Ingresa tu contraseña."
