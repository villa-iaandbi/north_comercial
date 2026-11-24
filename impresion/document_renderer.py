import logging
import os
import re
import io
import base64
import qrcode
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from django.conf import settings
from django.template.loader import render_to_string
from core.tenant import get_oracle_connection

logger = logging.getLogger(__name__)

try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError, Exception) as e:
    print(f"Advertencia: WeasyPrint no pudo inicializarse ({e}). Se usará renderizado Dummy.")
    WEASYPRINT_AVAILABLE = False

def dictfetchall(cursor):
    "Return all rows from a cursor as a dict"
    columns = [col[0].lower() for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def build_qr_base64(datos_fac, cufe):
    """
    Construye la cadena base del código QR según el estándar DIAN y genera la imagen en Base64 (formato SVG).
    """
    import qrcode.image.svg
    num_fac = datos_fac.get('num_documento', '')
    
    # Formateo de fecha y hora seguro
    try:
        fch = datos_fac.get('fch_documento')
        fec_fac = fch.strftime('%Y-%m-%d') if fch else ''
        hor_fac = fch.strftime('%H:%M:%S-05:00') if fch else ''
    except AttributeError:
        fec_fac = ''
        hor_fac = ''
        
    nit_fac = "890501170" # NIT Hardcoded temporal de la empresa
    doc_adq = datos_fac.get('num_identificacion', '')
    val_fac = str(datos_fac.get('tot_documento', '0.00'))
    
    # Generar el QR estrictamente con la URL para asegurar compatibilidad con todos los móviles
    qr_string = f"https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={cufe}"
    
    factory = qrcode.image.svg.SvgPathImage
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10, 
        border=4,
    )
    qr.add_data(qr_string)
    qr.make(fit=True)
    
    img = qr.make_image(image_factory=factory)
    svg_bytes = img.to_string()
    
    return base64.b64encode(svg_bytes).decode('utf-8')


import re


def _resolver_id_sistema(cursor, id_sistema='1'):
    """
    ID_SISTEMA efectivo de SG_SISTEMAS. Orden: TRIM(ID_SISTEMA) = <id de sesión>; si no hay fila,
    TRIM(ID_SISTEMA) = '1'; si tampoco, la primera fila por ID_SISTEMA. Devuelve el ID_SISTEMA tal
    cual está grabado (sin TRIM) o '' si la tabla está vacía. Así nunca se pierden NIT, dirección,
    teléfonos ni responsabilidades tributarias por un id de sesión que no coincide.
    """
    pedido = str(id_sistema or '1').strip() or '1'
    candidatos = [pedido] if pedido == '1' else [pedido, '1']
    for candidato in candidatos:
        cursor.execute("SELECT ID_SISTEMA FROM SG_SISTEMAS WHERE TRIM(ID_SISTEMA) = %s", [candidato])
        fila = cursor.fetchone()
        if fila:
            if candidato != pedido:
                logger.warning(f"POS: SG_SISTEMAS sin fila para ID_SISTEMA '{pedido}'; se usa '{candidato}'.")
            return fila[0]
    cursor.execute(
        "SELECT ID_SISTEMA FROM (SELECT ID_SISTEMA FROM SG_SISTEMAS ORDER BY ID_SISTEMA) WHERE ROWNUM <= 1"
    )
    fila = cursor.fetchone()
    if fila:
        logger.warning(f"POS: SG_SISTEMAS sin fila para ID_SISTEMA '{pedido}' ni '1'; se usa '{fila[0]}'.")
        return fila[0]
    return ''


def _fetch_empresa_data(cursor, id_sistema='1'):
    """
    Datos del emisor (SG_SISTEMAS, por defecto id='1'). Misma consulta para la factura masiva
    (_build_context) y para el tiquete POS (obtener_datos_emisor_pos).
    Filtra con TRIM(ID_SISTEMA) y, si el id pedido no existe, cae a '1' y luego a la primera fila.
    Incluye 'id_sistema_real' (el ID_SISTEMA efectivamente leído).
    """
    id_real = _resolver_id_sistema(cursor, id_sistema)
    vacio = {
        'nom_empresa': "", 'nit': "", 'dir': "", 'email': "",
        'obser1': "", 'obser2': "", 'logo_blob': None, 'id_sistema_real': id_real,
    }
    if id_real == '':
        return vacio
    cursor.execute("""
        SELECT NOM_SISTEMA, NIT, DIRECCION, ESLOGAN, OBSER1, OBSER2, LOGO_1 
        FROM SG_SISTEMAS WHERE ID_SISTEMA = %s
    """, [id_real])
    empresa_row = cursor.fetchone()
    if empresa_row:
        return {
            'nom_empresa': empresa_row[0] or "",
            'nit': empresa_row[1] or "",
            'dir': empresa_row[2] or "",
            'email': empresa_row[3] or "",
            'obser1': empresa_row[4] or "",
            'obser2': empresa_row[5] or "",
            'logo_blob': empresa_row[6],
            'id_sistema_real': id_real,
        }
    return vacio


def _resolver_caja_ts(caja_ref, filas):
    """
    Ubica la fila de TS_CAJAS (ID_CAJA, COD_CAJA, DESCRIPCION, ID_TIPO_DOCUMENTO, ID_DOC_CONSECUTIVO)
    que corresponde a lo que envía el POS. Acepta ID_CAJA, COD_CAJA, DESCRIPCION o variantes
    como 'CAJA-01', 'Caja 1', '01' o '1'. Prioridad: coincidencia exacta (ID_CAJA, COD_CAJA,
    DESCRIPCION) y luego coincidencia numérica (COD_CAJA, ID_CAJA, número dentro de DESCRIPCION).
    """
    ref = re.sub(r'\s+', ' ', str(caja_ref or '')).strip().upper()
    if not ref:
        return None
    m = re.search(r'(\d+)\s*$', ref)
    numero = int(m.group(1)) if m else None

    def txt(v):
        return re.sub(r'\s+', ' ', str(v if v is not None else '')).strip().upper()

    def num(v):
        return int(v) if re.fullmatch(r'\d+', txt(v)) else None

    def num_desc(v):
        mm = re.search(r'\b(\d+)\b', txt(v))
        return int(mm.group(1)) if mm else None

    filas = list(filas or [])
    pruebas = [
        lambda f: txt(f[0]) == ref,
        lambda f: txt(f[1]) == ref,
        lambda f: txt(f[2]) == ref,
    ]
    if numero is not None:
        pruebas += [
            lambda f: num(f[1]) == numero,
            lambda f: num(f[0]) == numero,
            lambda f: num_desc(f[2]) == numero,
        ]
    for prueba in pruebas:
        for fila in filas:
            if prueba(fila):
                return fila
    return None


