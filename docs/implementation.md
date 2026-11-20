# Guía de implementación

Cuadratura se ejecuta como una aplicación Django WSGI. Los formularios y las
plantillas construyen la interfaz. JavaScript añade selección de períodos,
revisión de PDF, consulta de contribuyentes y exportación interactiva. Los
parsers, consultas y escrituras se ejecutan durante la solicitud o desde un
comando de administración.

## Estructura del repositorio

`src/` es la raíz de importación de Python. Las apps conservan sus nombres
de módulo y los identificadores de Django, por ejemplo `sales` y `sales.Sale`.
El `manage.py` de la raíz agrega `src/` a la ruta de importación. El runner de
[pruebas](../src/config/testing.py) descubre la suite en `src/` cuando se ejecuta
`manage.py test` sin etiquetas. Las etiquetas como `sales` siguen funcionando.

[`settings.py`](../src/config/settings.py) distingue las fuentes (`SOURCE_DIR`)
de la raíz del repositorio o release (`BASE_DIR`). Las plantillas y los estáticos
se leen de las fuentes. Los archivos privados, el XLSX de cierre, la salida de
`collectstatic` y la carpeta por defecto de `importar_rcv` se resuelven desde
la raíz de la release.

| Ruta | Responsabilidad y punto de entrada |
|---|---|
| [`src/config/`](../src/config/) y [`manage.py`](../manage.py) | Settings por entorno, montaje de rutas y entrada WSGI o de comandos. `src/config/urls.py` incorpora login, logout, admin, módulos web y API. |
| [`src/accounts/`](../src/accounts/) | Usuario y formulario de ingreso. Django gestiona la sesión y valida la contraseña. |
| [`src/organizations/`](../src/organizations/) | Selección del contribuyente, administración, archivado, permisos y consulta opcional a Tupana. `access.py` centraliza el alcance de la interfaz. |
| [`src/sales/`](../src/sales/) y [`src/payments/`](../src/payments/) | Panel por mes, captura manual, cálculo de IVA, conciliación y registro de pagos. `src/sales/urls.py` también monta los formularios de carga documental. |
| [`src/documents/`](../src/documents/) | Boletas operativas, facturas y notas, honorarios y comando `importar_rcv`. Separa extracción, formularios y servicios de registro. |
| [`src/imports/`](../src/imports/) | Lectores de planilla y contratos JSON, previsualización, clasificación y confirmación. Aquí se transforma el lote en operaciones locales. |
| [`src/integrations/`](../src/integrations/) | Rutas HTTP y esquema de contrato, autenticación de clientes, protección contra repetición y comandos de gestión de credenciales. |
| [`src/reports/`](../src/reports/) | Consultas y XLSX de boletas, libro provisional, cierre, reapertura y pantalla de auditoría. |
| [`src/audit/`](../src/audit/) y [`src/sii/`](../src/sii/) | Escritura de eventos de auditoría y adaptadores de emisión. El gateway SII de ejecución falla de forma cerrada. |
| [`src/templates/`](../src/templates/) y [`src/static/`](../src/static/) | Plantillas por capacidad, componentes comunes en `src/templates/core/`, CSS, JavaScript y marca. WhiteNoise sirve los estáticos recopilados por `collectstatic`. |
| [`deploy/`](../deploy/) y [`scripts/release/`](../scripts/release/) | Ejemplos de Nginx y systemd, hooks y script de releases con respaldo y rollback. Véase la [guía de releases](releases.md). |

Los modelos y migraciones viven en la app dueña. Las pruebas pueden ejercitar varias apps a través de un mismo flujo. Las vistas y formularios
pertenecen al módulo que expone el flujo. Los comandos propios se organizan
bajo `management/commands/`. [Modelo de datos](data-model.md)

## Clases y funciones que conectan las capas

| Punto de entrada | Colaboración en código |
|---|---|
| Login y acceso web | `LoginView` usa [`LoginForm`](../src/accounts/forms.py). Las vistas con sesión llaman a [`authorized_organization`](../src/organizations/access.py) y acotan las consultas subordinadas al contribuyente. |
| Captura y conciliación | [`sales.views`](../src/sales/views.py) valida formularios y delega en [`register_manual_operation` y `register_manual_receipt`](../src/sales/services.py). `TaxPreview` transporta el cálculo de neto e IVA. Los servicios escriben la operación compuesta y registran eventos dentro de la transacción. |
| Vista mensual | [`period_dashboard`](../src/sales/views.py) compone ventas, estados y resumen documental. [`sales.periods`](../src/sales/periods.py) construye la navegación entre meses. Los servicios documentales consultan facturas/notas y honorarios del período. |
| Importación desde la web | [`imports.views`](../src/imports/views.py) llama a [`create_preview_batch` y `commit_batch`](../src/imports/services.py). El lector de [planillas](../src/imports/parser.py) o [Elemental](../src/imports/elemental_v1.py) produce filas de trabajo. El lote y sus filas retienen datos para revisión y trazabilidad. |
| Recepción API | [`api_client_required`](../src/integrations/api.py) usa [`authenticate_request`](../src/integrations/security.py). Después se comprueba el vínculo con el contribuyente. `create_api_preview_batch` valida el contrato [genérico v1](../src/imports/operations_v1.py) y reutiliza la clasificación y persistencia de importaciones. |
| Facturas y honorarios PDF | [`upload_document_pdf`](../src/documents/views.py) permite leer y confirmar. Los módulos [de factura](../src/documents/invoice_pdf.py) y [de honorarios](../src/documents/honorarium_pdf.py) interpretan texto local. Los servicios [de factura](../src/documents/invoice_services.py) y [de honorarios](../src/documents/honorarium_services.py) bloquean el contribuyente y comprueban el mes de emisión revisado antes de persistir. |
| RCV por consola | [`Command`](../src/documents/management/commands/importar_rcv.py) construye un [`ImportPlan`](../src/documents/rcv_import.py) compuesto por archivos y documentos. Con confirmación explícita ejecuta `apply_plan`, que reclasifica bajo bloqueo e inserta los documentos. |
| Exportación y cierre | [`reports.views`](../src/reports/views.py) llama a [`generate_workbook`, `close_month` o `reopen_month`](../src/reports/services.py). `close_month` bloquea el contribuyente, revisa excepciones y guarda versión, archivo y hash. `reopen_month` cambia el estado de la versión y registra el motivo. |
| Auditoría y emisión | [`record_event`](../src/audit/services.py) es el punto común de escritura. [`SiiGateway`](../src/sii/gateway.py) es un protocolo con adaptador deshabilitado y fake para pruebas. La boleta manual registra un documento conocido. |

## Transacciones, archivos y evidencia

Las transacciones del ORM agrupan escrituras del negocio y sus eventos. El
almacenamiento de archivos queda fuera de la transacción de PostgreSQL. El
preview de importación conserva un original privado y el cierre guarda un
XLSX bajo `MEDIA_ROOT`. La recuperación de una instalación necesita el
respaldo de la base y de esos archivos. El PDF usado para extracción se procesa
temporalmente y la confirmación conserva los datos revisados y su procedencia.

Las pruebas se distribuyen entre `tests.py`, módulos `test_*.py` y
`src/imports/tests/`. Para modificar un flujo, ubica su entrada en la tabla,
luego revisa restricciones y migraciones del modelo y las pruebas de ese
módulo. El [README](../README.md#pruebas) registra el resultado local. La
configuración efectiva del proxy, TLS, respaldos y restauración se comprueba
por separado en el entorno de operación.
