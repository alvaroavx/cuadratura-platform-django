# Hooks de Cuadratura para scripts/release/release.sh.
# release.sh los carga desde la release nueva, con ENV_FILE ya cargado,
# RELEASE_PATH definido y la función `manage` disponible.

pre_checks() {
  [[ "${DJANGO_DEBUG:-false}" =~ ^(0|false|no|off)$ ]] || die "DJANGO_DEBUG debe estar apagado en producción."
  [[ -n "${DJANGO_SECRET_KEY:-}" ]] || die "Falta DJANGO_SECRET_KEY."
  # Las facturas PDF se leen con pdftotext (poppler-utils).
  command -v pdftotext >/dev/null || die "Falta pdftotext: instalar poppler-utils."
  [[ -x "$RELEASE_PATH/.venv/bin/gunicorn" ]] || die "gunicorn no está en requirements.txt; el servicio no podría arrancar."
}

post_collectstatic() {
  chmod 755 "$RELEASE_PATH"
}
