# ADR 0007: consulta de antecedentes de contribuyentes por RUT

Estado: aceptado como implementación local. El proveedor vigente es **Tupana** (desde el 2026-11-04) y reemplaza a SRE (ver historial).
- **Contrato:** verificado con llamadas reales el 2026-11-04.
- **Modelo:** ampliado con la migración aditiva `organizations/0003_taxpayer_public_data`, aplicada solo en la base desechable de pruebas.
- **Activación:** queda deshabilitado mientras no exista `TUPANA_API_KEY`.

## Contexto

Crear o corregir un contribuyente (`TaxpayerOrganization`, que en Cuadratura es también el tenant) exige escribir RUT, razón social y nombre de fantasía. Se pidió autocompletar desde una fuente pública por RUT y actualizar contribuyentes existentes con revisión humana, sin persistir nada sin confirmación y sin integrar el SII.

## Decisión

- **Adaptador** `organizations/contributor_info.py`: protocolo `ContributorInformationProvider.lookup(rut) -> LookupResult` con estados `found`, `not_found`, `unavailable`, `provider_limited` y `disabled`, y un `ContributorInfo` normalizado. Incluye `EconomicActivity` y `RegisteredAddress` como colecciones y no depende del JSON del proveedor. Implementaciones: `TupanaContributorInformationProvider`, `DisabledContributorInformationProvider` (por defecto) y `FakeContributorInformationProvider` (pruebas). Sigue el patrón de `sii/gateway.py`.
- **Proveedor Tupana:** `GET https://api.tupana.ai/v2/master-entities/?rut=NNNNNNNN-D` con `Authorization: Api-Key …`. URL fija en código, sin redirecciones, timeout configurable, respuesta de hasta 256 KiB y sin reintentos. Usa `urllib` de la biblioteca estándar: no hay dependencias nuevas y no se usa el SDK oficial. Detalle en [integración Tupana](../integrations/tupana.md).
- **Creación:** `POST /organizaciones/consultar-rut/` (`organization-tax-id-lookup`). Exige sesión, administrador de plataforma (el único que crea contribuyentes) y CSRF, y valida el RUT por módulo 11 antes de llamar al proveedor. Aplica un límite por usuario y minuto y no escribe en la base. Si el RUT ya existe no consulta al proveedor y entrega el enlace a la ficha si no está archivada. «Usar estos datos» copia los valores no vacíos al formulario y el guardado sigue el POST normal.
- **Edición («Actualizar desde Tupana»):**
  - `POST /organizaciones/<uuid>/consultar-rut/` usa el RUT **registrado** (nunca el del request) y devuelve la comparación y la versión del registro.
  - `POST /organizaciones/<uuid>/actualizar-desde-rut/` aplica solo los campos marcados «Sí». Exige `confirm=yes` y una fuente conocida, ignora vacíos, nunca acepta `tax_id` y valida con las reglas del modelo.
  - La concurrencia optimista compara `updated_at` dentro de `select_for_update`; sin migración.
- **Guardado normal de edición:** usa la misma concurrencia optimista cuando el formulario trae `version` (la plantilla siempre la envía); un POST sin `version` conserva la conducta anterior. Guarda solo los campos del formulario (`update_fields`), para no pisar columnas ajenas como las del archivado.
- **Mapeo al modelo:**
  - `name` va a `legal_name` y `email` a `public_email`.
  - `activities` se guardan en `TaxpayerActivity` y `addresses` en `TaxpayerAddress`, siempre que una persona las seleccione.
  - Tupana no entrega nombre de fantasía, que sigue siendo manual. El detalle está en «Modelo implementado».
- **Auditoría:**
  - `organization.created` agrega `prefilled_from` y el resumen de las colecciones creadas.
  - `organization.updated` registra `changed_fields`. En el flujo asistido agrega `source`, `fetched_at`, actividades agregadas o actualizadas, la cantidad de direcciones agregadas o actualizadas y lo marcado como no informado.
  - No se guarda la respuesta del proveedor ni hay tabla de historial de consultas.

## Contrato Tupana (verificado el 2026-11-04)

Detalle completo en [integración Tupana](../integrations/tupana.md). En resumen:
- **RUT:** se acepta con o sin puntos.
- **RUT inexistente:** responde **200** con `name` vacío e `id: null`, no 404.
- **Ids:** son cadenas opacas con prefijo.
- **Direcciones:** cada una trae `is_main_in_sii` (casa matriz) e `is_default` (preferencia de la plataforma Tupana, que se descarta).
- **Key inválida:** responde 403 con un error anidado, `{"error": {"type", "code", "message", "request_id", …}}`.
- **Actividades:** los nombres llegan truncados a 40 caracteres.
- **Tamaño:** una empresa grande puede informar cientos de direcciones (519 en una prueba, 138 KB).

## Modelo implementado (migración `0003_taxpayer_public_data`, aditiva)

- **`TaxpayerOrganization`:** suma `public_email` (editable a mano), `external_source`, `external_reference` (`ment_…`) y `external_synced_at`. Todos son opcionales y vacíos en los registros existentes.
- **`TaxpayerActivity`:**
  - campos: organización, `code` (de 4 a 6 dígitos, con check en la base), `name`, `source` (`MANUAL` o `TUPANA`), `last_reported_at` y `not_reported_since`;
  - única por organización y código.
