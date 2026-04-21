# CAPÍTULO 4: Facturación Electrónica DIAN (Proveedor: Binapps)

**A. ARQUITECTURA DE TRANSMISIÓN ASÍNCRONA (Background Workers)**

**1. El Problema (Evitar el Bloqueo):**
Si se facturan 100 pedidos masivamente, enviar cada JSON al proveedor tecnológico (PT) de forma síncrona dentro del mismo ciclo congelará la interfaz del usuario web y causará un *Timeout*.

**2. Estrategia de Optimización en Python (Cola de Tareas):**
* La vista principal (UI) **solo** ejecuta el Capítulo 2 (crea las facturas en la base de datos Oracle).
* Una vez creadas, la vista encola los `ID_DOCUMENTO` en un sistema de Background Tasks (ej. `Django-Q` o `Celery`) y le responde inmediatamente al usuario: *"X facturas generadas. Transmitiendo a la DIAN en segundo plano"*.
* Un "Worker" (trabajador en segundo plano) toma cada factura, arma el JSON y hace la petición HTTP POST a Binapps de forma paralela.

---

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
