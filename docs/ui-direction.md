# Dirección visual: el orden tiene una forma

Identidad derivada del nombre: equilibrio, precisión, cierre y conciliación. Sobriedad
institucional sin imitar un portal estatal. Definida por la mesa BPO (artista y diseñador)
en noviembre de 2026; el login es la iteración 1 y el interior la iteración 2.

## Tokens (`src/static/css/app.css`, `:root`)

| Token | Valor | Uso |
| --- | --- | --- |
| `--structural` | `#0f3d4a` (petróleo) | Barra superior, títulos, ítem activo del menú |
| `--petroleo-hondo` | `#0a2c36` | Fondo profundo del panel del login |
| `--action` | `#156677` | Botones primarios y enlaces (6,0:1 sobre papel) |
| `--text` / `--muted` | `#17272b` / `#586d73` | Texto principal (14:1) y secundario (≥4,6:1 sobre papel y neutros) |
| `--background` / `--papel` | `#f6f4f1` | Fondo cálido de toda la aplicación |
| `--surface` | `#fff` | Tarjetas, tablas y formularios |
| `--border` | `#d6d8d3` | Bordes decorativos; los campos usan `#82969b` (3,1:1) |
| `--cobre` / `--cobre-claro` | `#a8552a` / `#e0a77d` | Solo identidad: línea bajo la barra superior, línea y cuadre del login, cuadre de estados vacíos |
| `--accent` | `#d95016` | **Solo foco.** Sobre petróleo el foco es blanco (el naranjo da 2,86:1) |

Los colores semánticos (`--success`, `--warning`, `--danger`, `--info`) no cambian. El cobre
nunca se usa en botones, errores, indicadores, tablas ni navegación: se confundiría con el
rojo de error y perdería su valor.

## Marca

Wordmark tipográfico «Cuadratura» (600, `letter-spacing: .02em`), sin el logo PNG, que
queda solo como favicon hasta que se rediseñe el símbolo. El motivo geométrico (retícula de
cuadrados con un único cuadre en cobre) aparece solo en el login y en los estados vacíos.

## Tipografía y números

Pila `Inter, system-ui…`; Inter no se aloja todavía (pendiente). Títulos 650 con
`letter-spacing: -.01em`. Montos, RUT y conteos usan `tabular-nums lining-nums`; los
indicadores muestran el «$» atenuado y más pequeño que la cifra.

## Patrones

- Contexto persistente: contribuyente, RUT y período. La ruta de navegación no repite
  elementos y el nombre del contribuyente enlaza al resumen del período.
- Menú lateral sin iconos; cada pantalla marca un único ítem como actual.
- Acciones irreversibles o de alto impacto (cierre, reapertura, eliminar contribuyente)
  muestran «Qué ocurrirá» antes de confirmar, y el botón nombra el objeto («Cerrar septiembre 2026»).
- Mensajes de error con `role="alert"`; el resto con `role="status"`.
- Tablas anchas: región enfocable con nombre, indicio «Desliza…» y primera columna fija en
  móvil (≤560px).
- Controles de al menos 44 px, texto base de 16 px.

Esto implementa una base accesible, pero no declara conformidad WCAG formal sin una
auditoría con tecnología asistiva.
