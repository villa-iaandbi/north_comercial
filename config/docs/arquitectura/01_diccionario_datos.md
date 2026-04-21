# CAPÍTULO 1: Diccionario de Datos y Mapeo Físico (Oracle <-> Django ORM)

**A. REGLAS ESTRICTAS PARA MODELADO Y AGENTES DE IA**
1.  **Tipado de IDs:** Todos los campos identificadores (`ID_DOCUMENTO`, `ID_TERCERO`, `ID_ARTICULO`, etc.) son `VARCHAR2(8)` o `VARCHAR2(15)`. En Django deben ser `CharField`. No usar `AutoField`.
2.  **Generación de ID_DOCUMENTO:** No es autoincremental en Python. Se debe obtener ejecutando: `SELECT SEC_DOCUMENTO.NEXTVAL FROM DUAL;`. Este valor se asigna a `vID_DOC` y se usa como llave primaria/foránea en toda la transacción.
3.  **Regla de Sistema:** El proyecto NO es multitenant. El campo `ID_SISTEMA` debe insertarse siempre con el valor constante `'1'`.
4.  **Campos de Auditoría:** `FCH_REGISTRO` debe ser siempre `SYSDATE` (o `timezone.now()` en Python). `TERMINAL` se debe marcar como `'NORTH-LOCAL'`.

---

**B. TABLAS ORIGEN (Pedidos Pendientes de North)**

| Entidad | Tabla Oracle | Campo | Tipo | Comentario AI |
| :--- | :--- | :--- | :--- | :--- |
| **Cabecera** | `mv_pedidos_north` | `ID_VENDEDOR` | VARCHAR2(8) | NOT NULL |
| | | `ID_SISTEMA` | VARCHAR2(15) | NOT NULL (Usar '1') |
| | | `NUM_PEDIDO` | VARCHAR2(15) | NOT NULL |
| | | `ID_TERCERO` | VARCHAR2(8) | NOT NULL |
| | | `FORMA_PAGO` | VARCHAR2(10) | 'CONTADO', 'CREDITO', etc. |
| | | `PROCESADO` | VARCHAR2(1) | Filtro: Solo si es NULL |
| **Detalle** | `mv_pedido_items_north`| `ID_ARTICULO` | VARCHAR2(8) | NOT NULL |
| | | `CANTIDAD` | NUMBER(16,2) | Cantidad a facturar |
| | | `VLR_UNITARIO` | NUMBER(16,2) | Precio de lista |
| | | `DESCUENTO` | NUMBER(16,2) | Porcentaje aplicado |

---

**C. TABLAS DESTINO (Estructura de la Factura Oficial)**

**1. Cabecera Principal: `co_documentos`**
* `ID_DOCUMENTO` (PK): `VARCHAR2(8)` (Obtenido de `SEC_DOCUMENTO.NEXTVAL`).
* `ID_SISTEMA`: `VARCHAR2(8)` (Fijo '1').
* `ID_ANO`: `VARCHAR2(4)` (Año actual de la fecha del documento).
* `ID_TIPO_DOCUMENTO`: `VARCHAR2(8)` (Código de factura crédito/contado).
* `NUM_DOCUMENTO`: `VARCHAR2(15)` (Consecutivo legal de la resolución).
* `FCH_DOCUMENTO`: `DATE` (Fecha de la factura).
* `TOT_DOCUMENTO`: `NUMBER(16,2)` (Valor total de la CxC).
* `ESTADO_DOC`: `VARCHAR2(12)` (Default: 'GRABADO').

**2. Cartera e Impuestos: `ct_ventas`**
* `ID_DOCUMENTO` (FK): `VARCHAR2(8)`.
* `TOT_MERCANCIA`: `NUMBER(16,2)` (Suma de bases gravables).
* `TOT_IVA`: `NUMBER(16,2)` (Suma total de IVAs).
* `TOT_RETEFUENTE`: `NUMBER(16,2)` (Suma de retenciones).
* `VLR_VENTA`: `NUMBER(16,2)` (Neto a pagar).
* `TOT_DESCUENTO`: `NUMBER(16,2)` (Valor total descontado).

**3. Kardex / Inventario: `in_mov_inventarios`**
* `ID_DOCUMENTO` (FK): `VARCHAR2(8)`.
* `ID_ITEM`: `VARCHAR2(8)` (Contador incremental de línea).
* `ID_ARTICULO`: `VARCHAR2(8)`.
* `ID_BODEGA`: `VARCHAR2(8)` (Según parámetro de sistema).
* `ENTRA_SALE`: `VARCHAR2(1)` (Fijo 'S' para venta).
* `CANTIDAD`: `NUMBER(10,2)`.
* `VLR_UNITARIO`: `NUMBER(16,2)`.
* `VLR_IVA`: `NUMBER(16,2)` (IVA calculado por esta línea).
* `EXISTENCIA`: `NUMBER(16,2)` (Existencia en bodega antes de la venta).

**4. Asiento Contable: `co_documento_items`**
* `ID_DOCUMENTO` (FK): `VARCHAR2(8)`.
* `ID_ITEM`: `VARCHAR2(8)`.
* `ID_CUENTA`: `VARCHAR2(8)` (ID de la cuenta contable según la plantilla).
* `DEBE_HABER`: `VARCHAR2(1)` ('D' o 'H').
* `CAMPO`: `VARCHAR2(50)` (Referencia: 'MCIA', 'IVA', 'CXC', etc.).
* `VALOR`: `NUMBER(16,2)`.

**D. La Regla Anti-Paginación para Oracle 11g
A partir de este momento, en todo el código Python (Django/FastAPI) que interactúe con Oracle, quedan ESTRICTAMENTE PROHIBIDOS los siguientes métodos del ORM porque inyectan FETCH FIRST o OFFSET:

❌ Prohibido usar:

Model.objects.first()

Model.objects.last()

Model.objects.all()[:10] (Slicing)

Model.objects.earliest() / latest()

Cualquier paginador nativo de Django (Paginator).

✅ Nuestras Alternativas Obligatorias:

Iteración Defensiva (Para buscar "El Primero"):
Como te sugirió Antigravity, si necesitamos buscar la última factura, no usamos .first(). Traemos el QuerySet ordenado y lo cortamos en memoria:

Python
# BIEN HECHO
for doc in CoDocumento.objects.filter(estado_doc='A').order_by('-fch_documento'):
    ultimo_doc = doc
    break # Rompemos el ciclo en Python
Raw SQL (El Rey del Rendimiento para Lotes):
Cuando el cajero abra la pantalla de "Buscar Artículos" y necesitemos traer los primeros 50 para no reventar la red, usaremos SQL puro inyectando el clásico ROWNUM:

Python
# BIEN HECHO
query = """
SELECT * FROM (
    SELECT * FROM in_articulos 
    WHERE nombre LIKE %s 
    ORDER BY nombre
) WHERE ROWNUM <= 50
"""
articulos = InArticulo.objects.raw(query, ['%PAPEL%'])
El get() Seguro:
Cuando busquemos un registro único por su llave primaria (ej. al facturar un pedido específico), usar .get() es totalmente seguro porque Django lo traduce a un simple WHERE id = X, sin sintaxis moderna de paginación.

Python
# BIEN HECHO
factura = CoDocumento.objects.get(id_documento='FAC-1234')