def _fmt_fecha_dd_mm_yyyy(valor):
    """DATE de Oracle (datetime/date) -> 'DD/MM/YYYY'; '' si viene nulo o no es fecha."""
    try:
        return valor.strftime('%d/%m/%Y') if valor else ''
    except Exception:
        return ''


def _a_entero(valor):
    """NUMBER de Oracle -> int; None si viene nulo o no es numérico."""
    if valor is None or valor == '':
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _texto_resolucion_dian(num_resolucion, fch_expedicion, fch_vencimiento, prefijo,
                           rango_inicial, rango_final):
    """
    Texto de resolución para el tiquete térmico 80mm, omitiendo lo que venga nulo. Ej.:
    'Res. DIAN No. 18764000001 del 01/01/2025 al 01/01/2027 Prefijo SETT del 1 al 5000'
    Las fechas se esperan ya formateadas (DD/MM/YYYY) o vacías.
    """
    texto = f"Res. DIAN No. {num_resolucion}"
    if fch_expedicion and fch_vencimiento:
        texto += f" del {fch_expedicion} al {fch_vencimiento}"
    elif fch_expedicion:
        texto += f" del {fch_expedicion}"
    elif fch_vencimiento:
        texto += f" vigente hasta {fch_vencimiento}"
    if prefijo:
        texto += f" Prefijo {prefijo}"
    if rango_final and rango_final > 0:
        texto += f" del {rango_inicial if rango_inicial is not None else 1} al {rango_final}"
    return texto


def obtener_datos_emisor_pos(caja_id=None, id_sistema='1'):
    """
    Datos fiscales del emisor para el tiquete POS, con la misma fuente que la facturación masiva:
      - SG_SISTEMAS (nombre, NIT, dirección, teléfono si existe, OBSER1 = responsabilidades tributarias)
        filtrado por id_sistema (por defecto '1').
      - TS_CAJAS (PosTurno.caja_id puede traer 'CAJA-01', ID_CAJA, COD_CAJA o DESCRIPCION; ver _resolver_caja_ts):
        COD_CAJA, ID_TIPO_DOCUMENTO, ID_DOC_CONSECUTIVO y la resolución DIAN propia de la caja
        (NUM_RESOLUCION, FCH_EXPEDICION, FCH_VENCIMIENTO, PREFIJO, RANGO_INICIAL, RANGO_FINAL, CLAVE_TECNICA).
      - Resolución DIAN: si TS_CAJAS.NUM_RESOLUCION tiene valor se arma el texto con esas columnas.
        Respaldo: CO_DOC_CONSECUTIVOS.OBSER del ID_DOC_CONSECUTIVO de la caja (filtrado por
        ID_SISTEMA y el ID_ANO más reciente) y, si tampoco hay OBSER, RESOLUCION(<ID_TIPO_DOCUMENTO>).
      - Prefijo: TS_CAJAS.PREFIJO tiene prioridad sobre CO_DOC_CONSECUTIVOS.PREFIJO.
    Nunca lanza excepciones: ante cualquier fallo devuelve los campos disponibles.
    """
    id_sistema = str(id_sistema or '1').strip() or '1'
    datos = {'nom_sistema': '', 'nit_emp': '', 'dir_emp': '', 'tels_emp': '',
             'contribuyente': '', 'resolucion': '', 'cod_caja': '', 'prefijo_caja': '',
             'prefijo': '', 'num_resolucion': '', 'fch_expedicion': '', 'fch_vencimiento': '',
             'rango_inicial': None, 'rango_final': None, 'clave_tecnica': '',
             'texto_encabezado': '', 'texto_footer': ''}
    try:
        with get_oracle_connection().cursor() as cursor:
            emp = _fetch_empresa_data(cursor, id_sistema)
            datos['nom_sistema'] = emp.get('nom_empresa') or ''
            datos['nit_emp'] = str(emp.get('nit') or '')
            datos['dir_emp'] = emp.get('dir') or ''
            datos['contribuyente'] = emp.get('obser1') or ''

            # Teléfono del emisor: columna opcional; la factura masiva espera la llave 'tels'.
            try:
                cursor.execute(
                    "SELECT TELS FROM SG_SISTEMAS WHERE ID_SISTEMA = %s",
                    [emp.get('id_sistema_real') or id_sistema],
                )
                row = cursor.fetchone()
                datos['tels_emp'] = (row[0] or '') if row else ''
            except Exception as exc:
                # SG_SISTEMAS no tiene columna TELS en este esquema (los teléfonos viajan dentro de DIRECCION).
                logger.debug(f"POS: SG_SISTEMAS.TELS no disponible (id_sistema '{emp.get('id_sistema_real')}'): {exc}")
                datos['tels_emp'] = ''

            # Textos configurables del tiquete: BAPP_TX7 (encabezado) y BAPP_TX8 (pie).
            try:
                id_sys_resuelto = emp.get('id_sistema_real') or id_sistema
                cursor.execute(
                    "SELECT SUBSTR(PARAMETRO_CAR('BAPP_TX7', SYSDATE, %s), 1, 500), "
                    "SUBSTR(PARAMETRO_CAR('BAPP_TX8', SYSDATE, %s), 1, 500) FROM DUAL",
                    (id_sys_resuelto, id_sys_resuelto),
                )
                fila_tx = cursor.fetchone()
                if fila_tx:
                    datos['texto_encabezado'] = str(fila_tx[0] or '').strip()
                    datos['texto_footer'] = str(fila_tx[1] or '').strip()
            except Exception as exc:
                logger.warning(f"POS: no se pudieron leer BAPP_TX7/BAPP_TX8: {exc}")
                datos['texto_encabezado'] = ''
                datos['texto_footer'] = ''

            caja_ref = str(caja_id or '').strip()
            if caja_ref:
                try:
                    cursor.execute(
                        "SELECT ID_CAJA, COD_CAJA, DESCRIPCION, ID_TIPO_DOCUMENTO, ID_DOC_CONSECUTIVO, "
                        "NUM_RESOLUCION, FCH_EXPEDICION, FCH_VENCIMIENTO, PREFIJO, "
                        "RANGO_INICIAL, RANGO_FINAL, CLAVE_TECNICA "
                        "FROM TS_CAJAS"
                    )
                    fila = _resolver_caja_ts(caja_ref, cursor.fetchall())
                    if fila:
                        (_id_caja, cod_caja, _descripcion, id_tipo_doc, id_doc_consec,
                         num_res, fch_exp, fch_ven, prefijo_ts,
                         rango_ini, rango_fin, clave_tecnica) = fila
                        datos['cod_caja'] = str(cod_caja or '').strip()
                        datos['num_resolucion'] = str(num_res or '').strip()
                        datos['fch_expedicion'] = _fmt_fecha_dd_mm_yyyy(fch_exp)
                        datos['fch_vencimiento'] = _fmt_fecha_dd_mm_yyyy(fch_ven)
                        datos['rango_inicial'] = _a_entero(rango_ini)
                        datos['rango_final'] = _a_entero(rango_fin)
                        datos['clave_tecnica'] = str(clave_tecnica or '').strip()
                        prefijo_ts = str(prefijo_ts or '').strip().upper()

                        # Respaldo desde CO_DOC_CONSECUTIVOS (OBSER y PREFIJO).
                        obser_consec, prefijo_consec = '', ''
                        if id_doc_consec:
                            cursor.execute(
                                "SELECT OBSER, PREFIJO FROM ("
                                "  SELECT OBSER, PREFIJO FROM CO_DOC_CONSECUTIVOS "
                                "  WHERE ID_DOC_CONSECUTIVO = %s AND ID_SISTEMA = %s "
                                "  ORDER BY ID_ANO DESC"
                                ") WHERE ROWNUM <= 1",
                                [id_doc_consec, id_sistema],
                            )
                            res = cursor.fetchone()
                            if res:
                                obser_consec = str(res[0] or '').strip()
                                prefijo_consec = str(res[1] or '').strip()

                        datos['prefijo'] = prefijo_ts or prefijo_consec
                        datos['prefijo_caja'] = datos['prefijo']

                        if datos['num_resolucion']:
                            datos['resolucion'] = _texto_resolucion_dian(
                                datos['num_resolucion'], datos['fch_expedicion'],
                                datos['fch_vencimiento'], datos['prefijo'],
                                datos['rango_inicial'], datos['rango_final'],
                            )
                        else:
                            datos['resolucion'] = obser_consec
                            if not datos['resolucion'] and id_tipo_doc:
                                cursor.execute("SELECT RESOLUCION(%s) FROM DUAL", [id_tipo_doc])
                                res = cursor.fetchone()
                                datos['resolucion'] = str((res[0] if res else '') or '').strip()
                    else:
                        print(f"POS: la caja '{caja_ref}' no corresponde a ninguna fila de TS_CAJAS.")
                except Exception as exc:
                    print(f"POS: no se pudo obtener la caja/resolución DIAN de la caja {caja_ref}: {exc}")
    except Exception as exc:
        logger.error(f"POS: no se pudieron leer los datos del emisor en Oracle: {exc}")
    return datos


