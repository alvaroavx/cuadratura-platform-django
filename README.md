# Cuadratura

Backoffice web autónomo y multiempresa para registrar ventas, pagos, boletas manuales, conciliación y cierres mensuales. Es un sistema independiente: no comparte repositorio, base de datos, autenticación ni modelos con Plataforma Elemental.

## Guía humana

### Qué es, para qué sirve y quiénes lo usan

**Cuadratura** es un backoffice financiero multiempresa para ordenar el trabajo mensual de una organización: registrar operaciones, contrastar ventas y pagos, importar historia y cerrar un período con evidencia. Está dirigido a administradores de plataforma y a usuarios miembros de cada contribuyente con permisos definidos. Es independiente de Plataforma Elemental: no es su API, extensión ni repositorio compartido.

### Qué contiene y cómo trabaja

Contiene un monolito Django server-rendered, PostgreSQL, aplicaciones de cuentas, organizaciones, ventas, pagos, documentos, reportes, importaciones, auditoría e integraciones (API v1). Cada operación se ejecuta en el contexto de una organización y el servidor vuelve a comprobar autorización. Las importaciones (XLSX/CSV del libro de caja y JSON del contrato `elemental-cuadratura-v1`) se previsualizan antes de confirmarse; los cierres preservan versión, archivo y SHA-256. No hay SPA, worker ni emisión SII real. Otros sistemas pueden enviar lotes de operaciones por la API v1 (`/api/v1/`, token por cliente); esos lotes quedan en previsualización hasta que una persona los confirma, igual que un archivo.

### Funcionalidades y tareas que resuelve

- Administrar usuarios, organizaciones, membresías y roles.
- Consultar antecedentes públicos por RUT en Tupana al crear un contribuyente (vista previa y confirmación) y al editarlo («Actualizar desde Tupana», con comparación y selección campo a campo). Es opcional y está deshabilitado sin API key; ver [ADR 0007](docs/adr/0007-consulta-contribuyentes-rut.md).
- Registrar ventas, pagos y documentos DTE 39/41 configurables.
- Importar información histórica mediante XLSX o CSV con previsualización y confirmación atómica.
- Importar operaciones y pagos confirmados desde Plataforma Elemental mediante un archivo JSON `elemental-cuadratura-v1`, con idempotencia por identificador externo y detección de conflictos.
- Recibir lotes de operaciones de cualquier sistema autorizado por la API v1 genérica (`cuadratura-operations-v1`), con token por cliente, alcance por organización e idempotencia por `batch_id`. Ver [guía de la API](docs/api-v1.md).
- Conciliar operaciones, construir el libro mensual y exportar un XLSX provisional.
- Revisar, copiar y descargar en XLSX las boletas aceptadas del período (**Exportar a Excel**).
- Consultar los últimos 200 eventos de auditoría de la organización (roles de cierre).
- Cerrar y reabrir períodos con trazabilidad y auditoría append-only.
- Registrar facturas y notas de crédito/débito de compras y ventas desde los CSV del Registro de Compras y Ventas del SII con el comando interno `importar_rcv` (simula por defecto, idempotente, concilia con los resúmenes). No crea ventas ni pagos. Ver [importación RCV](docs/importacion-rcv-sii.md) y [ADR 0009](docs/adr/0009-documentos-tributarios-rcv.md).
- Mantener un límite explícito para SII: el gateway falla de forma cerrada y no emite documentos reales.

### Trazabilidad humana

- **Solicitante:** no hay una solicitud ni una persona solicitante verificable en la historia o documentación versionada.
- **Desarrollo:** el primer commit disponible fue creado por **Álvaro Vargas Quezada**; el repositorio no contiene una atribución formal de todos los aportes posteriores.
- **Cuándo:** el primer registro Git disponible es del **31 de julio de 2026**. La fecha describe el inicio observable del repositorio, no una certificación de inicio del producto.

### Qué puede mejorar y oportunidades

Las oportunidades centrales son validar el libro mensual con la contraparte contable, sustituir la exportación XLSX provisional por un contrato aprobado y decidir si la integración SII será realmente necesaria. Para operar en producción faltan decisiones/evidencia sobre hosting, TLS, respaldos, retención, monitoreo y recuperación. La evolución debe conservar aislamiento por organización, inmutabilidad financiera y auditoría; vea [arquitectura](docs/architecture.md), [seguridad](docs/security.md), [libro mensual](docs/monthly-book.md) y los ADR.

## Estado del primer corte

