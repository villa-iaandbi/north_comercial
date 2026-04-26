# CAPÍTULO 4: Facturación Electrónica DIAN (Proveedor: Binapps)

**A. ARQUITECTURA DE TRANSMISIÓN ASÍNCRONA (Background Workers)**

**1. El Desafío (Rendimiento y Tolerancia a Fallos):**
Transmitir cientos de facturas masivamente dentro del hilo principal produciría un inevitable *Timeout* HTTP y congelaría el servidor web. Además, si una sola factura de un lote grande produce error en la API de Binapps, no debe frenarse la cola de las demás facturas.

**2. Sistema de Colas con Aislamiento de Micro-base (Django-Q2 + SQLite):**
* El proyecto implementa **Django-Q2** para el agendamiento y encolamiento asíncrono.
* **Aislamiento Estratégico (Workaround Oracle 11g):** Debido a que el ORM de Django >= 4.0 genera secuencias `FETCH FIRST` y `Identity Columns` al iterar colas internas (incompatibles en Oracle 11g que producen los fatales `ORA-00933` y `ORA-02000`), se diseñó un **Enrutador de Base de Datos Personalizado** (`core.routers.DjangoQRouter`). 
* El enrutador aísla de forma quirúrgica *toda* la metadata, modelos y colas de `django_q` hacia un archivo de micro-base de datos local (`sqlite_db/qcluster.sqlite3`). 
* Esto nos otorga la velocidad, ligereza y cero-dependencias de SQLite, mientas se mantiene a Oracle 11g dedicado 100% solo al trabajo transaccional corporativo.

**3. Continuidad del Negocio (Worker Robusto):**
* Cuando el usuario orquesta el reenvío desde el botón Masivo (`HTMX`), la vista extrae la lista de identificadores `selected_docs` solicitando su encolo inmediato a SQLite.
* El proceso `qcluster` levanta a un grupo de "Workers" invisibles que reciben el lote, lo desempacan ID por ID y llaman al proceso unitario `InvoiceOrchestrator` de forma iterativa.
* **Aislamiento Lógico (`try...except`):** Cada iteración individual sobre la factura se aísla herméticamente en el código fuente. Ningún error nativo ni excepción HTTP al transmitir detiene el bucle; asegurando la transmisión garantizada del resto del bloque.


**B. AUTENTICACIÓN Y CONEXIÓN HTTP**

Antes de transmitir, el motor de Python debe realizar el flujo OAuth2:
1.  **Obtener Token:** Hacer un POST a la URL de autenticación (ej. `http://localhost/binappstoken/connect/token` o la configurada en variables) enviando el body: `client_id=EDocumentsWebApi`, `client_secret=...` y `grant_type=client_credentials`.
2.  **Cabeceras de Envío (Headers):**
    * `Content-Type: application/json; charset=UTF-8`
    * `TenantId`: Extraído del parámetro `BAPP_TEN`.
    * `Authorization`: `Bearer <token_obtenido>`.
3.  **URL Destino:** Se evalúa el parámetro `BAPP_URL` para Facturas/Soporte y `BAPP_URL2` para Notas.

---

**C. ESTRUCTURA DEL PAYLOAD JSON (Contrato de Datos)**

*(AI INSTRUCTION: Queda estrictamente prohibido ensamblar el JSON mediante concatenación de cadenas. Se debe construir un diccionario nativo en Python `dict` y serializarlo usando `json.dumps()` para garantizar el tipado de enteros, booleanos y nulos).*

**1. Fechas y Tiempos:**
Todas las fechas (`IssueDateTime`, `InvoiceDueDate`) deben enviarse en formato estricto ISO 8601: `YYYY-MM-DD"T"HH24:MI:SS`, garantizando que se use la zona horaria de Colombia (`America/Bogota`).

**2. Mapeo de Nodos Principales (Para Factura Venta):**

* **`InvoiceGeneralInformation`:**
    * `InvoiceNumber`: `NUM_DOCUMENTO` (Limpiando caracteres extraños).
    * `InvoiceAuthorizationNumber`: Resolución DIAN de la factura.
    * `Currency`: `'COP'`.
    * `SalesPerson`: Nombre del vendedor.
* **`CustomerInformation` (Adquirente):**
    * `Identification`: NIT del cliente (`NIT_TERCERO`), sin dígito de verificación.
    * `DV`: Función `DIGITO_VERIFICACION`.
    * `TaxLevelCodeListName`: Si el régimen es 2 usa `'49'`, si no `'48'`.
    * `TaxTributeCode`: Si régimen es 2 usa `'ZZ'`, si no `'01'` (IVA).
    * `FiscalResponsability`: Si régimen es 3 usa `'O-13'`, si no `'R-99-PN'`.
    * `CityCode`: Código DIAN del municipio (`ID_MUNICIPIO_DIAN`).
* **`DocumentItems` (Arreglo de Detalles):**
    * Se itera sobre `IN_MOV_INVENTARIOS`.
    * `ItemReference`: Referencia del artículo.
    * `Quantity`: Cantidad trunca (máximo a 2 decimales).
    * `Price`: VLR_UNITARIO con descuentos aplicados.
    * `TaxesInformation`: Arreglo interno con el cálculo exacto del IVA (y un segundo objeto si aplica `IMPOCONSUMO`).
* **`TotalInvoiceTaxes`:**
    * Agrupación del IVA por tarifas (TributeCode: 0), Impoconsumo (TributeCode: 1) y Retenciones aplicadas en cabecera (ej. RteCompras = TributeCode: 5).
* **`InvoiceTotal`:**
    * `LineExtensionAmount` / `TaxExclusiveAmount`: Total Mercancía pura sin impuestos.
    * `TaxInclusiveAmount` / `PayableAmount`: Gran total más impuestos y retenciones.
* **`PaymentMeans`:**
    * `PaymentType`: `2` (Crédito) si `PLAZO_PAGO > 0`, de lo contrario `1` (Contado).
    * `PaymentDueDate`: Fecha factura + Días de plazo.

---

**D. MANEJO DE RESPUESTAS Y ACTUALIZACIÓN EN BASE DE DATOS**

Una vez se hace la petición HTTP a Binapps mediante la librería `httpx` o `requests` de Python, se debe capturar el JSON de respuesta.

1.  Se extraen las llaves: `eDocumentStatus`, `cufe` y `statusMessage`.
2.  **Bloque de Actualización (Transaccional):**
    * Si `eDocumentStatus == '30'` (Procesado/Aprobado):
        * Se ejecuta un `INSERT` en la tabla `CT_VENTAS_FEL` (para facturas) con el `ID_DOCUMENTO`, el `CUFE` validado, el mensaje de error/respuesta, la fecha de envío actual y el estado `'30'`.
    * Si `eDocumentStatus == '2'` (Actualizado/Rechazado previo):
        * Se ejecuta un `UPDATE` en `CT_VENTAS_FEL` actualizando el estado, el `CUFE` y el mensaje para ese `ID_DOCUMENTO`.
3.  **Registro de Logs:** Al igual que en Oracle, el Python Worker debe guardar un log nativo con el payload enviado y la respuesta recibida para auditoría local.
