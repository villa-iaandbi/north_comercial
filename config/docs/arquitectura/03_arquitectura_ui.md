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

**C. FLUJO DE REINTENTO DE FACTURACIÓN MASIVA (UI Y LÓGICA HTMX)**

La pantalla principal de monitoreo FEL (Panel de Control DIAN) está diseñada para otorgar control granular y retroalimentación en tiempo real, operando bajo las siguientes reglas:

1.  **Motor de Datos y Desempeño:**
    * La vista se alimenta de `CT_VENTAS_FEL`, mediante una consulta SQL nativa optimizada con `ROWNUM` cruzando con la tabla maestra de documentos para obtener prefijos y fechas reales. Esto evita los bloqueos de memoria (*OOM*) del ORM tradicional en Oracle 11g al procesar millones de registros.
    * Los datos se ordenan estrictamente de forma descendente por la Fecha Real Original del Documento.

2.  **Selección Múltiple Granular (Checkboxes):**
    * Se abandona el diseño imperativo estricto ("todo o nada") para permitir flexibilidad ante fallos técnicos del proveedor tecnológico. 
    * El usuario tiene a su disposición casillas de verificación globales e individuales en cada fila para seleccionar exactamente qué documentos fallidos o atascados desea procesar.

3.  **Disparador Asíncrono e Interfaz Reactiva (HTMX):**
    * Con el botón de "Reenviar Seleccionadas", HTMX orquesta una llamada `POST` segura (integrando el token CSRF de Django) enviando los IDs en lote para su encolamiento.
    * El backend responde inmediatamente con un Modal (*Feedback visual rápido*) confirmando cuántos documentos entraron en cola, sin bloquear en ningún momento la pantalla.

4.  **Polling Inteligente Auto-Regulado:**
    * El dashboard implementa un mecanismo de *polling inteligente* inyectado por HTMX.
    * El frontend audita silenciosamente, cada 10 segundos, si existen procesos pendientes en la base de datos de colas (`qcluster_db`). Si el trabajador (Worker) se desocupa (cola vacía), la interfaz de HTMX detiene su auto-recarga automáticamente, garantizando cero consumo inútil del servidor.