Incluye Django server-rendered, usuario propio, organizaciones y membresías con roles fijos, captura manual transaccional, importación histórica XLSX/CSV con previsualización y commit atómico, importación de archivos JSON `elemental-cuadratura-v1`, DTE 39/41 configurables, conciliación básica, cierres versionados con archivo y SHA-256, reapertura auditada, exportación XLSX reimportable, exportación interactiva de boletas aceptadas y un gateway SII que falla de forma cerrada. **No existe emisión SII real.** Elemental puede entregar el mes como archivo, que una persona con rol de importación carga en la pantalla Importación, o enviarlo por la API v1. En ambos casos una persona confirma el lote en Cuadratura.

También está montado el admin de Django en `/admin/`, que registra todos los modelos (incluidos ventas, pagos, boletas, cierres y auditoría) para usuarios `is_staff`. El admin no aplica el aislamiento por organización ni los servicios transaccionales; solo las restricciones y triggers de PostgreSQL siguen vigentes. Comandos propios (`integrations`):
- `create_api_client` y `rotate_api_client_token`: emiten el token de un cliente API y lo muestran una sola vez.
- `set_api_client_public_key`: registra la llave pública de firma.
- `link_api_client_organization`: habilita o deshabilita una organización para el cliente.
- `purge_api_security_records`: aplica la retención.

Comando propio de `documents`: `importar_rcv`, que importa documentos tributarios desde CSV del RCV del SII ([guía](docs/importacion-rcv-sii.md)).

Todos auditan sus cambios.

La exportación es provisional. La planilla de referencia se contrastó solo en estructura: sus columnas históricas coinciden con correlativo, operación, documento, emisor, clasificación, fecha, detalle y montos. No hay confirmación de cuál hoja constituye el mes patrón corregido por la contadora. Las planillas de trabajo se mantienen fuera de Git: `.gitignore` excluye `local/`, `docs/*.xlsx`, `docs/*.csv`, `data/*.xlsx`, `data/*.csv` y `data/*.png` (en la copia local revisada la referencia está en `data/`, no en `docs/`).

## Requisitos

- Python 3.12 a 3.14 (`pyproject.toml` exige `>=3.12,<3.15`). Producción usará 3.12, la del servidor (D-013 del workspace); verificado con la suite el 2026-11-20
- PostgreSQL 18 (`compose.yaml` usa `postgres:18.4`)
- Dependencias de ejecución en `requirements.txt`: Django 5.2.x, psycopg 3, openpyxl, WhiteNoise y cryptography (firma Ed25519 de la API). `ruff` y `coverage` figuran solo como extra `dev` en `pyproject.toml` y no se instalan con `requirements.txt`.
- Poppler (`pdftotext`) para leer facturas PDF. En Debian y Ubuntu viene en `poppler-utils`.
- opcionalmente Docker Compose para levantar solo PostgreSQL

