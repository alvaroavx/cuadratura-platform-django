# Releases y recuperación

**Estado:** implementado y probado en local el 2026-11-20. Cuadratura todavía no
tiene servidor productivo; se publicará en el mismo Droplet que Plataforma
Elemental (D-013 del workspace), con este esquema desde el primer día.

`scripts/release/release.sh` es una **copia idéntica** del script de
`avx-django-plataformaelemental`. El diseño completo, la recuperación y la prueba
local están en `avx-django-plataformaelemental/docs/operacion/RELEASES.md` y en
`docs/06-operacion/releases-y-recuperacion.md` del workspace. Si se cambia el
script, copiarlo al otro repositorio.

## Lo propio de Cuadratura

| Ítem | Valor |
|---|---|
| Raíz | `/srv/apps/cuadratura/` (`repo.git`, `releases/`, `current`, `shared/`) |
| Configuración | `deploy/release.conf.example` → `shared/release.conf` |
| Hooks | `deploy/hooks.sh`: exige `DJANGO_DEBUG` apagado, `DJANGO_SECRET_KEY`, `pdftotext` y `gunicorn` |
| Compartido | `media/` (archivos importados y documentos) |
| Servicio | `deploy/systemd/cuadratura.service.example`: Gunicorn, 2 workers, `127.0.0.1:8010` |
| Nginx | `deploy/nginx/cuadratura.conf.example` (dominio por definir; estáticos por WhiteNoise) |
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

- **Agregar `gunicorn` a `requirements.txt`.** Hoy no está y el hook lo bloquea.
- Instalar `poppler-utils` en el servidor.
- Crear base y rol propios en PostgreSQL. El rol no comparte base con Elemental.
- Definir dominio y certificado.
- Ejecutar `docs/seguridad-produccion.md`.

## Comandos

```bash
R=/srv/apps/cuadratura
bash <(git --git-dir=$R/repo.git show <sha>:scripts/release/release.sh) -c $R/shared/release.conf deploy <sha>
bash $R/current/scripts/release/release.sh -c $R/shared/release.conf list
bash $R/current/scripts/release/release.sh -c $R/shared/release.conf rollback
bash $R/current/scripts/release/release.sh -c $R/shared/release.conf restore-db <dump> --confirm <POSTGRES_DB>
```
