# Importación de documentos tributarios desde el RCV del SII

Estado: **implementado** en local (comando `importar_rcv`, ADR [0009](adr/0009-documentos-tributarios-rcv.md)). **No verificado** en QA ni producción.

## Objetivo

Registrar en Cuadratura las facturas, notas de crédito y notas de débito que el SII informa en el Registro de Compras y Ventas (RCV), a partir de los CSV descargados a mano desde el sitio del SII. Es un mecanismo interno: no tiene interfaz web, no se conecta al SII, no usa Tupana y no requiere servicios pagados. Es repetible e idempotente.

Importar un documento **no** crea ventas, pagos, boletas, asientos ni movimientos bancarios, ni lo incorpora al libro mensual o al cierre. Una factura de compra en el RCV no implica que esté pagada.

## Archivos soportados

Se leen solo archivos de la carpeta indicada (sin subcarpetas) cuyo nombre calza exactamente con:

| Archivo | Formato | Uso |
|---|---|---|
| `RCV_COMPRA_REGISTRO_<RUT-DV>_<AAAAMM>.csv` | `sii-rcv-compra-registro-v1`, 27 columnas | Crea documentos de compra |
| `RCV_VENTA_<RUT-DV>_<AAAAMM>.csv` | `sii-rcv-venta-v1`, 43 columnas | Crea documentos de venta |
| `RCV_RESUMEN_COMPRA_REGISTRO_<RUT>_<AAAAMM>.csv` | resumen, 8 columnas | Solo concilia |
| `RCV_RESUMEN_VENTA_<RUT>_<AAAAMM>.csv` | resumen, 6 columnas | Solo concilia |

Todo otro archivo se ignora y solo se cuenta. Un `RCV_*` con otro nombre (p. ej. compras pendientes o reclamadas) se informa como no soportado y no se procesa.

Formato esperado: UTF-8 (con o sin BOM), separador `;`, montos CLP enteros sin separador de miles, fechas `DD/MM/AAAA` y recepción `DD/MM/AAAA HH:MM:SS` (hora de Chile). Se tolera el `;` sobrante al final de las filas sin desplazar columnas. El encabezado debe coincidir exactamente; si el SII cambia una columna, el archivo se rechaza indicando cuál. Límite: 5 MiB y 20.000 filas por archivo.

`data/*.csv` está excluido en `.gitignore`. Los CSV contienen información tributaria real: no se copian al repositorio ni a fixtures. El importador nunca modifica, mueve ni elimina los archivos.

## Campos persistidos

Modelo `documents.TaxDocument`. Detalle y justificación en el ADR 0009.

| RCV compras | RCV ventas | Campo |
|---|---|---|
| Tipo Doc | Tipo Doc | `document_type` (33, 34, 56, 61) |
| Tipo Compra | Tipo Venta | `transaction_type` |
| RUT Proveedor | (contribuyente) | `issuer_tax_id` |
| RUT Proveedor / Razon Social | Rut cliente / Razon Social | `counterparty_tax_id`, `counterparty_name` |
| Folio, Fecha Docto, Fecha Recepcion | ídem | `folio`, `issue_date`, `sii_received_at` |
| Monto Exento, Monto Neto | ídem | `exempt_amount`, `net_amount` |
| Monto IVA Recuperable | Monto IVA | `vat_amount` |
| Monto Iva No Recuperable, Codigo IVA No Rec., IVA uso Comun | — | `vat_non_recoverable_amount`, `vat_non_recoverable_code`, `vat_common_use_amount` |
| Monto Total | Monto total | `total_amount` |
| — | Tipo/Folio Docto. Referencia | `reference_document_type`, `reference_folio`, `referenced_document` |

Además: organización, dirección, período (del nombre del archivo), formato, nombre y SHA-256 del archivo, línea y fecha de importación.

Las demás columnas tributarias (IVA retenido, activo fijo, impuesto sin derecho a crédito, tabacos, otros impuestos, comisiones, zona franca, envases, pasajes, no facturable) deben venir vacías, en `0` o `-`. Si traen valor, la fila se rechaza en vez de perder ese impuesto. Un documento con «Fecha Reclamo» también se rechaza. Se ignoran `Nro`, acuse de recibo, indicadores, sucursal, número interno y receptor extranjero.

## Requisitos

- Migraciones `documents.0002_tax_document` y `documents.0003_tax_document_guard` aplicadas. Sin ellas, la simulación funciona como si no hubiera documentos registrados y `--apply` se niega.
- El contribuyente titular ya debe existir en Cuadratura, activo y no archivado. El importador no lo crea.

## Comandos

```bash
set -a; source .env; set +a
.venv/bin/python manage.py importar_rcv --path data/            # simulación (por defecto)
.venv/bin/python manage.py importar_rcv --path data/ --dry-run  # simulación explícita
.venv/bin/python manage.py importar_rcv --path data/ --apply    # registra
```

Opciones:

- `--file NOMBRE` (repetible): procesa solo esos archivos dentro de `--path`. Solo nombres, sin rutas.
- `--period AAAAMM` (repetible) y `--direction compras|ventas`: filtran.
- `--rut RUT`: identifica al contribuyente. Es obligatorio si el nombre no trae un RUT con dígito verificador válido; si lo trae y no coincide, el archivo se bloquea.

