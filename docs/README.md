# Documentación IA First — Cuadratura

Última revisión documental el 2026-11-20 contra el código de `4393d3e`. Las rutas, servicios, modelos y migraciones enlazados permiten verificar cada flujo.

| Objetivo | Documentos |
|---|---|
| Entender producto y reglas financieras | [README raíz](../README.md) → [arquitectura](architecture.md) → [libro mensual](monthly-book.md) |
| Ubicar carpetas, puntos de entrada y clases | [guía de implementación](implementation.md) |
| Entender relaciones, claves y triggers | [modelo de datos](data-model.md) |
| Revisar aislamiento y datos sensibles | [seguridad](security.md) → ADR de `docs/adr/` |
| Modificar importaciones | ADR-0004, `src/imports/`, sus parser/services/tests y el contrato de archivo privado |
| Preparar QA o producción | [checklist de seguridad](seguridad-produccion.md), [ADR-0006](adr/0006-seguridad-api.md), [releases y recuperación](releases.md) |
| Conectar un sistema por API | [guía API v1](api-v1.md), [ADR-0005](adr/0005-api-v1.md), `src/integrations/`, `src/imports/operations_v1.py`, `src/integrations/tests.py` |
| Recibir datos de Plataforma Elemental por archivo | [arquitectura § Recepción Elemental v1](architecture.md#recepción-elemental-v1), [contrato y ejemplo](contracts/), `src/imports/elemental_v1.py`, `src/imports/tests/test_elemental_v1.py` |
| Modificar cierre o exportación | [libro mensual](monthly-book.md), `src/reports/`, `src/static/js/receipt-export.js`, `src/audit/` y pruebas de reportes |
| Revisar pagos de alumnos vinculados y honorarios pendientes | [importación de Finanzas Elementos](finanzas-elementos-import.md) |
| Importar documentos tributarios del RCV del SII (comando `importar_rcv`) | [importación RCV](importacion-rcv-sii.md), [ADR 0009](adr/0009-documentos-tributarios-rcv.md), `src/documents/rcv.py`, `src/documents/rcv_import.py`, `src/documents/test_rcv.py` |
| Consulta de contribuyentes por RUT (Tupana) | [ADR 0007](adr/0007-consulta-contribuyentes-rut.md), [integración Tupana](integrations/tupana.md), `src/organizations/contributor_info.py`, `src/organizations/rut.py`, `src/organizations/test_contributor_lookup.py` |
| Evaluar integraciones futuras (SII, DTE, bancos, recaudación) | [hoja de ruta Tupana](roadmap/tupana-integrations.md) |
| Preparar ejecución local | [README raíz](../README.md), `.env.example`, `compose.yaml`, `src/config/settings.py` |

## Arquitectura verificable

Monolito modular Django server-rendered con PostgreSQL. Las vistas obtienen organización por UUID de ruta y delegan operaciones financieras a servicios transaccionales. Los modelos y migraciones conservan restricciones de dinero, estados, idempotencia e inmutabilidad; la autorización se vuelve a comprobar en servidor. No hay SPA, workers ni emisión SII real. Hay una API HTTP v1 genérica (`/api/v1/`, token por cliente) que crea lotes en previsualización; una persona los confirma. Elemental puede usarla o seguir entregando un archivo JSON.

## Cobertura documental existente

- `architecture.md`: decisiones de dominio, estados, transacciones, roles, límites del primer corte y recepción Elemental v1, incluida su diferencia con el esquema.
- `security.md`: aislamiento tenant, auditoría, límites de carga, admin de Django y secretos.
- `monthly-book.md`: contrato y límites de exportación/cierre mensual y exportación interactiva de boletas.
- `finanzas-elementos-import.md`: carga puntual de pagos de alumnos y qué parte quedó versionada.
- `importacion-rcv-sii.md`: comando interno que registra facturas y notas de compras y ventas desde CSV del RCV, con idempotencia, notas de crédito y conciliación con resúmenes.
- `ui-direction.md`: tokens y criterios visuales (coinciden con `src/static/css/app.css`).
- `contracts/`: esquema JSON y ejemplo ficticio de `elemental-cuadratura-v1` (copia idéntica a la del workspace padre). El esquema genérico de la API vive en `src/integrations/contracts/` porque la API lo sirve.
- `api-v1.md`: cómo dar de alta un cliente, firmar requests, enviar lotes, leer el acuse y los errores.
- `releases.md`: deploy por releases con respaldo, rollback y restauración de base (`scripts/release/release.sh`).
- `seguridad-produccion.md`: checklist con evidencia para QA, producción y la futura integración SII.
- `adr/`: decisiones sobre monolito, SII, integraciones, importación versionada, seguridad de la API, eliminación de contribuyentes como archivado (ADR-0008) y documentos tributarios del RCV como registro inmutable (ADR-0009); cada ADR termina con una verificación contra el código.

## Límites y precauciones

Infraestructura productiva, TLS, backups, retención, monitoreo y aceptación contable final no están certificados por el repositorio. Los documentos no autorizan migraciones, despliegues ni acceso a datos reales. Cualquier cambio funcional debe actualizar esta documentación y la evidencia específica afectada.

Validación local del 2026-11-20 con Python 3.14.4 y PostgreSQL 18.6 desechable. Pasaron las 173 pruebas, `manage.py check` y `makemigrations --check --dry-run`. [Comandos y alcance](../README.md#pruebas)
