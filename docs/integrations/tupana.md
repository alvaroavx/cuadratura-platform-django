# Integración Tupana: consulta de contribuyentes por RUT

- **Contrato verificado** con llamadas reales el **2026-11-04**: cinco consultas, una key de desarrollo y RUT de empresas públicas.
- **Documentación revisada** el 2026-11-04.
- Decisión y alternativas: [ADR 0007](../adr/0007-consulta-contribuyentes-rut.md).
- Integraciones futuras: [hoja de ruta](../roadmap/tupana-integrations.md).

| Aspecto | Estado |
|---|---|
| Adaptador, endpoints, creación y edición asistidas, actividades, direcciones y correo | **Implementado** |
| Pruebas automáticas con fixtures anonimizadas con la forma real | **Validado** (suite local) |
| Contrato de `master-entities` | **Validado** con llamadas reales (detalle abajo) |
| Migración `organizations/0003_taxpayer_public_data` | **Implementado**; aplicada solo en la base desechable de pruebas |
| Uso en QA o producción | **No verificado** |

## Qué hace

Cuadratura consulta la nómina de empresas de Tupana para precargar o actualizar los datos de un contribuyente a partir de su RUT:

| Dato | Destino |
|---|---|
| Razón social | `legal_name` |
| Correo de contacto | `public_email` |
| Actividades económicas | `TaxpayerActivity` |
| Direcciones | `TaxpayerAddress` |

Una persona revisa y confirma todo antes de guardar. La consulta no escribe en la base y el trabajo manual siempre sigue disponible.

## Fuentes oficiales

- Portal: https://developers.tupana.ai/ · Empresas: https://developers.tupana.ai/companies · Precios: https://developers.tupana.ai/pricing
- OpenAPI v2: https://docs.tupana.ai/api-reference/openapi-v2.json · Documentación: https://docs.tupana.ai/
- Términos generales: https://www.tupana.ai/terms (actualizados al 5 de diciembre de 2025)

## Endpoint y autenticación

```
GET https://api.tupana.ai/v2/master-entities/?rut=61704000-K
Authorization: Api-Key <TUPANA_API_KEY>
Accept: application/json
```

Implementación: `src/organizations/contributor_info.py`, `TupanaContributorInformationProvider`.
- Usa `urllib` sin redirecciones, con timeout y una respuesta de hasta 1 MiB.
- No hace reintentos.
- La key viaja solo en la cabecera: nunca en la URL, el navegador ni los logs.

## Contrato verificado (2026-11-04)

Llamadas realizadas:
1. Codelco, `61704000-K`.
2. Codelco con puntos.
3. Un RUT con dígito verificador válido pero inexistente.
4. Una key inválida.
5. BancoEstado, `97030000-7`.

| Pregunta | Documentación | Realidad observada |
|---|---|---|
| ¿RUT con o sin puntos? | OpenAPI: sin puntos. Página: con puntos | **Ambos funcionan** y devuelven lo mismo. Cuadratura envía sin puntos |
| ¿RUT inexistente? | OpenAPI: 404. Página: «responde vacío» | **200** con `{"id": null, "opaque_id": null, "name": "", "email": "", "addresses": [], "activities": [], "tax_id": "<rut>"}`. Tardó unos 3 s (consulta al SII) |
| ¿Tipo de los ids? | Esquema: entero. Ejemplo: cadena | **Cadenas opacas con prefijo**: `ment_…`, `addr_…`, `acti_…`, `dist_…`, `city_…`. `opaque_id` no aparece en las respuestas con datos |
| ¿Casa matriz? | No documentado | Cada dirección trae **`is_main_in_sii`** (booleano) e **`is_default`**. BancoEstado: 2 de 519 marcadas como casa matriz; Codelco: 0 de 24 |
| ¿Error de autenticación? | `{"error": "...", "message": "..."}` | **403** con `{"error": {"type", "code", "message", "doc_url", "request_id"}}` |
| ¿Límite de tasa? | No documentado | Sin cabeceras de límite. No se probó un 429 |
| ¿Correo? | Nullable | `null` en las dos empresas consultadas |
| ¿Nombres de actividad? | — | **Truncados a 40 caracteres** (como el SII) |
| ¿Tamaño? | — | Codelco: 7,5 KB. BancoEstado: 138 KB (519 direcciones) |

### Mapeo

| Campo Tupana | Normalizado | Persistencia en Cuadratura |
|---|---|---|
| `name` | `legal_name` | `TaxpayerOrganization.legal_name` (si se acepta) |
| `email` | `public_email` (validado) | `TaxpayerOrganization.public_email` (si se acepta) |
| `id` (`ment_…`) | `provider_reference` | `TaxpayerOrganization.external_reference` |
| `tax_id` | Se compara con el RUT pedido | No se guarda: el RUT es el identificador y nunca lo reemplaza la consulta |
| `activities[].code` y `.name` | `EconomicActivity` (código de 4 a 6 dígitos, sin duplicados, máximo 50) | `TaxpayerActivity` (única por organización y código) |
| `addresses[].address`, `district.name`, `district.city.name`, `sii_branch_code`, `phone`, `is_main_in_sii`, `id` | `RegisteredAddress` (máximo 1000; se informa si se truncó) | `TaxpayerAddress` (`external_reference` = `addr_…`) |
| `addresses[].is_default` | Se descarta | **No se guarda**: es una preferencia de facturación de la plataforma Tupana, no un dato del contribuyente |
| `activities[].id`, `district.id`, `city.id` | Se descartan | No aportan al dominio |