CONSUMIDOR_FINAL_NIT = '222222222222'


def obtener_tercero_pos(id_tercero=None):
    """
    Datos reales del adquirente desde CO_TERCEROS (mismos campos y funciones que
    services/legacy_repository.get_doors_invoice_data). `id_tercero` puede ser el ID_TERCERO o el
    COD_TERCERO (NIT); si viene vacío se usa el consumidor final 222222222222.

    Retorna dict con: nit, digito_verificacion, nom_tercero, razon_social, nombre_comercial, direccion,
    id_municipio_dian, municipio_nombre, departamento_nombre, telefono, email, regimen.
    Si Oracle no responde o no existe el tercero, devuelve lo mínimo con el NIT recibido.
    """
    from services.legacy_repository import calculate_dv
    ref = str(id_tercero or '').strip() or CONSUMIDOR_FINAL_NIT
    datos = {
        'nit': ref, 'digito_verificacion': calculate_dv(ref), 'nom_tercero': '', 'razon_social': '',
        'nombre_comercial': '', 'direccion': '', 'id_municipio_dian': '', 'municipio_nombre': '',
        'departamento_nombre': '', 'telefono': '', 'email': '', 'regimen': '2', 'encontrado': False,
    }

    def _consultar(valor):
        with get_oracle_connection().cursor() as cursor:
            cursor.execute(
                "SELECT COD_TERCERO, NOM_TERCERO, NOM_NEGOCIO, E_MAIL, DIR, TELS, ID_MUNICIPIO_DIAN, "
                "       ID_REGIMEN, NOM_DEPTO(SUBSTR(ID_MUNICIPIO, 1, 2)), NOM_MUNICIPIO_DIAN(ID_MUNICIPIO_DIAN) "
                "FROM ("
                "  SELECT * FROM CO_TERCEROS WHERE ID_TERCERO = %s OR COD_TERCERO = %s "
                "  ORDER BY CASE WHEN ID_TERCERO = %s THEN 0 ELSE 1 END"
                ") WHERE ROWNUM <= 1",
                [valor, valor, valor],
            )
            return cursor.fetchone()

    try:
        row = _consultar(ref)
        if not row and ref != CONSUMIDOR_FINAL_NIT:
            logger.warning(f"POS: tercero '{ref}' no existe en CO_TERCEROS; se usa el consumidor final.")
            ref = CONSUMIDOR_FINAL_NIT
            row = _consultar(ref)
        if row:
            nit = str(row[0] or ref).strip()
            nombre = re.sub(r'\s+', ' ', str(row[1] or '')).strip()
            datos.update({
                'nit': nit,
                'digito_verificacion': calculate_dv(nit),
                'nom_tercero': nombre,
                'razon_social': nombre,
                'nombre_comercial': re.sub(r'\s+', ' ', str(row[2] or '')).strip() or nombre,
                'email': str(row[3] or '').strip(),
                'direccion': str(row[4] or '').strip(),
                'telefono': str(row[5] or '').strip(),
                'id_municipio_dian': str(row[6] or '').strip(),
                'regimen': str(row[7]) if row[7] else '2',
                'departamento_nombre': str(row[8] or '').strip(),
                'municipio_nombre': str(row[9] or '').strip(),
                'encontrado': True,
            })
        else:
            datos['nit'] = ref
            datos['digito_verificacion'] = calculate_dv(ref)
    except Exception as exc:
        logger.warning(f"POS: no se pudo leer el tercero '{ref}' en CO_TERCEROS: {exc}")
    return datos