Variables de entorno leídas por `config/settings.py`: `DJANGO_SECRET_KEY` (obligatoria fuera de DEBUG), `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `POSTGRES_DB/USER/PASSWORD/HOST/PORT`, más las de endurecimiento HTTP, de la API (`API_*`) y de la consulta de contribuyentes por RUT (`CONTRIBUTOR_INFO_*`, `TUPANA_API_KEY`; deshabilitada por defecto, ver [ADR 0007](docs/adr/0007-consulta-contribuyentes-rut.md) y [integración Tupana](docs/integrations/tupana.md)), listadas en `.env.example` y en el [checklist de producción](docs/seguridad-produccion.md). `SII_GATEWAY` se lee en settings, pero ningún código lo consume todavía.

## Instalación local

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
docker compose up -d db
set -a; source .env; set +a
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

Si PostgreSQL ya existe, cree la base/usuario indicados en `.env` y omita Compose. El host inspeccionado no tenía Docker; `compose.yaml` queda como opción reproducible.

La primera organización, incluida Espacio Cultural y Deportivo Elementos SpA, se crea en **Nuevo contribuyente** después de iniciar sesión como `PLATFORM_ADMIN`/superusuario. No hay tenants en migraciones ni seeders.

### Iniciar el servidor local

Después de la instalación inicial, con PostgreSQL en marcha, copia y pega este comando en Bash desde la raíz del repositorio.

```bash
(set -a && source ./.env && set +a && .venv/bin/python manage.py runserver 127.0.0.1:8000)
```

Abre [Cuadratura en tu navegador](http://127.0.0.1:8000/). Para detener el servidor, presiona `Ctrl+C`.

### Acceso local y cambio de contraseña

La pantalla `/ingresar/` usa el **nombre de usuario**, no el correo electrónico. Si el acceso falla, compruebe primero que PostgreSQL esté iniciado; en una instalación local administrada por systemd:

```bash
systemctl start postgresql@18-main.service
```

Para cambiar la contraseña de una cuenta existente sin escribirla en el historial de la terminal ni compartirla con terceros:

```bash
set -a; source .env; set +a
.venv/bin/python manage.py changepassword NOMBRE_DE_USUARIO
```

El comando solicita la nueva contraseña dos veces sin mostrarla en pantalla. No agregue contraseñas al repositorio, archivos de documentación, logs ni fixtures.

### Facturas PDF y resumen tributario

En el período del contribuyente, selecciona **Cargar factura PDF**, sube una factura afecta (33) o exenta (34) y presiona **Leer PDF**. Verifica los RUT, folio, fecha y montos antes de **Guardar factura**. Cuadratura deduce si es compra o venta a partir de los RUT revisados y detecta duplicados con el RCV. Los permisos son los mismos de importación del libro de caja.

La extracción funciona con PDF que contienen texto. Para escaneos sin texto, completa el formulario desde el original. La lectura es local y se conservan los datos revisados, el nombre y el hash SHA-256, sin archivar el PDF original.

En **Resultado documentado e impuestos** puedes ver los ingresos, compras y su diferencia, además del IVA de ventas y compras. Ingresa la tasa PPM del mes para estimar el importe sobre los ingresos documentados sin IVA. El PPM y la retención de honorarios registrados se muestran desde el cierre vigente. El resultado cubre los documentos del período y la estimación no sustituye al F29. [Criterio del panel](docs/monthly-book.md#resultado-documentado-e-impuestos)

### Boletas de honorarios PDF

En el mes de emisión, selecciona **Cargar boleta de honorarios**, sube el PDF y revisa folio, fecha, RUT, bruto, retención y líquido antes de guardar. Si el contribuyente es emisor se registra un ingreso. Si es receptor se registra un egreso. Para habilitar esta función en una instalación existente, aplica las migraciones con `.venv/bin/python manage.py migrate` tras cargar `.env`.

El panel distingue el bruto de ingresos y egresos, la retención de emitidas y recibidas, y el líquido de cada boleta. Los honorarios se consideran en el resultado documentado sin incorporarse al cálculo de IVA ni al estimador de PPM de primera categoría. Al igual que en las facturas, el PDF se usa para extraer los datos y no se archiva.

## Pruebas

Las pruebas requieren PostgreSQL real; SQLite no es compatible ni está configurado. `manage.py test` crea una base `test_<POSTGRES_DB>`, por lo que el usuario de `.env` necesita permiso `CREATEDB`; si no lo tiene, use un clúster PostgreSQL 18 desechable y apunte `POSTGRES_HOST/PORT/USER` a él:

```bash
set -a; source .env; set +a
.venv/bin/python manage.py test -v 2
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
git diff --check
```

Estado verificado el 2026-10-12 (API v1 con seguridad ADR 0006): `check` y `makemigrations --check --dry-run` pasan, y la suite de 77 pruebas pasa completa. La prueba `test_competing_previews_confirm_only_once` se actualizó al comportamiento idempotente vigente desde `c1c7426`.

## Aplicaciones

- `accounts`: `User` UUID y rol global de plataforma.
- `organizations`: contribuyentes, membresías e invitaciones.
- `sales`, `payments`, `documents`: captura, dinero, DTE 39/41 y estados. `documents.TaxDocument` guarda los documentos tributarios informados en el RCV (ADR 0009).
- `payments`: además de `Payment`, `payments.services.register_historical_receipt_payment` vincula un pago histórico a una boleta aceptada; no tiene vista, comando ni prueba en el repositorio.
- `reports`: exportación XLSX provisional, exportación interactiva de boletas, cierre, reapertura y vista de auditoría. El panel mensual vive en `sales`.
- `imports`: parsers XLSX/CSV versionados, receptor JSON `elemental-cuadratura-v1` (`imports/elemental_v1.py`), lotes privados, previsualización y confirmación.
- `sii`: contrato, adaptador deshabilitado, fake de pruebas y modelos `SiiProfile`/`SiiSubmissionAttempt` migrados pero sin uso en flujos.
- `audit`: eventos append-only.
- `integrations`: API HTTP v1 (`/api/v1/`), modelos `ApiClient` y `ApiClientOrganization`, contrato `cuadratura-operations-v1` y comandos de alta y rotación de token. Ver [ADR 0005](docs/adr/0005-api-v1.md) y la [guía](docs/api-v1.md).

Consulte [arquitectura](docs/architecture.md), [seguridad multiempresa](docs/security.md), [criterio del libro](docs/monthly-book.md), [dirección visual](docs/ui-direction.md), [contrato Elemental v1](docs/contracts/), [API v1](docs/api-v1.md) y los ADR en `docs/adr/`.
