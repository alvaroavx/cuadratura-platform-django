# ADR 0008: eliminar un contribuyente es archivarlo

Estado: aceptado (2026-11-03). Decisor: Álvaro.

## Contexto

Se pidió poder eliminar un contribuyente desde `organizaciones/<id>/editar/`, con
doble confirmación y solo para administradores.

El borrado físico no es posible sin romper invariantes vigentes:

- Todas las relaciones hacia `TaxpayerOrganization` usan `on_delete=PROTECT`:
  membresías, invitaciones, ventas, pagos, boletas, cierres, lotes de importación,
  vínculos de API, perfil SII y auditoría.
- Todo contribuyente tiene al menos un `AuditEvent` (`organization.created`). La
  auditoría es append-only: el trigger `audit_no_delete`
  (`audit/migrations/0002_critical_append_only.py`) rechaza borrarla. Ventas, pagos,
  boletas y cierres tienen triggers equivalentes.

Un borrado real exigiría relajar la auditoría append-only y perder la trazabilidad.

## Decisión

**1. «Eliminar» significa archivar.** `TaxpayerOrganization` gana `archived_at`,
`archived_by` y `archive_reason` (migración `organizations/0002_organization_archive`).
Archivar:
- fija esos tres campos y pone `is_active=False`, en una transacción con
  `select_for_update`;
- registra el evento `organization.archived` con el motivo en `metadata`.

No se borra ni se modifica ningún dato operativo.

**2. Un archivado es invisible para operar.**
- `authorized_organization` responde 404 para un contribuyente archivado, también a
  los administradores de plataforma. Esto bloquea todas sus vistas.
- `scoped_organizations` lo excluye de las listas.
- La API v1 (`integrations/api.py` y `integrations/services.py`) exige además
  `archived_at IS NULL`. Es una defensa en profundidad, porque `is_active=False` ya lo
  rechazaba.

**3. Permiso y doble confirmación.**
- Solo un administrador de plataforma (superusuario o `PLATFORM_ADMIN`), igual que la
  edición.
- **Primer paso:** «Eliminar contribuyente…» en la zona de peligro de la edición.
- **Segundo paso:** una página que explica las consecuencias y exige el motivo y el RUT
  escrito. Se compara sin puntos ni espacios.

**4. Archivo de solo lectura.** `organizaciones/archivo/`, enlazado como
«Ver archivo (n)» en Mis organizaciones, solo para administradores de plataforma.
Muestra por cada contribuyente:
- razón social y RUT;
- fecha, autor y motivo de la eliminación;
- conteo de ventas, pagos, boletas y cierres conservados.

No ofrece edición ni acceso a su espacio de trabajo.

**5. Recuperación: pendiente.** No hay acción de restaurar en la aplicación. Hoy solo
se puede hacer en `/admin/`: vaciar `archived_at` y reactivar. Eso no deja un evento de
auditoría, así que un flujo «Recuperar» con auditoría queda como trabajo futuro.

## Consecuencias

- **RUT ocupado:** el RUT de un archivado sigue ocupado (`tax_id` es único). No se puede
  crear otro contribuyente con el mismo RUT; hay que recuperar el anterior.
- **Vínculos y membresías:** los vínculos de API y las membresías no se desactivan. Dejan
  de tener efecto porque el contribuyente no es accesible, y vuelven a funcionar si se
  recupera.
- **Comandos de API:** los comandos de gestión (`create_api_client`,
  `link_api_client_organization`) no filtran archivados. Pueden vincular uno, pero la API
  lo rechazará.
- **Retiro de datos personales:** un retiro real de datos, por ejemplo por una solicitud
  de eliminación de datos personales, sigue sin estar resuelto. Requeriría otro ADR que
  revise la auditoría append-only.

## Verificación (2026-11-03)

- `organizations/tests.py` (`OrganizationArchiveTests`) cubre:
  - el botón visible para el administrador de plataforma;
  - el rechazo a un administrador de organización;
  - un RUT incorrecto, que no archiva;
  - el archivado con auditoría, la ocultación y el 404 para administrador y miembro;
  - el archivo de solo lectura restringido.
- Migración aplicada en la base local. `manage.py check` sin problemas.
- No verificado en QA ni en producción.
