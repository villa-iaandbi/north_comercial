# CAPÍTULO 6: Arquitectura POS (Punto de Venta) - Offline y Sincronización

**A. TOPOLOGÍA Y ALMACENAMIENTO (Hub-and-Spoke)**
1.  **El Hub (Central):** Servidor Python + Oracle 11g. Contiene la única verdad financiera.
2.  **Los Nodos (Cajas POS):** Terminales web ligeras. Todo el catálogo de `IN_ARTICULOS` se descarga al inicio del turno y se almacena en la base de datos interna del navegador (`IndexedDB`).
3.  **Filosofía Offline-First:** La caja debe permitir registrar ventas y cobrar sin conexión a internet, encolando las transacciones localmente.

**B. KARDEX Y CONTABILIDAD POS (Consolidación Diaria)**
Para evitar la saturación de Oracle (el efecto "Everest" de registros), queda **estrictamente prohibido** que el POS local envíe un asiento contable o un movimiento de inventario por cada ticket individual.
1.  **Inventario (Kardex):** Los tickets se consolidan localmente o en una tabla temporal. Al realizar el "Cierre de Caja", se agrupan las ventas por `ID_ARTICULO` y se envía un **único registro consolidado** a `IN_MOV_INVENTARIOS` por cada producto vendido en ese turno/locación.
2.  **Contabilidad:** Se genera **un solo asiento contable** maestro (`CO_DOCUMENTO_ITEMS`) por locación al final del día (Comprobante de Ingreso Cierre Z), agrupando el total de la caja, descuentos e impuestos generados.

**C. CICLO DE VIDA DE DATOS LOCALES (Rutina de Purga / Garbage Collection)**
Para evitar el colapso de la memoria RAM del navegador (`IndexedDB`) en las cajas registradoras, el Service Worker del POS debe implementar una política estricta de limpieza de históricos locales:

* **Condición 1 (Sincronización Confirmada):** Un registro de venta local solo es elegible para eliminación si el servidor central (Python) ha devuelto una respuesta exitosa y el registro está marcado localmente como `sync_status = true`.
* **Condición 2 (Ventana Rodante - TTL):** Los registros confirmados se mantienen en la caché local por una ventana de **3 días** (configurable) para permitir reimpresiones rápidas o devoluciones inmediatas sin latencia de red. Todo registro donde `fecha_ticket < (HOY - 3 DIAS)` será destruido.
* **Ejecución de la Purga:** La rutina de eliminación local se ejecutará automáticamente en procesos de fondo, específicamente durante el **Cierre de Turno (Cierre Z)** o en el **Login Inicial** del día, para no afectar el rendimiento del procesador durante las horas pico de facturación.
* **Refresh de Catálogo:** La tabla maestra local de artículos y precios se sobreescribe (reemplazo total) al menos una vez al día para garantizar que no queden productos obsoletos en la memoria del navegador.
