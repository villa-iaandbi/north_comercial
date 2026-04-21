# CAPÍTULO 5: Representación Gráfica (Impresión con Plantillas y QR)

**A. CONCEPTO DE NEGOCIO Y ARQUITECTURA (AI INSTRUCTIONS)**
1.  **Cero Hardcoding Visual:** Queda estrictamente prohibido generar PDFs desde cero utilizando librerías de dibujo vectorial (como ReportLab o FPDF). 
2.  **Motor de Plantillas:** La representación gráfica se generará poblando documentos de Microsoft Word (`.docx`) utilizando la librería `docxtpl` (DocxTemplate).
3.  **Diseño Delegado:** El diseño visual (logos, fuentes, márgenes) pertenece a la plantilla física `.docx`. Python actuará únicamente como inyector de datos (Context Provider).

---

**B. ESTRUCTURA DEL CONTEXTO (El Diccionario de Datos)**

Para renderizar la plantilla, Python debe compilar toda la información de la factura (`CO_DOCUMENTOS`, `CT_VENTAS`, `IN_MOV_INVENTARIOS`) en un único diccionario de contexto.

**1. Cabecera y Maestros:**
* `nom_sistema`, `nit_emp`, `dir_emp`, `municipio_emp`, `resolucion` (Extraídos de `SG_PARAMETROS` o `SG_SISTEMAS`).
* `num_documento`, `fch_documento` (Fecha de expedición), `vendedor`.
* `nom_tercero`, `nit_tercero` (Datos del Adquirente).
* `cufe` (Obtenido de la respuesta del Proveedor Tecnológico en el Capítulo 4).

**2. Listas y Ciclos (Jinja2 Syntax):**
* **`items`:** Una lista de listas o diccionarios que alimentará el ciclo `{%tr for item in items %}` en la plantilla de Word. Debe contener: `cantidad`, `nom_articulo`, `vlr_unitario`, `vlr_total`.
* **`pagos`:** Lista de medios de pago para la sección de formas de pago.

**3. Totales e Impuestos:**
* `tot_items`, `tot_mercancia` (Subtotal), `tot_documento` (Gran total).
* Discriminación de tarifas: `tot_excluida`, `tot_mercancia2` (Base 5%), `tot_iva2`, `tot_mercancia3` (Base 19%), `tot_iva3`.

---

**C. GENERACIÓN E INYECCIÓN DEL CÓDIGO QR**

El Código QR es un requisito legal de la DIAN y debe generarse dinámicamente en memoria antes de inyectarse en el documento de Word.

**1. Construcción de la Cadena (String del QR):**
* La cadena debe apuntar a la URL de validación de la DIAN, concatenando el CUFE. 
* *Ejemplo de formato:* `https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={CUFE}`

**2. Estrategia de Inyección en Python:**
* **Generación:** Usar la librería `qrcode` de Python para generar la imagen PNG en memoria (usando `io.BytesIO` para no guardar archivos temporales basura en el servidor).
* **Conversión a InlineImage:** El agente debe importar `InlineImage` desde `docxtpl.shared`.
* **Inyección:** En el diccionario de contexto, la llave `codigo_qr` se asignará al objeto `InlineImage` instanciado con la imagen en memoria y un ancho (width) predefinido (ej. 30 o 40 milímetros).
* *En la plantilla de Word, simplemente se colocará la etiqueta `{{ codigo_qr }}` en la zona designada.*

---

**D. FLUJO DE EJECUCIÓN (Render y Salida)**

1.  **Carga de la Plantilla:** `doc = DocxTemplate("ruta/a/la/plantilla_factura_pos.docx")`
2.  **Renderizado:** `doc.render(contexto)`
3.  **Salida/Descarga:**
    * El documento final debe guardarse temporalmente con un nombre único (ej. `Factura_POS_Prefijo_Numero.docx`).
    * En un entorno web local (Django), la vista debe devolver este archivo generado directamente como una respuesta HTTP (`FileResponse` o `HttpResponse` con content-type `application/vnd.openxmlformats-officedocument.wordprocessingml.document`) para que el cajero lo descargue o se envíe directo a la cola de impresión local del sistema POS.
