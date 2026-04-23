# CAPÍTULO 2: Reglas de Negocio, Impuestos y Retenciones

**A. REGLAS GLOBALES Y DE ENTORNO (AI INSTRUCTIONS)**
1.  **Constante de Sistema:** El proyecto NO es multitenant. El campo `ID_SISTEMA` debe asignarse estrictamente como `'1'` en todos los inserts.
2.  **Manejo de Fechas:** Las facturas deben registrarse con la fecha del sistema en hora local de Colombia (`America/Bogota`). Prohibido usar UTC.
3.  **Manejo de Decimales (Crítico):** Se debe utilizar la librería `decimal.Decimal` de Python para todos los cálculos. Queda prohibido el uso de `float` para evitar descuadres.
4.  **Trazabilidad:** Cada paso crítico debe invocar los procedimientos de log `MARCA` o `MARCA2` en Oracle para auditoría.
5.  **Cero Alucinación:** Si un concepto o fórmula no está clara, el agente DEBE detenerse y solicitar aclaración.

---

**B. MOTOR DE PARÁMETROS Y DATOS MAESTROS (Carga Inicial y Caché)**

*(AI INSTRUCTION: Para evitar el problema N+1, todos los datos que dependen de catálogos maestros deben consultarse en bloque antes de iniciar el ciclo de facturación).*

**1. Parámetros Globales (`SG_PARAMETROS`):**
* Se omitirá el filtro `ID_SISTEMA = '1'` en el query a la tabla `sg_parametros` para asegurar que la caché cargue sin problemas (dado que todos se mapean como texto desde la columna `VLR_CHR`). Los IDs de parámetros obligatorios en código duro son:
  * `EXISTENCIA_BOD`: Código de bodega y Tipo existencia (por defecto asume que es la bodega principal o '1').
  * `COD_VEN_CREDITO`: Tipo de documento para asignar a las facturas. (Su valor devuelto suele ser ej. '201').
  * `CAUSA_RTEFUENTE`: Bandera de causación ('SI' / 'NO').
  * `RTE_COMPRAS`: ID retención general. Debe mapearse cruzándose contra `CO_RETENCIONES`.
  * `RTE_AGRICOLA`: ID retención especial. Debe mapearse contra `CO_RETENCIONES`, se dispara si el artículo indica ser Agrícola ('S').
  * `PORC_RETENCION_ICA`: ID de retención para el ICA, que fluye desde los cruces de retención.

**2. Asignación de Centro de Costo (`SG_USUARIO_CENTRO_COSTOS`):**
* **Concepto:** Cada transacción contable y de inventario debe estar amarrada al centro de costo del vendedor (`ID_VENDEDOR` en el pedido equivale al `ID_USUARIO`).
* **Estrategia Python:** Antes de iterar las facturas, extraer la lista de vendedores únicos del lote de pedidos. Hacer un único query a `SG_USUARIO_CENTRO_COSTOS` y guardar el mapa en memoria: `mapa_cco = {vendedor_id: id_centro_costo}`.
* **Aplicación:** Al construir el JSON transaccional, este valor se inyectará obligatoriamente en el campo `ID_CENTRO_COSTO` de las tablas `in_mov_inventarios` y `co_documento_items`.

**3. Maestro de Artículos (`IN_ARTICULOS`):**
* Extraer en diccionario por `ID_ARTICULO`: `ID_GRAVAMEN`, `VLR_PROMEDIO`, `IMPOCONSUMO`, `SIONO_EXISTENCIA_NEGATIVA`, `NOM_ARTICULO`.

**4. Comisiones Vendedores:**
* Cargar el cruce de `IN_COMISION_GRUPOS` para los esquemas de comisión de los vendedores involucrados.
---

**C. REGLAS DE INVENTARIO (Validación de Línea)**

Antes de procesar cada ítem del pedido, se aplica la lógica de disponibilidad:
1.  **Consulta de Existencia:** Se obtiene `nEXIST` de la bodega por defecto.
2.  **Validación de Negativos:**
    * Si `CANTIDAD > nEXIST` y el artículo tiene `SIONO_EXISTENCIA_NEGATIVA = 'N'`:
        * La cantidad a facturar se ajusta al saldo disponible: `nCANTIDAD = max(nEXIST, 0)`.
    * En caso contrario, se procesa la cantidad solicitada.
*(Nota: La validación de cupo de crédito se omite por ser gestionada previamente en el portal).*

---

---

**D. LÓGICA DE AGRUPACIÓN POR CLIENTE Y CONSOLIDACIÓN DE ÍTEMS**

