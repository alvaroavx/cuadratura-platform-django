# Releases y recuperación

**Estado**. Primer despliegue manual validado el 2026-11-20 en el servidor
Elemental. El ingreso está disponible en https://cuadratura.avx.cl/ingresar/.
La base partió vacía. CI/CD y la habilitación de datos reales siguen pendientes.

`scripts/release/release.sh` es una **copia idéntica** del script de
`plataformaelemental-platform-django`. El diseño completo, la recuperación y la prueba
local están en `plataformaelemental-platform-django/docs/operacion/RELEASES.md` y en
`docs/06-operacion/releases-y-recuperacion.md` del workspace. Si se cambia el
script, copiarlo al otro repositorio.

## Lo propio de Cuadratura

| Ítem | Valor |
|---|---|
| Raíz | `/srv/cuadratura/` (`repo.git`, `releases/`, `current`, `shared/`) |
| Configuración | `deploy/release.conf.example` → `shared/release.conf` |
| Hooks | `deploy/hooks.sh`: exige `DJANGO_DEBUG` apagado, `DJANGO_SECRET_KEY`, `pdftotext` y `gunicorn` |
| Compartido | `media/` (archivos importados y documentos) |
| Servicio | `deploy/systemd/cuadratura.service.example`: Gunicorn, 2 workers, `127.0.0.1:8010` |
| Nginx | `deploy/nginx/cuadratura.conf.example` (`cuadratura.avx.cl`; estáticos por WhiteNoise) |
| Respaldos | `/var/backups/cuadratura/cuadratura-<release>.dump` |
| Salud | `/ingresar/` (no hay endpoint de salud) |
| Python | 3.12 en el servidor (`pyproject.toml` acepta 3.12 a 3.14) |

## Arranque con la estructura src

La unidad systemd incorpora `current/src` al path de Gunicorn y ejecuta
`config.wsgi:application` desde `current`. Si una instalación de prueba usaba
la unidad anterior, actualiza la unidad con el ejemplo versionado y vuelve a
cargarla en systemd. El script de release sigue invocando `manage.py` desde
la raíz de cada release. `collectstatic` escribe en `staticfiles/` de la
release y `media/` sigue siendo un enlace al directorio compartido.

## Antes del primer deploy

- Agregar `gunicorn` a `requirements.txt` antes de automatizar; el primer deploy lo instala desde el requisito adicional en `shared/`.
- Instalar `poppler-utils` en el servidor.
- Crear base y rol propios en PostgreSQL. El rol no comparte base con Elemental.
- Preparar DNS y certificado para `cuadratura.avx.cl`. Validar HTTPS y la ruta `/ingresar/` tras configurar Nginx.
- Ejecutar `docs/seguridad-produccion.md`.

## Primer despliegue manual en el servidor Elemental

- Espejo `/srv/cuadratura/repo.git` clonado desde GitHub. Release activa
  `20261120T212850Z-4393d3e96e9b`, correspondiente a `main` publicada.
- Python 3.12 y PostgreSQL 16. Migraciones aplicadas sobre una base separada
  con rol propio sin superusuario y copia local antes de migrar.
- Unidad `cuadratura.service` habilitada, con usuario de sistema `cuadratura` y
  Gunicorn en `127.0.0.1:8010`. El ingreso devolvió HTTP 200. Poppler quedó instalado.
- `shared/release.conf` apunta a `requirements-deploy.txt` para instalar Gunicorn
  y a un hook que da lectura y ejecución al grupo de servicio en cada release.
  El código y el entorno virtual siguen bajo propiedad de root. El servicio
  puede escribir los archivos persistentes en `shared/media/`.
- El primer arranque falló por permisos de recorrido. El segundo despliegue
  corrigió los permisos y validó el arranque. Las advertencias de Django
  quedaron en HSTS sin subdominios ni preload. La redirección HTTP a HTTPS se aplica en Nginx.
- La unidad instalada corresponde a la estructura de la revisión publicada.
  Al desplegar una revisión que coloca la aplicación en `src/`, aplicar también
  ese cambio de la unidad. La suite completa en PostgreSQL 16 queda pendiente.
- Antes de admitir datos reales, completar el checklist de seguridad y retomar
  los respaldos cifrados externos con restauración probada.

## Publicación HTTPS

- El DNS de `cuadratura.avx.cl` apunta al servidor Elemental. Nginx usa
  `/etc/nginx/sites-available/cuadratura`, habilitado en `sites-enabled`.
  El bloque redirige HTTP a HTTPS y reenvía a Gunicorn en `127.0.0.1:8010`.
- El certificado propio de Let's Encrypt vence el 2027-02-18. Certbot usa
  webroot en `/var/www/letsencrypt/cuadratura`. El temporizador `certbot.timer`
  renueva el certificado y el hook `50-cuadratura-nginx` valida y recarga
  Nginx cuando se renueva este certificado. Instalar el
  [hook versionado](../deploy/nginx/50-cuadratura-nginx.example) con modo 755
  en `/etc/letsencrypt/renewal-hooks/deploy/50-cuadratura-nginx`.
  La renovación simulada con `--run-deploy-hooks` pasó.
- Nginx restringe `/admin/` y `/api/v1/organizations/` hasta configurar
  el acceso administrativo y la integración. La pantalla de ingreso y la
  redirección devolvieron HTTP 200 y 301 respectivamente desde fuera del Droplet.
- Para emitir el primer certificado en otro servidor, habilitar primero solo
  el bloque HTTP con la ruta ACME y el resto cerrado. Ejecutar
  `certbot certonly --webroot -w /var/www/letsencrypt/cuadratura --cert-name cuadratura.avx.cl -d cuadratura.avx.cl`
  y luego habilitar el bloque TLS. Validar con `nginx -t` antes de recargar.

## Comandos

```bash
R=/srv/cuadratura
bash <(git --git-dir=$R/repo.git show <sha>:scripts/release/release.sh) -c $R/shared/release.conf deploy <sha>
bash $R/current/scripts/release/release.sh -c $R/shared/release.conf list
bash $R/current/scripts/release/release.sh -c $R/shared/release.conf rollback
bash $R/current/scripts/release/release.sh -c $R/shared/release.conf restore-db <dump> --confirm <POSTGRES_DB>
```
