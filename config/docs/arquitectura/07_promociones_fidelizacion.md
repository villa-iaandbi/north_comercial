# CAPÍTULO 7: Arquitectura de Promociones y Fidelización

**A. MOTOR DE PROMOCIONES (Rules Engine)**
Para soportar la diversidad de promociones (dinero, especie, mixto) sin alterar el código fuente, se implementa una arquitectura basada en reglas dinámicas:

1.  **Modelo de Datos (Oracle 11g):**
    * `PR_PROMOCIONES`: Cabecera de la campaña (Vigencia y Prioridad).
    * `PR_CONDICIONES`: Filtros de activación (Por producto, por marca, por monto de factura, por cliente).
    * `PR_ACCIONES`: Beneficio a otorgar (Porcentaje de descuento, valor fijo, artículo de regalo).
2.  **Ejecución Offline (POS):**
    * Las reglas vigentes se sincronizan en la base de datos local (`IndexedDB`) al iniciar el turno.
    * El motor de JavaScript del POS evalúa el "carrito de compras" en tiempo real contra las condiciones.
    * Al liquidar, los descuentos otorgados se registran en una línea separada del JSON o afectando el `DESCUENTO` de la línea, para que el motor en Python (Capítulo 2) contabilice el costo de la promoción correctamente.

**B. MOTOR DE FIDELIZACIÓN (Programa de Puntos)**
1.  **Acumulación (Earn - Soporta Offline):**
    * El cálculo de puntos ganados se realiza localmente en el POS basado en el `TOT_MERCANCIA` del ticket.
    * Los puntos generados viajan en el JSON de la factura. Python se encarga de actualizar el "Kardex de Puntos" del cliente en Oracle de forma asíncrona.
2.  **Redención (Burn - Estrictamente Online):**
    * El uso de puntos como Medio de Pago (`PaymentMeans`) requiere validación de saldo en tiempo real.
    * **Restricción Arquitectónica:** Si la caja POS pierde conexión a la red central (Offline Mode), la opción de redimir puntos se desactiva automáticamente por seguridad para prevenir el doble gasto.