- **`TaxpayerAddress`:**
  - campos: organización, `street`, `district` (comuna), `city`, `sii_branch_code`, `phone`, `is_main_in_sii`, `source`, `external_reference` (`addr_…`), `last_reported_at` y `not_reported_since`;
  - única por organización y referencia cuando hay referencia.
  - Sin referencia, se compara por calle y comuna normalizadas.
- **Reglas** (`organizations/public_data.py`):
  - solo se agrega o actualiza lo seleccionado;
  - un vacío de la fuente no borra;
  - nada se elimina;
  - lo de Tupana que deja de informarse queda con `not_reported_since`, y lo manual nunca se marca;
  - todo ocurre bajo el bloqueo del contribuyente y su versión.
- **No se guarda:**
  - `is_default`, que no es del dominio;
  - los ids de comuna y ciudad;
  - la región, que Tupana no entrega;
  - el tipo de entidad y el estado tributario, que tampoco entrega.
- **Sigue propuesto:** la unicidad por RUT normalizado (`tax_id_normalized`). Antes hay que auditar duplicados y RUT sin dígito verificador válido.

## Configuración

- `CONTRIBUTOR_INFO_PROVIDER`: `disabled` o `tupana`.
- `TUPANA_API_KEY`
- `CONTRIBUTOR_INFO_TIMEOUT_SECONDS`: 8 por defecto.
- `CONTRIBUTOR_INFO_RATE_LIMIT_PER_MINUTE`: 10 por defecto.

Si falta la key, el proveedor queda deshabilitado y los botones no aparecen.

## Alternativas

- **SRE** (`sre.cl`): primer proveedor, retirado (ver historial).
- **Scraping del SII o actores de scraping de terceros:** descartados.
- **Archivo público del SII importado localmente:** se mantiene como respaldo si Tupana deja de ser viable. Encaja como otro `ContributorInformationProvider`.

## Consecuencias y riesgos

- **Dependencia de un tercero:** los términos generales de Tupana (5 de diciembre de 2025) no ofrecen SLA ni tratan la API ni la redistribución de datos. El trabajo manual sigue siempre disponible.
- **Límite de consultas por proceso:** se basa en la caché local de Django, así que con N workers el tope efectivo es N × límite.
- **Timeout parcial:** el timeout de `urllib` se aplica a cada operación de socket, no al total de la solicitud.
- **RUT sin validación estricta en el formulario:** no se exige dígito verificador al guardar (conducta vigente; el RUT ficticio local `77.111.222-3` no lo cumple). La unicidad compara el texto tal como se escribió, y el RUT sigue siendo editable a mano en la edición: propuesta pendiente.
- **Valores enviados por el navegador:** los valores que aplica el flujo asistido vienen del navegador. Solo los puede enviar un administrador de plataforma, que ya puede editar esos campos a mano, y el servidor los valida igual que el formulario.

## Revisión

- Aplicar la migración `0003` en la base local o en QA cuando el dueño lo decida (`manage.py migrate organizations`).
- Reconsiderar si Tupana cambia el contrato, empieza a cobrar o limita este endpoint.

## Historial

- **2026-11-03 — SRE (retirado).** Primer proveedor: `POST https://sre.cl/api/company_info` con el token en el cuerpo.
  - Con llamadas reales se observó lo siguiente: el token público compartido estaba agotado, con `403 "… ha consumido todas las consultas disponibles"`; el token inexistente respondía `403 "Usuario No registrado"`.
  - Nunca se vio una respuesta exitosa.
  - SRE no publicaba términos de uso comercial y había retirado la dirección y la comuna por la Ley 21.719.
- **2026-11-04 (tarde) — Contrato verificado y modelo ampliado.** Con la key de desarrollo se resolvieron las inconsistencias de la documentación. Se agregaron `public_email`, la procedencia externa, `TaxpayerActivity` y `TaxpayerAddress`.
- **2026-11-04 — Tupana.** El dueño decidió no pagar un plan de SRE para un solo usuario y migrar a Tupana, cuya consulta por RUT es gratuita.
  - Se eliminaron el cliente, la configuración (`SRE_API_TOKEN`) y las pruebas de SRE.
  - Se conservaron la validación de RUT, el adaptador, los endpoints, la interfaz, la comparación, la concurrencia, la auditoría y sus pruebas.

## Verificación contra el código

`organizations/test_contributor_lookup.py` cubre:
- validación del RUT;
- contrato de Tupana con mocks basados en la OpenAPI: éxito, incompleto, inexistente, 401/403, 429, 5xx, timeout, esquema inesperado, otro RUT y logs sin secretos;
- colecciones: creación con lo aceptado, comparación (nuevo, diferente, sin cambios, no informado), selección individual, preservación y nada eliminado;
- regresión de la migración sobre datos existentes;
- permisos, CSRF y límite de consultas;
- que la consulta no escribe en la base;
- creación asistida y duplicados;
- actualización parcial, campos seleccionados y descartados, y vacíos que no borran;
- confirmación, RUT no reemplazable y concurrencia en el flujo asistido y en el guardado normal.
