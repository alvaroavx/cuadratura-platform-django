# ADR 0009: documentos tributarios del RCV como registro documental inmutable

Estado: aceptado como implementación local (2026-11-04). Migraciones `documents/0002_tax_document` y `documents/0003_tax_document_guard` aplicadas solo en una base desechable. La importación real queda pendiente de autorización.

## Contexto

Se pidió registrar los documentos tributarios de un contribuyente a partir de los CSV que se descargan a mano desde el Registro de Compras y Ventas (RCV) del SII: facturas afectas (33), exentas (34), notas de débito (56) y notas de crédito (61), de compras y de ventas. Sin interfaz, sin conexión al SII y sin Tupana. Debe ser repetible, idempotente y verificable contra los resúmenes del mismo RCV.

Lo que ya existía no sirve sin deformarlo:

- `ElectronicReceipt` modela boletas 39/41 **emitidas por Cuadratura**: exige una `Sale`, `created_by` y un ciclo de emisión (`PREPARED`, `SUBMITTING`, `ACCEPTED`…). Una factura de compra no tiene venta ni emisión.
- `Sale` es una operación con pagos y conciliación. Una venta sin `RECONCILED` aparece en `Excepciones` y **bloquea el cierre mensual**. Crear ventas desde el RCV alteraría cierres y libro.
- `ImportBatch`/`ImportRow` son el flujo web de previsualización y confirmación con archivo copiado a `media/` y usuario que carga. Un comando sin usuario no encaja y su clasificación está acoplada a ventas y pagos.

## Decisión

**1. Modelo nuevo `documents.TaxDocument`.** No es una segunda tabla de facturas: es el primer modelo de documento recibido o informado por el SII; `ElectronicReceipt` sigue siendo la boleta emitida. Guarda:

- organización propietaria, dirección (`PURCHASE`/`SALE`), tipo SII, RUT emisor y folio;
- RUT y razón social de la contraparte tal como los informa el SII, y el tipo de compra o venta («Del Giro»);
- fecha de emisión, período tributario (primer día del mes del archivo) y recepción en el SII (hora de Chile convertida a UTC);
- exento, neto, IVA (en compras, el recuperable), IVA no recuperable con su código, IVA de uso común y total;
- referencia informada (tipo y folio) y `referenced_document` cuando el original está registrado;
- formato, nombre y SHA-256 del archivo, línea y fecha de importación.

No se persisten `Nro` (es un correlativo del listado, no un identificador), acuse de recibo (cambia en el tiempo y produciría falsos conflictos), indicadores informativos (venta sin costo, servicio periódico, sucursal, número interno) ni datos de receptor extranjero.

**2. Componentes tributarios no modelados se rechazan, no se aplanan.** Si una fila trae valor en IVA retenido, activo fijo, impuesto sin derecho a crédito, tabacos, otros impuestos, comisiones de liquidación, zona franca, envases, pasajes u otra columna tributaria sin campo propio, la fila es inválida y el archivo completo queda bloqueado. Lo mismo ocurre con un documento con «Fecha Reclamo». Se valida `exento + neto + IVA (+ no recuperable + uso común) = total`. Una factura 34 no puede traer neto ni IVA. PostgreSQL repite estas reglas con `CHECK`.

**3. Contraparte sin modelo propio.** Se guarda RUT normalizado (`12345678-9`, módulo 11 de `organizations/rut.py`) y razón social informada en cada documento. No se crea `TaxpayerOrganization` (sería un tenant) ni se enlaza por FK a otro tenant (cruzaría el aislamiento por organización sin aportar permisos ni datos). Como el RUT está normalizado, una futura tabla de contrapartes por organización puede poblarse desde los documentos existentes sin perder información.

**4. Clave natural:** única por `(organization, direction, document_type, issuer_tax_id, folio)`. En ventas el emisor es el contribuyente; en compras, el proveedor. No se usan `Nro` ni montos. Un documento existente idéntico es no-op; si difiere en cualquier campo persistido se informa como conflicto y se conserva lo registrado. No hay política de reemplazo.

**5. Notas de crédito y débito.** Se registran como documentos propios con montos sin signo, igual que en el SII. El efecto económico se deriva del tipo: 33, 34 y 56 suman, 61 resta (`economic_summary`). La factura original no se modifica, no se marca como anulada y no se elimina. Si la referencia apunta a un documento registrado del mismo contribuyente, dirección y emisor, se completa `referenced_document`; si no, la referencia queda guardada y se completa en una importación posterior. El RCV de compras no informa referencias, así que una NC de proveedor queda sin vínculo.

**6. Inmutabilidad en PostgreSQL.** El trigger `tax_document_guard` rechaza eliminar y modificar documentos. La única actualización permitida es completar `referenced_document_id` cuando está vacío. El admin es de solo lectura.

**7. Ejecución todo o nada.** El comando simula por defecto. Con `--apply` se niega si hay cualquier error bloqueante: archivo ilegible, encabezado desconocido, fila inválida, contribuyente inexistente, ambiguo, inactivo o archivado, o período cerrado (`MonthlyClose` `CLOSED`, igual que las otras capturas). Si no hay bloqueos, todo se persiste en una transacción con `select_for_update` sobre cada contribuyente. La clasificación se repite bajo el bloqueo; la restricción única es la última defensa. Conflictos y diferencias con el resumen no bloquean: se informan.

**8. Sin efectos financieros.** Importar no crea ventas, pagos, boletas, asientos ni movimientos, y no entra en el libro mensual ni en el cierre. Un documento del RCV no prueba pago.

## Consecuencias

- Un archivo con un tipo o componente no soportado no se puede importar hasta ampliar el modelo. Es deliberado: evita perder un impuesto.
- Una corrección del SII sobre un documento ya importado aparece como conflicto. Resolverla exige definir un flujo auditado.
- Los reportes existentes (libro, cierre, exportación de boletas) no muestran estos documentos. Los totales por tipo y el efecto neto están en `documents.rcv_import.economic_summary`, reutilizable por una vista futura.
- Formatos reconocidos: `sii-rcv-compra-registro-v1` (27 columnas), `sii-rcv-venta-v1` (43), y sus resúmenes (8 y 6). Otro estado de compras (pendiente, no incluir, reclamado) o un encabezado distinto se rechaza con mensaje. Agregar uno exige un formato nuevo y pruebas.

## Revisar si

- el SII cambia columnas o aparecen documentos con otros impuestos, receptor extranjero o reclamos que deban registrarse;
- se necesita mostrar estos documentos en la web (vista por organización con `authorized_organization`), conectar el libro de compras y ventas o identificar contrapartes entre organizaciones;
- se decide una política de corrección de documentos ya importados.

## Verificación (2026-11-04)

- `documents/test_rcv.py`: 27 pruebas con datos ficticios (parser, idempotencia, conflictos, NC pendiente y resuelta después, conciliación, bloqueo por organización o período, rollback, trigger y concurrencia con dos hilos).
- Simulación con los CSV reales de `data/` contra la base local sin migrar: el contribuyente existe y coincide, se reconocen los 5 documentos, los dos resúmenes cuadran y la NC se vincula con la factura que referencia.
- Importación de ensayo con `--apply` en un clúster PostgreSQL desechable: 5 creados, NC vinculada; la segunda ejecución no crea nada.
- No verificado: importación en la base local del usuario, QA o producción. Tampoco la semántica de «IVA uso Comun» ni de los indicadores ignorados con un contador; no hay casos en los archivos reales.