def _build_context(id_documento):
    """
    Construye el contexto exacto para la plantilla HTML consultando la base de datos de Oracle.
    Extrae dinámicamente datos de empresa, parámetros, y cruza detalles con impuestos.
    """
    with get_oracle_connection().cursor() as cursor:
        # 0. Datos de la Empresa (SG_SISTEMAS id='1')
        empresa_data = _fetch_empresa_data(cursor)

        # 0.1 Parámetros Generales (SG_PARAMETROS)
        cursor.execute("SELECT ID_PARAMETRO, NOM_PARAMETRO, VLR_CHR FROM SG_PARAMETROS")
        params_db = {}
        for row in cursor.fetchall():
            val = row[2] or ""
            if row[0]: params_db[row[0].upper().strip()] = val
            if row[1]: params_db[row[1].upper().strip()] = val

        # 1. Cabecera, Cliente y Ventas
        cursor.execute("""
            SELECT 
                doc.ID_DOCUMENTO, doc.NUM_DOCUMENTO, doc.FCH_DOCUMENTO, doc.TOT_DOCUMENTO, doc.OBSER as OBSERVACIONES,
                NOMBRE_CORTO(doc.ID_RESPONSABLE) AS ELABORADO_POR,
                NIT_TERCERO(ter.ID_TERCERO) AS NIT_TERCERO, ter.NOM_TERCERO, ter.DIR, ter.TELS, NVL(NOM_MUNICIPIO_DIAN(ter.ID_MUNICIPIO), NVL(NOM_MUNICIPIO(ter.ID_MUNICIPIO), ter.ID_MUNICIPIO)) AS MUNICIPIO, ter.DIR2 AS BARRIO, ter.NOM_NEGOCIO,
                fel.CUFE,
                ven.PLAZO_PAGO AS PLAZO, ven.CONDICIONES_PAGO AS ID_FORMA_PAGO,
                ven.TOT_MERCANCIA, ven.TOT_IVA,
                (VENDEDOR(doc.ID_VENDEDOR,'1') || '/' || NOMBRE_CORTO(doc.ID_VENDEDOR) || '/cel:' || SUBSTR(DIRTEL_PERSONA(doc.ID_VENDEDOR,'T'), 1, 12)) AS NOM_VENDEDOR,
                RESOLUCION(doc.ID_TIPO_DOCUMENTO) AS RESOLUCION_TEXT,
                NVL(CAJAS_DOC(doc.ID_DOCUMENTO), 0) AS TOTAL_CAJAS,
                NVL(PESO_DOC(doc.ID_DOCUMENTO), 0) AS TOTAL_PESO
            FROM CO_DOCUMENTOS doc
            LEFT JOIN CO_TERCEROS ter ON doc.ID_TERCERO = ter.ID_TERCERO
            LEFT JOIN CT_VENTAS_FEL fel ON doc.ID_DOCUMENTO = fel.ID_DOCUMENTO
            LEFT JOIN CT_VENTAS ven ON doc.ID_DOCUMENTO = ven.ID_DOCUMENTO
            WHERE doc.ID_DOCUMENTO = %s
        """, [id_documento])
        header_rows = dictfetchall(cursor)
        
        if not header_rows:
            raise ValueError(f"Documento no encontrado: {id_documento}")
            
        header = header_rows[0]

        # 1.1 Total Items (MAX id_item)
        cursor.execute("""
            SELECT NVL(MAX(TO_NUMBER(ID_ITEM)), 0)
            FROM IN_MOV_INVENTARIOS
            WHERE ID_DOCUMENTO = %s
        """, [id_documento])
        max_id_item_row = cursor.fetchone()
        max_id_item = str(max_id_item_row[0]) if max_id_item_row else "0"
        
        # 2. Detalles de la Factura (Ítems)
        cursor.execute("""
            SELECT 
                itm.REFERENCIA,
                itm.NOM_ARTICULO AS DESCRIPCION,
                lin.CANTIDAD, 
                lin.VLR_UNITARIO, 
                (lin.CANTIDAD * lin.VLR_UNITARIO) AS VLR_TOTAL,
                lin.ID_UNIDAD_MEDIDA AS UND_VTA,
                lin.VLR_IVA,
                lin.IMPOCONSUMO,
                CANTIDAD_UNIDAD(lin.ID_ARTICULO, NVL(lin.CANTIDAD, 1), 'G') AS CAJAS_CALCULADAS,
                CANTIDAD_UNIDAD(lin.ID_ARTICULO, NVL(lin.CANTIDAD, 1), 'GP') AS UNIDADES_CALCULADAS
            FROM IN_MOV_INVENTARIOS lin
            LEFT JOIN IN_ARTICULOS itm ON lin.ID_ARTICULO = itm.ID_ARTICULO
            WHERE lin.ID_DOCUMENTO = %s
            ORDER BY lin.ID_ARTICULO ASC
        """, [id_documento])
        items_db = dictfetchall(cursor)

    # Convertir LOGO a Base64 desde el archivo local (priorizando logo_surtidor.png)
    logo_base64 = ""
    try:
        ruta_logo = os.path.join(settings.BASE_DIR, 'templates', 'logo_surtidor.png')
        if not os.path.exists(ruta_logo):
            ruta_logo = os.path.join(settings.BASE_DIR, 'templates', 'logo_credito.jpeg')
        with open(ruta_logo, "rb") as image_file:
            logo_base64 = base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        logo_base64 = ""

    # Extracción de prefijo y número limpio
    num_doc = header.get('num_documento') or ''
    match = re.match(r"([A-Za-z]+)(\d+)", num_doc)
    if match:
        prefijo = match.group(1)
        num_doc_limpio = match.group(2)
    else:
        match_alpha = re.match(r"([A-Za-z]+)", num_doc)
        prefijo = match_alpha.group(1) if match_alpha else ""
        num_doc_limpio = num_doc[len(prefijo):] if prefijo else num_doc

    # Formateo de fechas
    fch = header.get('fch_documento')
    fecha_formateada = fch.strftime('%d-%b-%Y').upper() if fch else ''
    
    plazo = header.get('plazo') or 0
    vencimiento_formateado = ""
    if fch:
        try:
            vencimiento_formateado = (fch + timedelta(days=int(plazo))).strftime('%d-%b-%Y').upper()
        except (ValueError, TypeError):
            vencimiento_formateado = fecha_formateada

    # Helpers de formato y cantidad para valores que sean vacíos si son 0
    def fmt(val):
        try:
            fval = float(val)
            if fval == 0.0:
                return ""
            return "{:,.2f}".format(fval)
        except (ValueError, TypeError):
            return ""

    def fmt_qty(val):
        try:
            fval = float(val)
            if fval == 0.0:
                return ""
            if fval.is_integer():
                return str(int(fval))
            return "{:,.2f}".format(fval)
        except (ValueError, TypeError):
            return ""

    # Totales y Cifras Globales
    subtotal_fmt = fmt(header.get('tot_mercancia') or header.get('tot_documento', 0))
    total_iva_fmt = fmt(header.get('tot_iva') or 0)
    total_pagar_float = float(header.get('tot_documento') or 0)
    total_pagar_fmt = fmt(total_pagar_float)
    
    # Preparar el string de vendedor
    vendedor_str = header.get('nom_vendedor') or ''

    # QR DIAN
    cufe_str = header.get('cufe') or ''
    header_qr = dict(header)
    header_qr['num_identificacion'] = header_qr.get('nit_tercero', '')
    qr_base64_img = build_qr_base64(header_qr, cufe_str)

    # Código de Barras
    barcode_base64 = ""
    try:
        import barcode
        from barcode.writer import ImageWriter
        codigo = f"{prefijo}{num_doc_limpio}".strip()
        bc = barcode.get('code128', codigo, writer=ImageWriter())
        buffer_bc = io.BytesIO()
        bc.write(buffer_bc, options={"write_text": False, "module_width": 0.2, "module_height": 5, "quiet_zone": 1})
        barcode_base64 = base64.b64encode(buffer_bc.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"Error generando barcode: {e}")

    # Valor en Letras
    valor_en_letras = ""
    if total_pagar_float > 0:
        try:
            from num2words import num2words
            valor_en_letras = num2words(total_pagar_float, lang='es').upper() + " PESOS"
        except ImportError:
            valor_en_letras = "CANTIDAD EN LETRAS NO DISPONIBLE"
        except Exception:
            pass

    # Procesar Ítems y Calcular Bases
    items_list = []
    sum_impoconsumo = 0.0
    tot_excluida = 0.0
    tot_base_5 = 0.0
    tot_base_19 = 0.0
    
    for item in items_db:
        vlr_total_linea = float(item.get('vlr_total') or 0)
        vlr_iva = float(item.get('vlr_iva') or 0)
        vlr_impo = float(item.get('impoconsumo') or 0)
        
        sum_impoconsumo += vlr_impo
        
        porc_iva = round((vlr_iva / vlr_total_linea) * 100, 1) if vlr_total_linea > 0 else 0
        porc_iva_fmt = f"{int(round(porc_iva))}%" if porc_iva > 0 else ""
        
        # Calcular bases
        if porc_iva == 0:
            tot_excluida += vlr_total_linea
        elif 4.0 <= porc_iva <= 6.0:
            tot_base_5 += vlr_total_linea
        elif 18.0 <= porc_iva <= 20.0:
            tot_base_19 += vlr_total_linea
        
        cajas_calc = float(item.get('cajas_calculadas') or 0)
        unidades_calc = float(item.get('unidades_calculadas') or 0)
        cantidad_linea = float(item.get('cantidad') or 0)
        und_vta_str = str(item.get('und_vta') or '').upper().strip()

        if cajas_calc > 0:
            cajas_str = fmt_qty(cajas_calc)
        elif cantidad_linea > 0 and und_vta_str in ['CAJA', 'CAJAS', 'CJ', 'CJS']:
            cajas_str = fmt_qty(cantidad_linea)
        else:
            cajas_str = ""

        if unidades_calc > 0:
            unidades_str = fmt_qty(unidades_calc)
        elif cantidad_linea > 0 and (cajas_str == "" or und_vta_str not in ['CAJA', 'CAJAS', 'CJ', 'CJS']):
            unidades_str = fmt_qty(cantidad_linea)
        else:
            unidades_str = ""

        items_list.append({
            'referencia': item.get('referencia') or '',
            'descripcion': item.get('descripcion') or '',
            'cajas': cajas_str,
            'unidades': unidades_str, 
            'und_vta': item.get('und_vta') or '',
            'cantidad': fmt_qty(item.get('cantidad') or 0),
            'vlr_unitario': fmt(item.get('vlr_unitario')),
            'porc_iva': porc_iva_fmt,
            'impoconsumo': fmt(vlr_impo),
            'vlr_total': fmt(vlr_total_linea)
        })

    # Total Kilos (PESO_DOC)
    total_peso_float = float(header.get('total_peso') or 0)
    total_kilos_str = str(int(total_peso_float)) if total_peso_float.is_integer() else str(total_peso_float)
    if total_peso_float == 0.0:
        total_kilos_str = "0"

    # Construcción final del diccionario de contexto con llaves 100% en minúsculas
    contexto = {
        'nom_empresa': empresa_data.get('nom_empresa') or '',
        'nit_empresa': empresa_data.get('nit') or '',
        'dir_empresa': empresa_data.get('dir') or '',
        'tel_empresa': empresa_data.get('tels') or '',
        'email_empresa': empresa_data.get('email') or '',
        'logo_base64': logo_base64,
        'tipo_contribuyente': empresa_data['obser1'],
        'contribuyente': empresa_data.get('obser1') or '',
        
        'prefijo': prefijo,
        'fecha_documento_raw': fch,
        'num_documento': num_doc_limpio,
        'qr_base64': qr_base64_img,
        'barcode_base64': barcode_base64,
        
        'nom_cliente': header.get('nom_tercero') or "",
        'nit_cliente': header.get('nit_tercero') or "",
        'fch_factura': fecha_formateada,
        'dir_cliente': header.get('dir') or "",
        'municipio_cliente': header.get('municipio') or "",
        'fch_vencimiento': vencimiento_formateado,
        'tel_cliente': header.get('tels') or "",
        'barrio_cliente': header.get('barrio') or "",
        'forma_pago': "CRÉDITO" if str(header.get('id_forma_pago')) == '2' else "CONTADO",
        'nom_negocio': header.get('nom_negocio') or "",
        'vendedor': vendedor_str,
        'plazo_dias': plazo,
        'observaciones': header.get('observaciones') or "",
        
        'subtotal': subtotal_fmt,
        'total_iva': total_iva_fmt,
        'tot_impoconsumo': fmt(sum_impoconsumo),
        'total_pagar': total_pagar_fmt,
        
        'tot_excluida': fmt(tot_excluida),
        'tot_base_5': fmt(tot_base_5),
        'tot_base_19': fmt(tot_base_19),
        
        'items': items_list,
        'total_items': max_id_item,
        'valor_en_letras': valor_en_letras,
        'cufe': cufe_str,
        
        'texto_autorizacion': params_db.get('BAPP_TX3', ''),
        'texto_pagare': params_db.get('BAPP_TX4', ''),
        'mensaje_comercial': params_db.get('BAPP_TX4', ''),
        'texto_pie_productos': params_db.get('BAPP_TX5', ''),
        'texto_cuentas': empresa_data['obser2'],
        
        # Mantenemos estas por compatibilidad temporal si la plantilla las usa
        'texto_footer_1': params_db.get('BAPP_TX3', ''),
        'texto_footer_2': params_db.get('BAPP_TX4', ''),
        'texto_footer_3': empresa_data['obser2'],
        'texto_footer_4': params_db.get('BAPP_TX4', ''),
        
        'elaborado_por': header.get('elaborado_por') or "",
        'entregado_por': "",
        'usuario_imprime': "",
        'total_cajas_factura': fmt_qty(header.get('total_cajas', 0)),
        'total_kilos': total_kilos_str,
        'resolucion_dian_completa': header.get('resolucion_text') or '',
        
        'nom_software': params_db.get('NOM_SOFTWARE', 'EDocuments'),
        'desarrollador_software': params_db.get('DESARROLLADOR_SOFTWARE', 'BINAPPS'),
        'nit_desarrollador': params_db.get('NIT_DESARROLLADOR', '900619134')
    }
    
    return contexto, num_doc