1.  **Agrupación de Pedidos:** Para optimizar la cartera, todos los pedidos seleccionados de un mismo cliente (`ID_TERCERO`) bajo la misma `FORMA_PAGO` deben consolidarse en una única cabecera (Factura). 
2.  **Consolidación de Artículos:** Si distintos pedidos consolidados demandan el mismo `ID_ARTICULO` exactamente al mismo `VLR_UNITARIO` y con el mismo `DESCUENTO`, las cantidades deben sumarse en una única línea (detalle) en la factura resultante para evitar spam de renglones contables.

---

**E. MOTOR DE CÁLCULO FINANCIERO Y COSTOS (Línea por Línea)**

Por cada ítem validado (y opcionalmente consolidado), se ejecutan los cálculos:

1.  **Ajuste de Exentos ('Secuestro de Gravamen'):** Si el cliente receptor está marcado como exento de IVA (`SIONO_IVA == 'S'`), todo el gravamen del artículo baja forzadamente a tarifa cero (0%) independientemente del catálogo.
2.  **IVA (Gravamen):** Se obtiene el porcentaje. `PORC_IVA = GRAVAMEN(ID_GRAVAMEN) / 100`.
3.  **Subtotal:** `nSUBTOTAL = ROUND(nCANTIDAD * VLR_UNITARIO * (1 - DESCUENTO / 100), 2)`.
4.  **Valor IVA:** `nVLR_IVA = nSUBTOTAL * PORC_IVA`.
5.  **Doble Costo e Inventario:** Además del VLR_PROMEDIO (Costo Promedio), se extrae el costo de reposición (`VLR_ULT_COMPRA`) del artículo para cálculos de rentabilidad comercial.
6.  **Impoconsumo:** Contemplado linealmente. `nTOT_IMPOCONSUMO_LINEA = nCANTIDAD * IMPOCONSUMO`.
7.  **Extracción de Comisiones:** Se extrae dinámicamente consultando el maestro pre-cacheado `IN_COMISION_GRUPOS` filtrado por el `ID_USUARIO` (Vendedor de la tabla externa `CT_VENDEDOR`) en combinación con la clase/grupo del artículo respectivo.

---

**F. MOTOR TRIBUTARIO EN CASCADA (Retenciones de Cabecera)**

Al finalizar la sumarización de la factura consolidada, los atributos fiscales se computan en esquema de cascada:

1.  **Desbordamiento Agrícola a Compras:** 
    * Si las bases se segmentaron, pero la retención Agrícola **no supera su propia base mínima**, la base gravable agrícola sobrante se 'desborda' sumándose automáticamente a la base de Retención por Compras.
2.  **RTE_RENTA (Compras/Agrícola):**
    * Aplicadas si el tercero no es autorretenedor y la base consolidada (después del desbordamiento) supera el tope normativo (`VLR_BASE`).
3.  **RTE_IVA (Exclusivo a Régimen 3):**
    * Solo aplica sí y solo sí el Cliente Receptor pertenece al Gran Contribuyente (`ID_REGIMEN == '3'`) y la base de IVA supera el tope.
4.  **RTE_ICA:**
    * Basado en la ciudad del suministro y si el cliente es sujeto pasivo de ICA y no exento de dicho rubro municipal.
5.  **CXC Final (Total Cartera):**
    * `CXC = (Mercancía + IVA + Impoconsumo) - (Retenciones Totales Calculadas)`.

---

**F. AFECTACIÓN CONTABLE (Plantillas Dinámicas)**

**1. Concepto (`CO_DOCUMENTO_ITEMS`):**
La contabilización es dinámica. Una vez finalizada la factura, el motor lee la tabla `CO_PLANTILLA_ITEMS` asociada al `ID_TIPO_DOCUMENTO`.

**2. Mapeo en Python:**
Se utiliza un diccionario de mapeo para transferir los valores calculados a las cuentas contables:
```python
mapa_valores = {
    'MCIA1': nMCIA1, 'MCIA2': nMCIA2, 'MCIA3': nMCIA3, 'MCIA4': nMCIA4, 'MCIA5': nMCIA5,
    'TOT_IVA2': nTOT_IVA2, 'TOT_IVA3': nTOT_IVA3, 'TOT_IVA4': nTOT_IVA4, 'TOT_IVA5': nTOT_IVA5,
    'COSTO_VENTA': nCOSTO_VENTA, 'CXC': nCXC, 'TOT_IMPOCONSUMO': nTOT_IMPOCONSUMO,
    'RTE_COMPRAS': nRTE_COMPRAS, 'RTE_AGRICOLA': nRTE_AGRICOLA, 'RTE_ICA': nRTE_ICA,
    'RTE_OTROS': nRTE_CREE
}
```
**3. Inserción Masiva:**
* El agente debe iterar la plantilla, filtrar los campos con VALOR > 0 y realizar una inserción en bloque (bulk_create) para optimizar el rendimiento de la red local.
* Se debe usar el ID_CUENTA interno (PK) proporcionado por la plantilla.
