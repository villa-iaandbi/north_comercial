# Manual Estándar de Instalación: North Comercial en Windows Server
**Versión:** 1.0 (Estandarizada)  
**Sistemas Operativos Soportados:** Windows Server 2012 R2 / 2016 / 2019 / 2022 (64 bits)  
**Motor de BD:** Oracle 11g (Local o Remoto)

---

## 🎯 Objetivo y Filosofía de Despliegue

Este manual establece la **receta única y estandarizada** para instalar la aplicación **North Comercial** en cualquier cliente nuevo, garantizando que el procedimiento sea 100% idéntico en servidores legados (Windows Server 2012 R2) y en servidores modernos (Windows Server 2022).

Para lograr esto sin depender de diferencias de versión del sistema operativo, se ha fijado la matriz de componentes de software en versiones de máxima estabilidad y compatibilidad universal.

---

## 📋 Matriz Estándar de Componentes de Software

| Componente | Versión Estándar | Razón de Selección |
| :--- | :--- | :--- |
| **Python** | **3.8.10 (64-bit)** | Funciona de forma idéntica y nativa en Server 2012 R2 y Server 2022 sin bloqueos de directiva GPO. |
| **Oracle Instant Client** | **19c Basic (64-bit)** | `v19.23` o `v19.3`. Proporciona conectividad Thick obligatoria a Oracle 11g. |
| **Framework Web** | **Django 4.2 LTS** | Versión de soporte extendido de Django compatible con Python 3.8 a 3.12. |
| **Driver BD Oracle** | **python-oracledb** | Sustituto moderno de `cx_Oracle` configurado en modo Thick con alias automático. |
| **Generador de PDF** | **xhtml2pdf (Pisa)** | 100% puro Python, sin requerir instaladores DLL/GTK externos en Windows Server. |
| **Servidor de Producción** | **Waitress + NSSM** | Servidor WSGI ligero para Windows y gestor de Servicios de Windows 24/7. |

---

## ⚙️ Cambios Clave Incorporados en el Código Fuente (`north_comercial`)

Los siguientes ajustes ya quedaron **fijados en el repositorio de código**, por lo que al copiar la carpeta no se requiere modificar lógica interna:

1. **`config/settings.py`:**
   * Incorpora el alias automático `sys.modules["cx_Oracle"] = oracledb`.
   * Inicializa el cliente Oracle apuntando a la ruta estándar `C:\oracle\instantclient_19_23`.
2. **`config/urls.py`:**
   * Redirecciona automáticamente la raíz `/` hacia `/impresion/`.
3. **`impresion/urls.py`:**
   * Permite cargar el portal de impresión tanto en `/impresion/` como en `/impresion/portal/`.
4. **`impresion/document_renderer.py`:**
   * Organiza las facturas en carpetas por fecha real del documento (`facturas/YYYY/MM/DD/FES_NUM.pdf`).
   * Renderiza PDFs nativos utilizando `xhtml2pdf` / `WeasyPrint`.
5. **`services/payload_builder.py`:**
   * Mantiene compatibilidad de zona horaria con fallback `pytz` para Python 3.8.
6. **`services/legacy_repository.py`:**
   * Incluye `from __future__ import annotations` para soporte de anotaciones de tipo en Python 3.8.

---

## 🛠️ Procedimiento de Instalación Paso a Paso

### Paso 1: Copia del Código e Instalación de Oracle Instant Client
1. Copiar la carpeta del proyecto en la ruta raíz estándar:
   `C:\movil_apps\north_comercial`
2. Descargar e instalar **Oracle Instant Client Basic 19c (64-bit)**:
   * Crear la carpeta `C:\oracle`
   * Descomprimir el paquete de Oracle en `C:\oracle\instantclient_19_23`

### Paso 2: Instalación de Python 3.8.10 (64-bit)
1. Descargar el instalador oficial `python-3.8.10-amd64.exe`.
2. Hacer clic derecho -> **Ejecutar como administrador**.
3. **⚠️ IMPORTANTE:** Marcar la casilla **`[X] Add Python 3.8 to PATH`**.
4. Completar la instalación.

