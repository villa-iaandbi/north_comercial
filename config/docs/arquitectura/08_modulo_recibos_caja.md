# CAPÍTULO 8: Módulo de Recibos de Caja

**A. CONCEPTOS GENERALES**

El módulo de Recibos de Caja permite la liquidación de obligaciones (cartera) recaudadas en terreno, procesando lotes sincronizados de Supabase hacia el core financiero del ERP en Oracle.

---

**B. ARQUITECTURA DE RECAUDOS (La "Trinidad de Tesorería")**

El modelo de inserción de recaudos es un proceso integral que debe afectar la siguiente *trinidad* obligatoria en tableros legacy, además de la propia contabilidad:
1.  **`ts_ingresos` (El Ingreso Físico):** Registra el dinero, la confirmación de transferencia, o la constancia del cheque que físicamente entró a la cuenta.
2.  **`ts_medio_pagos` (La Conciliación Bancaria):** Cruza el medio del pago (número de recibo/cheque/voucher) con el banco emisor o la plaza para procesos de tesorería y arqueo de caja.
3.  **`co_documento_afectados` (El Cruce/Glosa):** Conecta el saldo abonado (y los descuentos o retenciones deducidas) con la cabecera original (la Cuenta Por Cobrar / Factura a la que va dirigida). Da de baja a la mora.

---

**C. DJANGO COMO ENRUTADOR**

A diferencia de la Facturación, donde el servidor realiza pesadas reglas de cascada y extracción de porcentajes (Capítulo 2); en el recaudo el backend de Django actúa exclusivamente como **Dummie-Router** o enrutador asíncrono.
Todas las retenciones (`ret_compras`, `ret_servicios`, `ret_iva`, `ret_ica`, `ret_otros`) y las rebajas (`descuento` financiero) **ya fueron acordadas y procesadas algorítmicamente en la aplicación móvil** o el portal respectivo (`mv_recibo_items_north`). Django confía en las variables de origen, se abstiene de realizar recálculos de tarifas y simplemente mapea y contabiliza.

---

**D. CUADRATURA CONTABLE DINÁMICA (El Algoritmo de Cierre)**

Se elabora a través de la inyección iterativa al diccionario de puente `mapa_valores` cruzado contra `co_plantilla_items`, aplicando la inquebrantable regla financiera conocida como "La Ecuación de Cierre Perfecto":

`Débitos (Valor de CAJA + Suma absoluta de RETENCIONES + DESCUENTOS Efectuados) == Créditos (Valor del abono a cruzarse contra la CXC del Documento Afectado)`

Si en el proceso el `mapa_valores` falla en lograr un balance a suma cero entre cuentas "D" y "H", denota corrupción del envío de parámetros frontales y se debe incurrir en Rollback automático.

---

**E. MECANISMOS DE SAFEGUARD DEL ORM (PKs Virtuales)**

*   **Problema con Django:** Tablas físicas en Oracle 11g como `ts_medio_pagos` emplean llaves primarias compuestas (`ID_DOCUMENTO`, `ID_ITEM`), arquitectura que Django actualmente no soporta orgánicamente en sus modelos sin paquetes de terceros.
*   **La Solución:** Para evitar el ORA-00001 (Violación de Unicidad), se dota al modelo interno de Django de un identificador `id_registro_virtual` ficticio actuando como `primary_key=True` sin estar mapeado en base de datos.
*   **Inyección Pura:** A su vez, para evitar el problema en que Django interpreta inserciones de llave compuesta como "Actualizaciones a llaves repetidas" que devuelven ORA-02291 / KeyError, toda la inyección debe realizarse ya sea por medio de Inyección SQL cruda (`cursor.execute`) con secuencias pre-armadas en RAM o utilizando estricto `objects.bulk_create([])`.
