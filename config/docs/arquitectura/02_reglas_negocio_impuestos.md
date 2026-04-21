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
* Extraer con `ID_SISTEMA = '1'`: Bodega por defecto, causación de retenciones, IDs de retención (RTE_COMPRAS, RTE_AGRICOLA), y porcentajes de ICA.

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

**D. MOTOR DE CÁLCULO FINANCIERO (Línea por Línea)**

Por cada ítem validado, se ejecutan los cálculos acumulando los valores en las variables correspondientes (`MCIA1...5`, `TOT_IVA2...5`):

1.  **IVA (Gravamen):** Se obtiene el porcentaje mediante `PORC_IVA = GRAVAMEN(ID_GRAVAMEN) / 100`.
2.  **Subtotal:** `nSUBTOTAL = ROUND(nCANTIDAD * VLR_UNITARIO * (1 - DESCUENTO / 100), 2)`.
3.  **Valor IVA:** `nVLR_IVA = nSUBTOTAL * PORC_IVA`.
4.  **Impoconsumo:** `nTOT_IMPOCONSUMO_LINEA = nCANTIDAD * IMPOCONSUMO`.
5.  **Comisión:** `nVLR_COMI = nSUBTOTAL * (PORC_COMISION / 100)`.

---

**E. MOTOR TRIBUTARIO (Retenciones de Cabecera)**

Si el parámetro `CAUSA_RTEFUENTE = 'SI'`, se calculan las retenciones tras sumar todos los ítems:

1.  **RTE_RENTA (Compras/Agrícola):**
    * Se aplica si el tercero **no** es autorretenedor y la base (`nTOT_MERCANCIA`) supera el `VLR_BASE` en `CO_RETENCIONES`.
    * `VALOR = ROUND(Base * PORC_RETENCION / 100)`.
2.  **RTE_IVA:**
    * Cruce de regímenes entre empresa y cliente según `SG_IVA_REGIMENES`.
    * `VALOR = ROUND(nTOT_IVA * PORC_RETENCION / 100)`.
3.  **RTE_ICA:**
    * Se aplica si existe `PORC_RETENCION_ICA > 0` y el cliente es sujeto de ICA.
    * `VALOR = ROUND(nTOT_MERCANCIA * PORC_RETENCION_ICA / 100)`.
4.  **CXC Final (Total a Pagar):**
    * `CXC = (Mercancía + IVA + Impoconsumo) - (Retenciones Totales)`.

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