### Paso 3: Configuración de Variables de Entorno del Sistema
1. Presionar `Windows + R`, escribir `sysdm.cpl` y presionar Enter.
2. Ir a **Opciones avanzadas** -> **Variables de entorno**.
3. En **Variables del sistema**, editar la variable **`Path`**.
4. Agregar la nueva entrada: `C:\oracle\instantclient_19_23`
5. Guardar los cambios.

### Paso 4: Creación del Entorno Virtual e Instalación de Requerimientos
Abrir **PowerShell** (o CMD) como Administrador y ejecutar:

```powershell
# 1. Entrar a la carpeta del proyecto
cd C:\movil_apps\north_comercial

# 2. Crear el entorno virtual
python -m venv venv

# 3. Activar el entorno virtual
.\venv\Scripts\activate

# 4. Actualizar pip e instalar todas las dependencias estándar
python -m pip install --upgrade pip
pip install -r requirements.txt
```

*(El archivo `requirements.txt` actualizado instala automáticamente Django 4.2, oracledb, python-dotenv, requests, pillow, qrcode, python-docx, docxtpl, django-q2, django-picklefield, waitress, pytz, python-barcode, num2words y xhtml2pdf para renderizado nativo de PDF en Windows).*

### Paso 5: Configuración del Archivo de Entorno (`.env`)
Crear el archivo `C:\movil_apps\north_comercial\.env` especificando los parámetros particulares del cliente:

```ini
# Credenciales Oracle del Cliente (Ejemplo Surtidor / AAA)
ORACLE_USER=USUARIO_BD
ORACLE_PASSWORD=PASSWORD_BD
ORACLE_DSN=192.168.X.X:1521/SID_ORACLE

# Configuración API Binapps (Facturación Electrónica DIAN y PDF)
BINAPPS_AUTH_URL=https://app-erpsso.azurewebsites.net/connect/token
BINAPPS_CLIENT_ID=EDocumentsWebApi
BINAPPS_CLIENT_SECRET=D10DFB2E-F977-44FA-B186-3FA4C0C3FAFB
BINAPPS_INVOICE_URL=https://app-edocumentapi.azurewebsites.net/api/Invoice/Dian/ReleaseInvoice
BINAPPS_CREDIT_NOTE_URL=https://app-edocumentapi.azurewebsites.net/api/Invoice/Dian/CreditNote
```

### Paso 6: Prueba de Conexión a Oracle (`test.py`)
Con el entorno `(venv)` activo en la consola, ejecutar:

```powershell
python test.py
```
*Respuesta esperada:* `CONEXION EXITOSA. FECHA ORACLE: YYYY-MM-DD HH:MM:SS`

### Paso 7: Despliegue Permanente como Servicio de Windows (24/7)

Para que el servidor web quede funcionando de forma continua sin depender de mantener ventanas abiertas:

1. Probar el servidor web `Waitress` manualmente:
   ```powershell
   waitress-serve --port=8000 config.wsgi:application
   ```
2. Registrar la aplicación como **Servicio de Windows** con **NSSM**:
   ```powershell
   # Descargar nssm.exe en C:\oracle o C:\Windows\System32
   nssm install NorthComercial "C:\movil_apps\north_comercial\venv\Scripts\waitress-serve.exe" "--port=8000 --dir=C:\movil_apps\north_comercial config.wsgi:application"
   nssm set NorthComercial AppDirectory "C:\movil_apps\north_comercial"
   nssm start NorthComercial
   ```

### Paso 8: Apertura de Firewall de Windows
Crear una regla de entrada en el Firewall de Windows para permitir el tráfico por el puerto TCP `8000`:
```powershell
netsh advfirewall firewall add rule name="NorthComercial_8000" dir=in action=allow protocol=TCP localport=8000
```

---

## 🌐 Verificación de Acceso de Usuarios

Desde cualquier computador de la red local (comercial/facturación), los empleados podrán ingresar abriendo el navegador en:

👉 **`http://<IP_SERVIDOR_ENLACE>:8000/impresion/`**
