# CAPÍTULO 5: Representación Gráfica (Impresión WeasyPrint y Asincronía)

**A. CONCEPTO DE NEGOCIO Y ARQUITECTURA (AI INSTRUCTIONS)**
1.  **Motor HTML a PDF (WeasyPrint):** La representación gráfica de las facturas (y otros documentos) se genera transformando plantillas HTML/CSS estrictamente maquetadas a formato PDF mediante la librería `WeasyPrint`. Quedan deprecadas las estrategias basadas en `docxtpl` o herramientas ofimáticas.
2.  **Impresión en Masa Asíncrona:** La generación de facturas puede ser intensiva. Por regla arquitectónica, toda impresión masiva se delega a colas de tareas en background utilizando **Django Q2** (`qcluster`). Las vistas Django solo encolan la tarea y responden inmediatamente.
3.  **Anti-Paginación Absoluta:** Para compatibilidad con Oracle 11g, las consultas del renderizador deben procesar todo en memoria usando `.fetchall()` o cursores sin limit/offset, evitando bloqueos en la base de datos origen.

---

**B. ESTRUCTURA DEL CONTEXTO Y RENDERIZADO**

La función constructora del contexto (`_build_context`) debe extraer la información cruda de Oracle 11g (`MvPedidosNorth`, `SG_PARAMETROS`, `SG_SISTEMAS`) y mapearla al diccionario requerido por el HTML.

**1. Desacoplamiento de Prefijo y Número:**
Para evitar colisiones o visualizaciones dobles (Ej: FES-FES123), la capa Python debe implementar expresiones regulares (`re.match(r"([A-Za-z]+)(\d+)", num_doc)`) para separar limpiamente el `prefijo` (alfabético) del `num_documento` (numérico) antes de enviarlo a la plantilla.

**2. Formateo y Mapeo Visual:**
*   **Logo de Empresa:** Se carga estáticamente desde el disco (ej. `BASE_DIR / 'templates' / 'logo_credito.jpeg'`) y se inyecta en Base64 para evitar dependencias de URLs absolutas de red durante el render de WeasyPrint.
*   **Limpieza de UI:** Se evitan textos 'quemados' en el HTML. Las direcciones, teléfonos y resoluciones provienen de la BD.

---

**C. GENERACIÓN E INYECCIÓN DE QR Y BARCODE**

El Código QR y el Código de Barras son requisitos normativos y operativos. Se procesan enteramente en RAM.

**1. Código QR (Alta Resolución):**
*   Librería: `qrcode`.
*   Para evitar difuminado por redimensionamiento en el PDF, se debe usar `box_size=10` y `error_correction=qrcode.constants.ERROR_CORRECT_M`.
*   Salida: `BytesIO` convertido a un string Base64 (`data:image/png;base64,...`) e inyectado directo al `src` de la etiqueta `<img>`.
*   CSS: Uso de `image-rendering: pixelated;` en la imagen HTML.

**2. Código de Barras (Code128):**
*   Librería: `python-barcode` con el `ImageWriter`.
*   Se genera usando la concatenación del prefijo limpio y número limpio (ej. `FES5663`). Ocultando el texto nativo (`write_text=False`).
*   Salida: Idéntico al QR, en formato Base64 inyectado en el DOM.

---

**D. FLUJO DE EJECUCIÓN (Cola de Impresión)**

1.  **Activación UI:** El usuario desencadena la impresión masiva desde el frontend (vía un POST/HTMX modal).
2.  **Encolamiento (`tasks.py`):** La vista llama a `async_task('facturacion.tasks.generar_pdfs_lote', request.POST)`.
3.  **Procesamiento Background:** 
    * El worker de `django-q2` toma la tarea.
    * Itera sobre las facturas seleccionadas.
    * Obtiene datos (Oracle), ensambla el HTML local y dispara WeasyPrint.
    * Guarda el PDF resultante en un directorio local predeterminado (ej. `facturas/`).
    * **Actualización DB:** Por cada factura exitosa, ejecuta un `UPDATE` en Oracle (`SIONO_IMPRESO='S'`) usando cursores nativos y realiza un `COMMIT`.
4.  **Feedback:** El frontend implementa Alpine.js o HTMX polling para informar al usuario sobre el estado final.
