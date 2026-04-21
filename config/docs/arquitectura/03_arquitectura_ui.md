# CAPÍTULO 3: Arquitectura del Proyecto y Flujo de Interfaz (UI)

**A. ESTRUCTURA DE DIRECTORIOS (Django Modular)**

Para mantener la coherencia con `north_admin` y asegurar las mejores prácticas, el proyecto se dividirá en las siguientes aplicaciones (apps) independientes dentro de Django:

* **`north_local_project/`**: Configuración principal, ruteo global y variables de entorno (`.env` con credenciales de Oracle).
* **`core/`**: Motor central. Contendrá la clase de conexión a Oracle (`oracledb`), utilidades globales, y las funciones puente para llamar a los logs de la base de datos (`MARCA` y `MARCA2`).
* **`facturacion/`**: Módulo crítico. Contendrá las vistas de la bandeja de entrada de pedidos, el motor de cálculo matemático (Capítulo 2) y la transmisión electrónica a la DIAN.
* **`impresion/`**: Módulo dedicado exclusivamente a la generación de PDFs y generación del código QR.
* **`pos/`**: Interfaz de cajero local para ventas rápidas.
* **`logistica/`**: Módulo para planillas de envío y control de picking.

**B. DISEÑO DE INTERFAZ (UI/UX)**

* **Framework CSS:** TailwindCSS.
* **Patrón Visual:** Estilo ERPNext. Menú lateral fijo (Sidebar) para navegación, barra superior para usuario/sucursal, y panel central de contenido limpio.
* **Accesibilidad (POS):** Para el módulo de punto de venta y logística, se usarán fuentes de alta legibilidad, botones de gran área de clic y contrastes claros (sin información innecesaria en pantalla) para evitar errores operativos.

**C. FLUJO DE FACTURACIÓN MASIVA (REGLAS ESTRICTAS DE UI Y NEGOCIO)**

La pantalla principal de facturación masiva no será una tabla interactiva estándar. Debe cumplir con el siguiente comportamiento para evitar descuadres operativos:

1.  **Carga de Datos (Query):**
    * La vista debe consultar a Oracle todos los pedidos aprobados que aún no han sido facturados: `SELECT * FROM mv_pedidos_mobilecorp WHERE procesado IS NULL ORDER BY fecha ASC`.
    * **Orden Obligatorio:** Los datos deben presentarse y procesarse estrictamente desde el más antiguo al más nuevo.
2.  **Regla de "No Selección" (Todo o Nada):**
    * Prohibido incluir casillas de verificación (checkboxes) por fila.
    * El usuario **no puede** elegir qué pedidos facturar y cuáles omitir. Esto previene el error humano de dejar pedidos rezagados.
    * *Política Operativa:* Si un pedido visible en la lista no debe ser facturado por algún error, el usuario debe ir a `north_admin` y anularlo/cancelarlo allá. Automáticamente desaparecerá de esta bandeja local.
3.  **Disparador de la Acción:**
    * Solo existirá un botón de acción global (ej. "Ejecutar Facturación Masiva").
    * Al hacer clic, el sistema debe desplegar un modal de confirmación advirtiendo la cantidad de pedidos que se van a procesar.
    * Al confirmar, el backend (Python) inicia el bucle transaccional descrito en el Capítulo 2.