Tupana no informa nombre de fantasía (sigue siendo manual), región, tipo de entidad, fechas ni estado tributario.

### Respuestas y estados

| Respuesta | Estado normalizado | Interfaz |
|---|---|---|
| 200 con `name` | `found` | Vista previa o comparación |
| 200 con `name` vacío (inexistente), cuerpo vacío, `{}`, `[]`; 404 | `not_found` | «No se encontraron antecedentes…» |
| 401 / 403 | `unavailable`; el log registra `credentials_rejected:<request_id>` | «Servicio no disponible» |
| 429 | `provider_limited` | «El proveedor está limitando las consultas» |
| 400, 5xx, 3xx, red, timeout, JSON inválido, esquema inesperado, otro RUT | `unavailable` | «Servicio no disponible» |
| Límite local por usuario superado | HTTP 429 `rate_limited` | «Espera un minuto» |

## Persistencia y reglas

- **Creación:**
  - la vista previa permite corregir la razón social, el nombre de fantasía y el correo, y marcar qué actividades y direcciones guardar;
  - las direcciones vienen marcadas por defecto cuando son 10 o menos; si son más, solo las de casa matriz;
  - «Usar estos datos» las incorpora al formulario y el guardado normal crea el contribuyente y las colecciones en una transacción, con `source = TUPANA`.
- **Edición («Actualizar desde Tupana»):**
  - la consulta usa el RUT registrado;
  - cada actividad o dirección se clasifica como nueva, diferente, sin cambios o no informada;
  - solo lo marcado se agrega o actualiza;
  - los valores vacíos de la fuente nunca borran (por ejemplo, un teléfono cargado a mano);
  - nada se elimina: lo que Tupana dejó de informar y había venido de Tupana queda con `not_reported_since`, y lo manual no se marca;
  - la concurrencia optimista usa `updated_at` bajo `select_for_update`.
- **Auditoría:** `organization.created` y `organization.updated` registran la fuente, `changed_fields` y el resumen de colecciones (códigos agregados o actualizados y cantidad de direcciones). No guardan la respuesta del proveedor.

## Configuración

| Variable | Valor | Notas |
|---|---|---|
| `CONTRIBUTOR_INFO_PROVIDER` | `tupana` | `disabled` por defecto |
| `TUPANA_API_KEY` | key de la consola de Tupana | Solo en `.env` o en el gestor de secretos; nunca en Git, logs, documentación ni pruebas |
| `CONTRIBUTOR_INFO_TIMEOUT_SECONDS` | `8` | Un RUT nuevo para Tupana tardó unos 3 s |
| `CONTRIBUTOR_INFO_RATE_LIMIT_PER_MINUTE` | `10` | Por usuario y proceso |

**Producción:** usar una key propia del ambiente. Este endpoint es gratuito y no activa servicios pagados.

### Aplicar la migración en local (cuando el usuario lo decida)

```bash
cd avx-django-cuadratura
set -a; source .env; set +a
.venv/bin/python manage.py migrate organizations --plan   # revisar: solo 0003_taxpayer_public_data
.venv/bin/python manage.py migrate organizations
```

- La migración es aditiva: cuatro columnas opcionales en `TaxpayerOrganization` y dos tablas nuevas.
- No modifica ni recalcula datos existentes.
- Reversión: `manage.py migrate organizations 0002_organization_archive`. **Ojo:** borra las dos tablas nuevas y los datos que tengan.

## Pruebas

- **Automáticas:** `src/organizations/test_contributor_lookup.py` usa un `FakeOpener` y fixtures ficticias con la forma real, incluido el 200 vacío y el 403 anidado. No dependen de la red ni de la key.
- **Manuales:** con `CONTRIBUTOR_INFO_PROVIDER=tupana`, consultar un RUT de empresa pública en «Nuevo contribuyente» y luego usar «Actualizar desde Tupana» en la ficha. No guardar respuestas crudas en el repositorio.

## Limitaciones conocidas

- Las empresas grandes informan cientos de direcciones. La interfaz las muestra con selección y desplazamiento, y se procesan hasta 1000.
- Los nombres de actividad llegan truncados a 40 caracteres.
- No se probó un 429 real ni la latencia sostenida. No hay SLA en los términos generales.
- Los valores que aplica el flujo asistido los envía el navegador de un administrador de plataforma, que ya puede editar esos datos a mano. El servidor los valida con las mismas reglas que el adaptador.