# ---------------------------------------------------------------------------
# Rutas de salida multi-tenant: facturas/<PREFIJO>/<tipo>/<AAAA>/<MM>/<DD>/
# ---------------------------------------------------------------------------
TIPOS_DOCUMENTO_VALIDOS = ('pos', 'credito')
PREFIJO_FALLBACK = 'GENERAL'


def _now_colombia():
    """Fecha/hora actual en hora de Colombia (naive), usada como respaldo."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Bogota")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow() - timedelta(hours=5)


def _sanitize_prefijo(prefijo_numeracion):
    """Mayúsculas y solo [A-Z0-9_-]; si queda vacío retorna 'GENERAL'."""
    limpio = re.sub(r'[^A-Z0-9_-]', '', str(prefijo_numeracion or '').strip().upper())
    return limpio or PREFIJO_FALLBACK


def _sanitize_tipo(tipo):
    tipo = str(tipo or '').strip().lower()
    return tipo if tipo in TIPOS_DOCUMENTO_VALIDOS else 'credito'


def _get_facturas_base_dir():
    """
    Ruta base absoluta para las facturas: settings.MEDIA_ROOT si está configurado,
    de lo contrario settings.BASE_DIR. Nunca depende del cwd.
    """
    base_dir = Path(str(settings.BASE_DIR)).resolve()
    media_root = str(getattr(settings, 'MEDIA_ROOT', '') or '').strip()
    if media_root:
        root = Path(media_root)
        if not root.is_absolute():
            root = base_dir / root
        return root / 'facturas'
    return base_dir / 'facturas'


def _coerce_date(value):
    """Convierte datetime/date (o string ISO simple) a datetime; None si no es posible."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str) and value.strip():
        txt = value.strip()
        for fmt_ in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                return datetime.strptime(txt[:19] if '%H' in fmt_ else txt[:10], fmt_)
            except ValueError:
                continue
    return None


