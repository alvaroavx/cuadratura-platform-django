# AGENTS.md

## Propósito

Cuadratura es un backoffice Django multiempresa para ventas, pagos, boletas manuales, importación histórica, conciliación y cierres. No integra ni emite ante SII: el gateway actual falla de forma cerrada.

## Antes de trabajar

Lee `README.md`, `docs/README.md`, `docs/architecture.md`, `docs/security.md`, `docs/monthly-book.md` y los ADR. Verifica código y tests antes de cambiar comportamiento sensible.

## Directorios importantes

- `src/organizations/`: tenants, membresías y autorización por organización.
- `src/sales/`, `src/payments/`, `src/documents/`: operación financiera, DTE y conciliación. `src/documents/` también guarda los documentos del RCV del SII (`TaxDocument`, comando `importar_rcv`, ADR-0009); son inmutables por trigger y no crean ventas ni pagos.
- `src/imports/`: preview/commit de XLSX/CSV histórico y JSON `elemental-cuadratura-v1`, más archivo privado. El contrato está en `docs/contracts/`; las diferencias entre esquema y validación real, en `docs/architecture.md`.
- `src/reports/`: libro provisional, exportación de boletas, cierre, reapertura y vista de auditoría.
- `src/integrations/`: API HTTP v1 (`/api/v1/`), clientes técnicos con token y contrato genérico `cuadratura-operations-v1` (ADR-0005, guía `docs/api-v1.md`) con seguridad de dos factores, intentos fallidos y límites (ADR-0006, checklist `docs/seguridad-produccion.md`). La API crea lotes en preview; nunca confirma. No relajes estos controles sin un ADR.
- `src/audit/`: eventos append-only; no registrar referencias de pago ni secretos.
- `src/sii/`: contrato deshabilitado, no emisión real.

## Invariantes

- La organización procede del UUID de URL y la autorización servidor se resuelve con `authorized_organization`; no aceptar tenant desde formularios.
- Montos CLP son enteros; operaciones compuestas usan servicios transaccionales.
- Períodos cerrados bloquean captura; reapertura exige permiso, motivo y auditoría.
- No elimines ni actualices entidades que PostgreSQL protege como append-only.

## Validación y seguridad

Las pruebas requieren PostgreSQL y un rol con `CREATEDB`, o un clúster desechable. Ejecuta los comandos de `README.md`, que también registra la última validación local; revisa aislamiento multiempresa, idempotencia, auditoría e importaciones. Nunca agregues secretos, CAF, certificados o llaves privadas al repositorio, logs o fixtures.
