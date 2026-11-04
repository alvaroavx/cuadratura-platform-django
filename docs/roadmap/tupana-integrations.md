# Hoja de ruta: integraciones posibles con Tupana Developers API

Fecha de consulta: **2026-11-04**. Este documento es una **propuesta**. No autoriza implementar, contratar ni conectar nada. La única integración implementada es la nómina de empresas ([integración Tupana](../integrations/tupana.md)).

Fuentes revisadas ese día:
- portal de desarrolladores: https://developers.tupana.ai/ , `/pricing`, `/companies`, `/bancos`, `/sandbox` y `/build`;
- documentación: https://docs.tupana.ai/ , la guía de webhooks y la de bases gratuitas;
- OpenAPI v2: https://docs.tupana.ai/api-reference/openapi-v2.json (43 rutas);
- términos generales: https://www.tupana.ai/terms (5 de diciembre de 2025).

Etiquetas:
- **Verificado en documentación:** aparece en la página u OpenAPI citada. No se probó.
- **No verificado:** no se encontró o hay inconsistencias.
- **Propuesto:** funcionalidad propia de Cuadratura.

## Precios publicados (Tupana Developers API, no la plataforma web)

Fuente: https://developers.tupana.ai/pricing y https://developers.tupana.ai/bancos, consultadas el 2026-11-04.
- Todos los valores son **netos, más IVA**.
- No hay cargo fijo ni mínimo: un mes sin empresas inscritas cuesta $0.
- La página usa como referencia UF = $40.845 (sin fecha).
- Los planes de la plataforma web de Tupana (Independiente 0,3 UF, Starter 1,0 UF, Pro 2,2 UF, Enterprise desde 4,5 UF) son **otro producto** y no aplican a la API.

| Producto | Precio | Unidad | Límites y condiciones publicados |
|---|---|---|---|
| Nómina de empresas del SII | Gratis | Con cualquier API key | No consume cupo; búsqueda por RUT o nombre |
| Lectura SII | 0,2 UF + IVA | Por empresa conectada, al mes | RCV y boletas de honorarios; requiere la clave SII de la empresa (vía Tupana Link) |
| Facturación (emisión DTE) | 0,5 UF + IVA | Por empresa inscrita, al mes | Hasta 100 documentos o 1.000 boletas de venta por empresa al mes; el mismo precio con el facturador de mercado o con el gratuito del SII (este último limitado a 3 referencias y 10 líneas por documento) |
| Lectura bancaria (cartola) | 0,35 UF + IVA | Por banco conectado, al mes | Cuentas, saldos y movimientos «sin límite» |
| Lectura bancaria con tarjeta de crédito | 0,45 UF + IVA | Por banco conectado, al mes | Publicado en `/bancos`; no aparece en `/pricing` |
| Recaudación (link de pago) | 2 % + IVA | Sobre el monto recaudado | Transbank y webhook; sin costo fijo |
| Sandbox | Gratis | Sin plazo | No llega al SII; folios `SANDBOX-{n}` |

Regla para Cuadratura: **estas tarifas no deben ser constantes de negocio en el código.** Si algún día se muestran costos, se leerán de configuración o del contrato vigente.

## 1. Nómina de empresas (consulta por RUT)

- **Estado:** implementado localmente (ADR 0007). Pendiente de verificación con key real.
- **Verificado en documentación:**
  - `GET /v2/master-entities/?rut=`, gratis;
  - entrega `name`, `tax_id`, `email`, `activities[]` y `addresses[]`.
- **No verificado:** el endpoint `GET /v2/sii-companies/?size=` (tramo de ventas, trabajadores, rubro y comuna, desde 2020). La guía de bases gratuitas lo menciona, pero **no está en la OpenAPI v2**.
- **Propuesto:** guardar actividades y direcciones (ADR 0007, «Modelo propuesto»). Evaluar `sii-companies` como segmentación opcional.

## 2. Lectura de información del SII

- **Estado:** investigación; no implementar.
- **Verificado en documentación (OpenAPI):**
  - `GET /book-summaries/`: resumen del Registro de Compras y Ventas por período y operación;
  - `GET /book-documents/`: detalle documento por documento con los montos tal como los informa el SII;
  - `POST /sync-requests/` y `GET /sync-status/`: sincronización asíncrona; frecuencias de 24, 12 o 3 h; la solicitud a demanda solo existe en el plan de 24 h;
  - `/honorary/...`: boletas de honorarios.
  - Los datos solo llegan para entidades con el scrape habilitado (`is_enabled`).
- **Autorización:**
  - la empresa conecta su clave SII en Tupana Link (`POST /connect-sessions/` con la capacidad `sii_data`);
  - Cuadratura guardaría solo el `connection_id`, nunca la clave.
- **Propuesto para Cuadratura:**
  - importar el RCV como fuente contrastable contra las ventas y boletas registradas;
  - detectar documentos faltantes o duplicados;
  - armar la base de cuentas por cobrar y por pagar;
  - apoyar el libro mensual y el cierre.
  - Requiere modelar «documento informado por SII» separado de los documentos propios: Cuadratura no interpreta un dato recibido como documento emitido (regla del workspace).
- **Riesgos:**
  - el SII publica datos preliminares;
  - dependencia del scraping de Tupana;
  - costo recurrente por empresa;
  - custodia de la autorización de terceros (Ley 21.719).

## 3. Emisión de DTE