El comando termina con error (código distinto de cero) si hay errores bloqueantes, también en simulación.

## Selección del contribuyente y del período

1. El RUT se toma del nombre del detalle y se valida por módulo 11.
2. Se busca el único contribuyente con ese RUT, sin importar puntos o guion. Si no existe, hay más de uno, está inactivo o archivado, el archivo se bloquea. Nunca se asigna por nombre.
3. El período es el `AAAAMM` del nombre. Cada archivo se procesa por separado; compras y ventas pueden ser de meses distintos. Si el período tiene un cierre mensual vigente (`CLOSED`), el archivo se bloquea.
4. Un resumen se asocia al detalle de la misma dirección, RUT y período. Sin detalle, solo se informa.

## Idempotencia y conflictos

Clave natural: contribuyente, dirección, tipo SII, RUT emisor y folio (en ventas el emisor es el contribuyente; en compras, el proveedor). PostgreSQL la impone con una restricción única.

- Documento nuevo: se crea.
- Existente idéntico: no se toca («ya registrado»).
- Existente distinto: **conflicto**. Se muestran los campos que difieren y se conserva lo registrado.

Los documentos son inmutables en la base (trigger): no se pueden modificar ni eliminar. La única actualización permitida es completar la referencia de una nota cuyo original llegó después.

`--apply` es todo o nada: si cualquier archivo seleccionado tiene un error bloqueante no se escribe nada; si no los hay, todo se registra en una transacción con bloqueo del contribuyente, de modo que dos ejecuciones simultáneas no duplican. Conflictos y diferencias con el resumen se informan pero no bloquean. Cada ejecución con efecto deja un evento `tax_documents.rcv_imported` con conteos y hash del archivo, sin datos de contrapartes.

## Notas de crédito

Una nota de crédito (61) se registra como documento propio con montos positivos, como en el SII, y su efecto económico es negativo. Si informa «Tipo/Folio Docto. Referencia», se vincula al documento del mismo contribuyente, dirección y emisor con ese tipo y folio. La factura original **no** se modifica, no se marca como anulada y no se elimina. Si el original todavía no está registrado, la referencia se guarda y se completa en una ejecución posterior. El RCV de compras no trae referencias, así que una NC de proveedor queda sin vínculo.

## Conciliación con resúmenes

Por tipo de documento se comparan cantidad, exento, neto, IVA y total (en compras también IVA uso común y no recuperable) entre el resumen y el detalle. El informe muestra los totales brutos por tipo y, por separado, facturas y notas de débito, notas de crédito y el efecto neto (las NC restan). Una diferencia se informa; nunca se inventan documentos. Un resumen ausente no impide importar el detalle.

## Errores frecuentes

| Mensaje | Qué hacer |
|---|---|
| No existe un contribuyente con RUT … | Crear el contribuyente en la web o revisar el archivo |
| El nombre no trae un RUT válido | Renombrar el archivo como lo entrega el SII o usar `--rut` |
| Encabezado no reconocido … | El SII cambió el formato: no se importa hasta soportarlo |
| Componentes no soportados … | El documento tiene impuestos sin campo propio; ver ADR 0009 |
| El período … está cerrado | Reabrir el período con motivo, si corresponde |
| El archivo no está en UTF-8 | Volver a descargarlo; no convertirlo a mano sin revisar |

## Ejemplo

Con un detalle de ventas de octubre con dos facturas 33 y una NC 61 que referencia a la primera:

```text
== RCV_VENTA_76123456-0_202610.csv
   Ventas · período 2026-10 · formato sii-rcv-venta-v1
   Documentos: 3 · nuevos 3 · ya registrados 0 · conflictos 0 · inválidos 0
   L4    tipo 61  folio 1  15/10/2026  cliente 77222333-1  total $119.000  nuevo  ref. tipo 33 folio 15 (se vincula)
   Facturas y notas de débito: $357.000 · notas de crédito: $119.000 · efecto neto: total $238.000 …
   Conciliación con RCV_RESUMEN_VENTA_76123456_202610.csv: cuadra (cantidad y montos por tipo).
```

El listado muestra RUT, folio, fecha y total, pero no razones sociales.

## Limitaciones

- Solo tipos 33, 34, 56 y 61, y solo RCV de compras «registro» y de ventas.
- No hay vista web; los documentos solo se ven en el admin (solo lectura) o por consulta.
- No hay política para corregir un documento ya importado: aparece como conflicto.
- La semántica de «IVA uso Comun» y de los indicadores ignorados no se contrastó con un contador ni con casos reales.

## Cómo agregar un nuevo formato o tipo

1. Agregar un `RcvFormat` en `src/documents/rcv.py` con patrón de nombre y encabezado exactos, y decidir qué columnas se persisten y cuáles deben venir vacías.
2. Si se necesitan campos nuevos, migración aditiva en `documents` y ajuste de `COMPARED_FIELDS` en `src/documents/rcv_import.py` y del trigger si cambia la regla de inmutabilidad.
3. Pruebas con datos ficticios en `src/documents/test_rcv.py`. Nunca usar archivos reales como fixtures.
4. Actualizar este documento y el ADR 0009.

La lógica vive en funciones reutilizables (`build_plan`, `apply_plan`, `economic_summary`), así que una futura carga desde la web o una conexión con el SII puede usarlas sin duplicar validación. Eso no está desarrollado.
