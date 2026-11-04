"""Validación y formato de RUT chileno (módulo 11).

Solo se usa para habilitar la consulta externa por RUT. El formulario de
contribuyentes no exige dígito verificador: hay datos locales y de prueba que no
lo cumplen, y endurecerlo cambiaría un comportamiento vigente.
"""
import re
from typing import NamedTuple

_RUT_PATTERN = re.compile(r"^(\d{1,8})([0-9K])$")


class InvalidRut(ValueError):
    pass


class Rut(NamedTuple):
    body: int
    dv: str

    @property
    def compact(self):
        """Formato sin puntos y con guion: 61704000-K."""
        return f"{self.body}-{self.dv}"

    @property
    def formatted(self):
        """Formato con puntos y guion: 61.704.000-K."""
        return f"{self.body:,}".replace(",", ".") + f"-{self.dv}"


def verifier_digit(body):
    total, factor = 0, 2
    for digit in reversed(str(body)):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    remainder = 11 - total % 11
    return {11: "0", 10: "K"}.get(remainder, str(remainder))


def normalize_rut_text(value):
    return re.sub(r"[.\s-]", "", str(value or "")).upper()


def parse_rut(value):
    """Devuelve un `Rut` válido o lanza `InvalidRut`. Acepta puntos, guion y k minúscula."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 20 or not re.fullmatch(r"[0-9.\s-]*[0-9kK]", raw):
        raise InvalidRut("Formato de RUT inválido.")
    match = _RUT_PATTERN.match(normalize_rut_text(raw))
    if not match:
        raise InvalidRut("Formato de RUT inválido.")
    body = int(match.group(1))
    if body == 0:
        raise InvalidRut("Formato de RUT inválido.")
    if verifier_digit(body) != match.group(2):
        raise InvalidRut("El dígito verificador no corresponde.")
    return Rut(body, match.group(2))


def same_rut_text(left, right):
    """Compara dos RUT escritos en formatos distintos (sin validar dígito verificador)."""
    a, b = normalize_rut_text(left).lstrip("0"), normalize_rut_text(right).lstrip("0")
    return bool(a) and a == b