- **Estado:** investigación; no implementar. El gateway SII de Cuadratura sigue fallando de forma cerrada (ADR 0002).
- **Verificado en documentación:**
  - `POST /documents/batch/`: hasta 500 documentos por llamada; con `Idempotency-Key` (≤ 256 caracteres, 24 h);
  - `GET /documents/{id}/states/`: historial de estados;
  - `/scheduled-documents/`;
  - folios: `/folio-requests/` y `/folio-queries/`, con límites de 3 timbrajes por hora o 20 al mes y 10 consultas por hora por empresa;
  - `/cessions/`.
  - Tipos 33, 34, 39, 41, 52, 56, 61 y 110 a 112.
  - Webhooks `document.issued`, `.rejected`, `.paid` y otros.
  - PDF por URL firmada.
  - La key necesita el permiso `document:create` y la empresa emisora, credenciales SII válidas.
- **No verificado:** cómo se descarga el XML, y la diferencia operativa real entre el facturador de mercado y el gratuito del SII más allá de los límites citados.
- **Propuesto:** emitir los DTE 39/41 que hoy se registran manualmente.
  - Exige confirmación humana, idempotencia por operación, estados auditables y conciliación con el SII.
  - Un DTE emitido no se borra: solo se anula con nota de crédito.

## 4. Lectura bancaria

- **Estado:** investigación; no implementar.
- **Verificado en documentación:**
  - 9 bancos: Chile, Santander, BancoEstado, BCI, Itaú, Scotiabank, Security, BICE y Falabella (Falabella solo con cuentas de empresa);
  - `GET /institutions/`, `/bank-accounts/`, `/movements/` (con contraparte, RUT y razón social) y `/movements/sync/` con cursor;
  - estados de cuenta;
  - sincronización cada hora y deduplicación de movimientos;
  - webhooks `connection.active` y `connection.requires_action`;
  - reautorización con `POST /connections/{id}/reauthorizations/`;
  - eventos consultables con retención de 400 días.
- **No verificado:** cuánto historial se obtiene al conectar.
- **Propuesto:**
  - conciliar abonos con pagos y boletas;
  - detectar movimientos sin respaldo;
  - mantener el estado de cuentas por cobrar y por pagar.
  - Encaja con el dominio de `payments` y con la conciliación existente; se importaría como lote en previsualización, igual que la API v1.

## 5. Recaudación

- **Estado:** investigación; no implementar.
- **Verificado en documentación:** link de pago con Transbank, webhooks `payment_request.paid` y `payment_request.expired`, y una guía en https://docs.tupana.ai/guias/link-de-pago.md.
- **No verificado:** los endpoints de recaudación **no aparecen en la OpenAPI v2** revisada.
- **Propuesto:** vincular cobro, pago y DTE. El sitio público no debe exponer operaciones financieras (regla del workspace).

## 6. Tupana Link, sandbox, MCP, SDK y webhooks

- **Tupana Link (Connect):**
  - el backend crea la sesión con `POST /connect-sessions/`, capacidades `invoicing`, `sii_data` o `banking`;
  - el `client_token` dura 30 minutos y sirve una sola vez;
  - la empresa autoriza en el widget;
  - la conexión se confirma con el webhook `connection.active`, no con el evento del widget;
  - Cuadratura guardaría solo `connection_id` y `company_id`.
- **Sandbox:** gratis, sin plazo, con la misma API. Nada llega al SII y se generan empresas y facturas de prueba.
- **Webhooks:**
  - firma HMAC-SHA256 en hexadecimal sobre el cuerpo crudo, cabecera `X-Pana-Signature`, con el `secret` definido al crear el webhook;
  - cabeceras `X-Pana-Event-Id` (para deduplicar), `X-Pana-Event-Type`, `X-Pana-Delivery` y `X-Pana-Timestamp`;
  - la ventana de validez del timestamp **no está documentada**;
  - reintentos a 1 min, 5 min, 15 min, 1 h y 6 h;
  - hay que responder 2xx en menos de 2 s.
- **SDK:** `tupana` para Python y Node.js (MIT), con reintentos, `Idempotency-Key` automática, errores tipados y verificación de webhooks. Cuadratura hoy no lo usa para evitar una dependencia por un solo GET; se reevaluará al integrar DTE o bancos.
- **MCP:** `https://mcp.tupana.ai/mcp` con OAuth 2.1. Herramientas para emitir y consultar documentos, buscar empresas por RUT y leer libros SII.
  - **No se conecta ni se incorporan agentes.**
  - Un agente futuro podría preparar documentos o conciliaciones, siempre con confirmación humana (la documentación de Tupana pide lo mismo) y con permisos mínimos.

## Secuencia sugerida (propuesta, no compromiso)

1. Autocompletado de contribuyentes: en curso, gratis.
2. Importación del RCV y conciliación de DTE (lectura SII).
3. Cuentas por cobrar y por pagar sobre lo anterior.
4. Conciliación bancaria (lectura bancaria).
5. Emisión de DTE.
6. Recaudación y automatización.

| Ámbito | Qué es |
|---|---|
| Capacidades del proveedor | Las de cada sección «Verificado en documentación» |
| Desarrollo propio de Cuadratura | Modelos de documento informado o movimiento, conciliación, interfaz de revisión, auditoría, idempotencia y webhooks firmados |
| Autorizaciones y credenciales | API key por ambiente; `connection_id` por empresa y capacidad; secreto de webhook; nunca claves SII ni bancarias en Cuadratura |
| Riesgos de dependencia | Sin SLA en los términos generales; scraping del SII aguas arriba; cambios de precio o contrato |
| Costos recurrentes | Por empresa, banco o monto (tabla de precios). Se decide por cliente, no se fija en el código |

### Decisiones pendientes

- Quién paga y cómo se traslada el costo a cada organización.
- Retención y tratamiento de datos de terceros, como contrapartes y movimientos bancarios.
- Si se adopta el SDK.
- Cómo se versiona un contrato interno «documento externo» antes de la primera integración pagada.