def _build_output_dir(prefijo_numeracion, tipo, fecha=None):
    """Crea y retorna facturas/<PREFIJO>/<tipo>/<AAAA>/<MM>/<DD>/ bajo la base anclada."""
    fecha = _coerce_date(fecha) or _now_colombia()
    out_dir = (
        _get_facturas_base_dir()
        / _sanitize_prefijo(prefijo_numeracion)
        / _sanitize_tipo(tipo)
        / f"{fecha.year:04d}" / f"{fecha.month:02d}" / f"{fecha.day:02d}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir)


def render_invoice_to_pdf(id_documento, prefijo_numeracion=None, tipo='credito'):
    """
    Genera físicamente el PDF de una factura utilizando Weasyprint y retorna la ruta del archivo.
    Aplica la lógica del Código QR requerida.

    Guarda en: facturas/<prefijo_numeracion>/<tipo>/<AAAA>/<MM>/<DD>/FES_<num>.pdf
    Si no se indica prefijo_numeracion se usa el prefijo del propio documento (o 'GENERAL').
    """
    context, num_documento = _build_context(id_documento)

    # 5. Renderizar la plantilla a STRING
    html_string = render_to_string('plantilla_factura_weasyprint.html', context)

    # 6. Generar estructura de directorios (anclada a MEDIA_ROOT / BASE_DIR)
    prefijo = prefijo_numeracion or context.get('prefijo') or PREFIJO_FALLBACK
    media_dir = _build_output_dir(prefijo, tipo, context.get('fecha_documento_raw'))

    pdf_filename = f"FES_{num_documento}.pdf"
    file_path = os.path.join(media_dir, pdf_filename)

    # 7. Salida a disco
    if WEASYPRINT_AVAILABLE:
        try:
            HTML(string=html_string).write_pdf(file_path)
            if os.path.exists(file_path) and os.path.getsize(file_path) > 1000:
                return file_path
        except Exception as e:
            print(f"Error nativo WeasyPrint: {str(e)}")

    # 2. Intentar xhtml2pdf (pisa) como motor de alto rendimiento para Windows Server
    try:
        from xhtml2pdf import pisa
        import re
        clean_html = re.sub(r'@bottom-center\s*\{[\s\S]*?\}', '', html_string)
        clean_html = clean_html.replace('<span class="page">1</span>', '<pdf:pagenumber>')
        clean_html = clean_html.replace('<span class="pages">1</span>', '<pdf:pagecount>')
        clean_html = clean_html.replace('<span class="page"></span>', '<pdf:pagenumber>')
        clean_html = clean_html.replace('<span class="pages"></span>', '<pdf:pagecount>')
        clean_html = clean_html.replace('<pdf:pagenumber/>', '<pdf:pagenumber>')
        clean_html = clean_html.replace('<pdf:pagecount/>', '<pdf:pagecount>')
        
        with open(file_path, 'wb') as f:
            pisa_status = pisa.CreatePDF(src=clean_html, dest=f)
            
        if os.path.exists(file_path) and os.path.getsize(file_path) > 2000:
            print(f"PDF real generado exitosamente ({os.path.getsize(file_path)} bytes).")
            return file_path
    except Exception as e:
        print(f"Excepción en xhtml2pdf: {e}")

    _generate_dummy_pdf(file_path, num_documento, html_string)
    return file_path

def _generate_dummy_pdf(file_path, num_doc, html_string):
    """Fallback por si falla WeasyPrint en el servidor. Crea un PDF en blanco y el HTML real para inspección."""
    # 1. Crear el PDF Dummy
    with open(file_path, 'wb') as f:
        f.write(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000056 00000 n\n0000000111 00000 n\ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF")
        
    # 2. Guardar el archivo HTML para que el usuario pueda visualizar cómo quedó el diseño
    html_path = file_path.replace('.pdf', '.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_string)


def _to_decimal(val):
    """Convierte números / strings numéricos a Decimal; vacío o inválido -> 0."""
    if isinstance(val, Decimal):
        return val
    if val is None or val == '':
        return Decimal('0')
    try:
        return Decimal(str(val).strip().replace(',', ''))
    except (InvalidOperation, ValueError):
        return Decimal('0')


def _fmt_money(val):
    """Valor monetario sin decimales (redondeo half-up) y con punto como separador de miles: 74.836."""
    entero = int(_to_decimal(val).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return f"{entero:,}".replace(',', '.')


def _fmt_money_opt(val):
    """Como _fmt_money pero devuelve '' si el valor redondeado es 0 (campos opcionales)."""
    d = _to_decimal(val).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return '' if d == 0 else _fmt_money(d)


def _fmt_qty_pos(val):
    """Cantidad: entera sin decimales (2, no 2.00); fraccionaria con coma decimal (1,5)."""
    d = _to_decimal(val)
    if d == d.to_integral_value():
        return str(int(d))
    return f"{d.quantize(Decimal('0.01')).normalize():f}".replace('.', ',')


# --- Código de gravamen del tiquete POS (A/B/C fijos; otras tarifas/INC reciben D, E, ...) ---
_GRAVAMEN_FIJOS = {('IVA', 0.0): 'A', ('IVA', 5.0): 'B', ('IVA', 19.0): 'C'}


def _clave_gravamen(porc, tipo=None):
    tipo = str(tipo or 'IVA').strip().upper() or 'IVA'
    return (tipo, round(float(_to_decimal(porc)), 2))


def _label_tarifa(clave):
    tipo, porc = clave
    if tipo == 'INC' and not porc:
        return 'INC'  # impoconsumo de valor fijo por unidad
    pct = str(int(porc)) if float(porc).is_integer() else f"{porc:g}".replace('.', ',')
    return f"{tipo} {pct}%"


def _asignar_codigos_gravamen(claves):
    """Mapa {clave: letra}. A/B/C fijos para IVA 0/5/19; el resto en orden desde 'D' sin repetir."""
    codigos = {k: _GRAVAMEN_FIJOS[k] for k in claves if k in _GRAVAMEN_FIJOS}
    usadas = set(_GRAVAMEN_FIJOS.values())
    siguiente = ord('D')
    for k in sorted(set(claves) - set(codigos)):
        while chr(siguiente) in usadas and siguiente < ord('Z'):
            siguiente += 1
        codigos[k] = chr(siguiente)
        usadas.add(chr(siguiente))
    return codigos


def _limpiar_descripcion(texto):
    """Solo normaliza espacios; el ancho útil lo aprovecha CSS (nowrap + overflow hidden), sin recorte en Python."""
    return re.sub(r'\s+', ' ', str(texto or '')).strip()


def _preparar_datos_tiquete(data):
    """
    Normaliza ticket_data para la plantilla: ítems en 2 renglones con código de gravamen,
    tabla de impuestos con equivalencia (A: IVA 0%) y fila TOTAL, y todos los valores
    monetarios sin decimales. Los valores numéricos llegan crudos desde pos/views.py.
    """
    # Tabla de impuestos de respaldo (llaves tot_excluida / tot_mercancia2 / tot_mercancia3)
    impuestos_in = list(data.get('impuestos') or [])
    if not impuestos_in:
        for porc, base_k, val_k in ((5, 'tot_mercancia2', 'tot_iva2'), (19, 'tot_mercancia3', 'tot_iva3')):
            if _to_decimal(data.get(base_k)):
                impuestos_in.append({'porc': porc, 'base': data.get(base_k), 'valor': data.get(val_k)})
        if _to_decimal(data.get('tot_excluida')):
            impuestos_in.insert(0, {'porc': 0, 'base': data['tot_excluida'], 'valor': 0})

    items_in = list(data.get('items') or [])
    claves = [_clave_gravamen(i.get('porc_iva'), i.get('tipo_impuesto')) for i in items_in]
    claves += [k for k in (_clave_gravamen(i.get('porc'), i.get('tipo')) for i in impuestos_in)
               if k != ('INC', 0.0)]
    codigos = _asignar_codigos_gravamen(claves)

    items = []
    for it in items_in:
        clave = _clave_gravamen(it.get('porc_iva'), it.get('tipo_impuesto'))
        items.append({
            'cantidad': _fmt_qty_pos(it.get('cantidad')),
            'referencia': str(it.get('referencia') or it.get('id_articulo') or '')[:10],
            'vlr_unitario': _fmt_money(it.get('vlr_unitario')),
            'vlr_total': _fmt_money(it.get('vlr_total')),
            'cod': codigos[clave],
            'nom_articulo': _limpiar_descripcion(it.get('nom_articulo')),
            'descuento': _fmt_money(it.get('descuento')) if _to_decimal(it.get('descuento')) > 0 else '',
        })
    data['items'] = items

    filas = []
    tot_base = Decimal('0')
    tot_valor = Decimal('0')
    for imp in impuestos_in:
        clave = _clave_gravamen(imp.get('porc'), imp.get('tipo'))
        base = _to_decimal(imp.get('base')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        valor = _to_decimal(imp.get('valor')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        tot_base += base
        tot_valor += valor
        etiqueta = _label_tarifa(clave) if clave not in codigos else f"{codigos[clave]}: {_label_tarifa(clave)}"
        filas.append({'tarifa': etiqueta,
                      'base': _fmt_money(base), 'valor': _fmt_money(valor)})
    data['impuestos'] = sorted(filas, key=lambda f: f['tarifa'])
    data['impuestos_total'] = {'base': _fmt_money(tot_base), 'valor': _fmt_money(tot_valor)} if filas else None

    # Subtotal/Descuentos solo se imprimen si hay descuento (total_descuentos > 0, ya redondeado)
    data['total_descuentos'] = _to_decimal(
        data.get('total_descuentos', data.get('tot_descuento'))
    ).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    # Cambio: solo si algún medio de pago fue efectivo
    hay_efectivo = any(
        str(p.get('metodo') or '').strip().lower() == 'efectivo' and _to_decimal(p.get('valor')) > 0
        for p in (data.get('pagos') or [])
    )
    if not hay_efectivo:
        data['cambio'] = 0
    for key in ('tot_mercancia', 'tot_descuento', 'tot_iva', 'tot_impoconsumo', 'cambio'):
        data[key] = _fmt_money_opt(data.get(key))
    data['tot_documento'] = _fmt_money(data.get('tot_documento'))
    data['pagos'] = [
        {'metodo': p.get('metodo'), 'valor': _fmt_money(p.get('valor'))}
        for p in (data.get('pagos') or []) if _to_decimal(p.get('valor')) != 0
    ]
    return data


def _write_thermal_pdf(html_string, file_path, width_mm=80, margin_mm=4):
    """
    WeasyPrint no soporta 'size: 80mm auto' (cae a A4). Se renderiza una primera vez con
    una página muy alta para medir el contenido y luego se escribe el PDF con la altura exacta
    (un único "rollo" continuo de 80 mm de ancho).
    """
    from weasyprint import CSS
    base_url = str(settings.BASE_DIR)
    max_h = 5000
    probe = HTML(string=html_string, base_url=base_url).render(
        stylesheets=[CSS(string=f"@page {{ size: {width_mm}mm {max_h}mm; }}")]
    )
    try:
        page_box = probe.pages[0]._page_box
        html_box = page_box.children[0]
        content_px = html_box.margin_height() if callable(getattr(html_box, 'margin_height', None)) else html_box.height
        content_mm = content_px * 25.4 / 96.0
    except Exception:
        content_mm = 0
    height_mm = min(max_h, max(40, content_mm + 2 * margin_mm + 2)) if content_mm else 200
    HTML(string=html_string, base_url=base_url).write_pdf(
        file_path,
        stylesheets=[CSS(string=f"@page {{ size: {width_mm}mm {height_mm:.1f}mm; margin: {margin_mm}mm; }}")],
    )


def render_pos_ticket_to_pdf(ticket_data, prefijo_numeracion=None):
    """
    Renderiza el tiquete POS térmico (80 mm) con WeasyPrint y retorna la ruta del PDF.

    ticket_data: dict con las llaves de templates/plantilla_tiquete_pos_weasyprint.html
        (nom_sistema, nit_emp, dir_emp, municipio_emp, tels_emp, resolucion, contribuyente,
         caja, atendido_por, num_documento, fch_documento, nom_tercero, nit_tercero,
         items, tot_items, tot_mercancia, tot_documento, impuestos, pagos, cufe, ...).
        Los valores monetarios se envían numéricos (sin formatear); aquí se redondean a
        cero decimales. Cada ítem: referencia, cantidad, vlr_unitario y vlr_total (con
        impuestos incluidos), descuento, porc_iva. Cada impuesto: porc, base, valor.
        Si no se envía 'impuestos' se arma a partir de tot_excluida / tot_mercancia2 /
        tot_iva2 / tot_mercancia3 / tot_iva3 (campos de plantilla_factura_pos.docx).
    Guarda: facturas/<prefijo_numeracion>/pos/<AAAA>/<MM>/<DD>/Tiquete_<num_documento>.pdf
    """
    if not WEASYPRINT_AVAILABLE:
        raise RuntimeError("WeasyPrint no está disponible; no se puede generar el tiquete POS.")

    data = dict(ticket_data or {})
    num_documento = str(data.get('num_documento') or '').strip()
    if not num_documento:
        raise ValueError("ticket_data debe incluir 'num_documento'.")

    fecha = _coerce_date(data.get('fch_documento')) or _coerce_date(data.get('fecha_documento_raw'))
    if isinstance(data.get('fch_documento'), (datetime, date)):
        data['fch_documento'] = _coerce_date(data['fch_documento']).strftime('%Y-%m-%d %H:%M:%S')

    # QR en Base64 (SVG) con el CUFE
    cufe = data.get('cufe') or ''
    if cufe and not data.get('qr_base64'):
        data['qr_base64'] = build_qr_base64({'num_documento': num_documento}, cufe)

    # Ítems (2 renglones + código de gravamen), impuestos con TOTAL y valores sin decimales
    data = _preparar_datos_tiquete(data)

    html_string = render_to_string('plantilla_tiquete_pos_weasyprint.html', data)

    out_dir = _build_output_dir(prefijo_numeracion, 'pos', fecha)
    safe_num = re.sub(r'[^A-Za-z0-9_-]', '_', num_documento)
    file_path = os.path.join(out_dir, f"Tiquete_{safe_num}.pdf")

    _write_thermal_pdf(html_string, file_path)
    return file_path
