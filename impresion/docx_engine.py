import os
import io
import qrcode
import logging
from decimal import Decimal
from django.conf import settings
from django.db import connection
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm

logger = logging.getLogger(__name__)

def format_cop(value) -> str:
    """Formatea valores numéricos a Pesos Colombianos (COP) utilizando decimal.Decimal."""
    if value is None:
        dec_val = Decimal('0.00')
    elif isinstance(value, Decimal):
        dec_val = value
    else:
        try:
            dec_val = Decimal(str(value))
        except Exception:
            dec_val = Decimal('0.00')
            
    formatted = f"{dec_val:,.2f}"
    return "$ " + formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def generar_contexto_factura_docx(id_documento: str, doc: DocxTemplate) -> dict:
    """
    Recopila y consolida la información del documento desde Oracle 11g
    mapeando la cabecera, terceros, totales, medios de pago e ítems para docxtpl.
    """
    query_header = """
    SELECT 
        d.ID_DOCUMENTO,
        d.NUM_DOCUMENTO,
        d.FCH_DOCUMENTO,
        d.TOT_DOCUMENTO,
        d.ID_VENDEDOR,
        t.ID_TERCERO,
        t.NOM_TERCERO,
        t.DIR AS DIRECCION,
        t.TELS AS TELEFONO,
        t.ID_MUNICIPIO_DIAN,
        v.TOT_MERCANCIA,
        v.TOT_IVA,
        v.TOT_RETEFUENTE,
        v.VLR_VENTA,
        fel.CUFE
    FROM CO_DOCUMENTOS d
    LEFT JOIN CT_VENTAS v ON d.ID_DOCUMENTO = v.ID_DOCUMENTO
    LEFT JOIN CO_TERCEROS t ON d.ID_TERCERO = t.ID_TERCERO
    LEFT JOIN CT_VENTAS_FEL fel ON d.ID_DOCUMENTO = fel.ID_DOCUMENTO
    WHERE d.ID_DOCUMENTO = %s
    """

    query_items = """
    SELECT 
        i.ID_ARTICULO,
        a.REFERENCIA,
        a.NOM_ARTICULO,
        i.CANTIDAD,
        i.VLR_UNITARIO,
        i.VLR_IVA
    FROM IN_MOV_INVENTARIOS i
    LEFT JOIN IN_ARTICULOS a ON i.ID_ARTICULO = a.ID_ARTICULO
    WHERE i.ID_DOCUMENTO = %s
    ORDER BY i.ID_ITEM
    """

    with connection.cursor() as cursor:
        cursor.execute(query_header, [id_documento])
        h_row = cursor.fetchone()
        if not h_row:
            raise ValueError(f"Documento '{id_documento}' no encontrado en la base de datos.")

        columns = [col[0].lower() for col in cursor.description]
        header = dict(zip(columns, h_row))

        cursor.execute(query_items, [id_documento])
        items_rows = cursor.fetchall()
        item_columns = [col[0].lower() for col in cursor.description]
        items_raw = [dict(zip(item_columns, row)) for row in items_rows]

    # Formateo de Ítems
    items = []
    for idx, raw in enumerate(items_raw, 1):
        cant = Decimal(str(raw.get('cantidad') or '1.00'))
        vlr_u = Decimal(str(raw.get('vlr_unitario') or '0.00'))
        vlr_iva = Decimal(str(raw.get('vlr_iva') or '0.00'))
        tot_l = (cant * vlr_u) + vlr_iva

        items.append({
            'item_no': idx,
            'id_articulo': raw.get('id_articulo', ''),
            'referencia': raw.get('referencia', ''),
            'nom_articulo': raw.get('nom_articulo', ''),
            'cantidad': f"{cant:.2f}",
            'vlr_unitario': format_cop(vlr_u),
            'vlr_iva': format_cop(vlr_iva),
            'tot_linea': format_cop(tot_l)
        })

    # Extraer Medios de Pago
    pagos = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ID_TIPO_MEDIO_PAGO, VALOR FROM TS_MEDIO_PAGOS WHERE ID_DOCUMENTO = %s", [id_documento])
            rows_p = cursor.fetchall()
            for rp in rows_p:
                tipo_mp = str(rp[0]).upper()
                nom_mp = 'Efectivo' if tipo_mp in ['1', '01', 'EFECTIVO'] else ('Tarjeta' if tipo_mp in ['2', '02', 'TARJETA'] else ('Puntos' if 'PUNTO' in tipo_mp else tipo_mp))
                pagos.append({
                    'medio': nom_mp,
                    'valor': format_cop(rp[1])
                })
    except Exception as e:
        logger.debug(f"Sin registros en ts_medio_pagos para {id_documento}: {e}")

    if not pagos:
        pagos = [
            {'medio': 'Contado / POS General', 'valor': format_cop(header.get('tot_documento'))}
        ]

    # Código QR Generado en Memoria (io.BytesIO)
    cufe = header.get('cufe') or f"CUFE_PENDIENTE_DOC_{id_documento}"
    qr_url = f"https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={cufe}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="black", back_color="white")

    qr_io = io.BytesIO()
    img_qr.save(qr_io, format='PNG')
    qr_io.seek(0)

    qr_image = InlineImage(doc, qr_io, width=Mm(30))

    # Ensamblaje del contexto final
    fch_doc = header.get('fch_documento')
    fch_str = fch_doc.strftime('%Y-%m-%d %H:%M:%S') if fch_doc else ''

    context = {
        'doc': {
            'id_documento': header.get('id_documento', ''),
            'num_documento': header.get('num_documento', id_documento),
            'fch_documento': fch_str,
            'resolucion': '18760000001 - Res. Habilitación DIAN',
            'cufe': cufe
        },
        'empresa': {
            'nom_empresa': 'NORTH COMERCIAL S.A.S.',
            'nit': '890.501.170-1',
            'direccion': 'Zona Industrial Cúcuta, Norte de Santander'
        },
        'tercero': {
            'nit': header.get('id_tercero', ''),
            'nom_tercero': header.get('nom_tercero', 'CONSUMIDOR FINAL'),
            'direccion': header.get('direccion', 'CIUDAD'),
            'telefono': header.get('telefono', '0000000')
        },
        'vendedor': {
            'nombre': header.get('id_vendedor', '01')
        },
        'items': items,
        'pagos': pagos,
        'totals': {
            'tot_mercancia': format_cop(header.get('tot_mercancia')),
            'tot_iva': format_cop(header.get('tot_iva')),
            'tot_retefuente': format_cop(header.get('tot_retefuente')),
            'tot_documento': format_cop(header.get('tot_documento'))
        },
        'qr_code': qr_image
    }

    return context


def renderizar_factura_docx(id_documento: str) -> io.BytesIO:
    """
    Renderiza la plantilla .docx utilizando docxtpl e inyecta el código QR generado en memoria.
    Retorna un flujo de bytes (io.BytesIO) listo para descarga o FileResponse.
    """
    template_path = os.path.join(
        settings.BASE_DIR, 'impresion', 'templates', 'impresion', 'plantilla_factura.docx'
    )
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Plantilla .docx no encontrada en: {template_path}")

    doc = DocxTemplate(template_path)
    context = generar_contexto_factura_docx(id_documento, doc)
    doc.render(context)

    output_stream = io.BytesIO()
    doc.save(output_stream)
    output_stream.seek(0)

    return output_stream
