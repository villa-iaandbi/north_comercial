import os
import io
import base64
import qrcode
from datetime import datetime
from django.conf import settings
from django.template.loader import render_to_string
from django.db import connection

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
    Construye la cadena base del código QR según el estándar DIAN y genera la imagen en Base64.
    """
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
    
    # Si la info de impuestos no está en BD principal, la mockeamos o se calcula.
    val_iva = "0.00"
    val_otro_im = "0.00"
    val_tol_fac = val_fac
    
    qr_string = (
        f"NumFac: {num_fac}\n"
        f"FecFac: {fec_fac}\n"
        f"HorFac: {hor_fac}\n"
        f"NitFac: {nit_fac}\n"
        f"DocAdq: {doc_adq}\n"
        f"ValFac: {val_fac}\n"
        f"ValIva: {val_iva}\n"
        f"ValOtroIm: {val_otro_im}\n"
        f"ValTolFac: {val_tol_fac}\n"
        f"CUFE: {cufe}\n"
        f"QRCode: https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={cufe}"
    )
    
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10, # PARÁMETRO VITAL PARA LA RESOLUCIÓN
        border=4,
    )
    qr.add_data(qr_string)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()
    
    return base64.b64encode(img_bytes).decode('utf-8')


import re
from datetime import timedelta

def _build_context(id_documento):
    """
    Construye el contexto exacto para la plantilla HTML consultando la base de datos de Oracle.
    Extrae dinámicamente datos de empresa, parámetros, y cruza detalles con impuestos.
    """
    with connection.cursor() as cursor:
        # 0. Datos de la Empresa (SG_SISTEMAS id='1')
        cursor.execute("""
            SELECT NOM_SISTEMA, NIT, DIRECCION, ESLOGAN, OBSER1, OBSER2, LOGO_1 
            FROM SG_SISTEMAS WHERE ID_SISTEMA='1'
        """)
        empresa_row = cursor.fetchone()
        if empresa_row:
            empresa_data = {
                'nom_empresa': empresa_row[0] or "",
                'nit': empresa_row[1] or "",
                'dir': empresa_row[2] or "",
                'email': empresa_row[3] or "",
                'obser1': empresa_row[4] or "",
                'obser2': empresa_row[5] or "",
                'logo_blob': empresa_row[6]
            }
        else:
            empresa_data = {
                'nom_empresa': "", 'nit': "", 'dir': "", 'email': "", 
                'obser1': "", 'obser2': "", 'logo_blob': None
            }

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
                NIT_TERCERO(ter.ID_TERCERO) AS NIT_TERCERO, ter.NOM_TERCERO, ter.DIR, ter.TELS, ter.ID_MUNICIPIO AS MUNICIPIO, ter.DIR2 AS BARRIO, ter.NOM_NEGOCIO,
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
            SELECT NVL(MAX(ID_ITEM), 0)
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
                lin.IMPOCONSUMO
            FROM IN_MOV_INVENTARIOS lin
            LEFT JOIN IN_ARTICULOS itm ON lin.ID_ARTICULO = itm.ID_ARTICULO
            WHERE lin.ID_DOCUMENTO = %s
            ORDER BY lin.ID_ARTICULO ASC
        """, [id_documento])
        items_db = dictfetchall(cursor)

    # Convertir LOGO a Base64 desde el archivo local
    logo_base64 = ""
    try:
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
        
        items_list.append({
            'referencia': item.get('referencia') or '',
            'descripcion': item.get('descripcion') or '',
            'cajas': "", # Se deja vacío intencional si no hay datos de caja en línea
            'unidades': "", 
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

def render_invoice_to_pdf(id_documento):
    """
    Genera físicamente el PDF de una factura utilizando Weasyprint y retorna la ruta del archivo.
    Aplica la lógica del Código QR requerida.
    """
    context, num_documento = _build_context(id_documento)

    # 5. Renderizar la plantilla a STRING
    html_string = render_to_string('plantilla_factura_weasyprint.html', context)
    
    # 6. Generar estructura de directorios
    now = datetime.now()
    media_dir = os.path.join(settings.MEDIA_ROOT, 'facturas', str(now.year), f"{now.month:02d}", f"{now.day:02d}")
    os.makedirs(media_dir, exist_ok=True)
    
    pdf_filename = f"FES_{num_documento}.pdf"
    file_path = os.path.join(media_dir, pdf_filename)
    
    # 7. Salida a disco
    if WEASYPRINT_AVAILABLE:
        try:
            HTML(string=html_string).write_pdf(file_path)
        except Exception as e:
            print(f"Error nativo WeasyPrint: {str(e)}")
            _generate_dummy_pdf(file_path, num_documento, html_string)
    else:
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
