import logging
from django.db import connection

logger = logging.getLogger(__name__)

def calculate_dv(nit: str) -> str:
    """Calcula el Dígito de Verificación (Mod 11) de la DIAN."""
    if not nit or not nit.isdigit():
        return ""
    primos = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]
    suma = 0
    for i, char in enumerate(reversed(nit)):
        if i < len(primos):
            suma += int(char) * primos[i]
    residuo = suma % 11
    if residuo > 1:
        return str(11 - residuo)
    return str(residuo)
def get_system_parameter(param_id: str, default_value: str = "") -> str:
    """Obtiene un parámetro de sistema desde Oracle SG_PARAMETROS"""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VLR_CHR FROM SG_PARAMETROS WHERE ID_PARAMETRO=%s", [param_id])
            row = cursor.fetchone()
            if row and row[0]:
                return str(row[0])
    except Exception as e:
        logger.error(f"Fallo al recuperar parámetro SG_PARAMETROS ({param_id}): {e}")
    return default_value

def get_doors_invoice_data(id_documento: str) -> tuple[dict, list, dict]:
    """
    Repositorio Legacy de Oracle 11g (DOORS).
    Extrae la matemática exacta almacenada en BD. Prohibido recalcular valores.
    """
    header_sql = """
    SELECT 
        d.ID_DOCUMENTO,
        d.NUM_DOCUMENTO,
        d.FCH_DOCUMENTO,
        d.OBSER,
        d.TOT_DOCUMENTO,
        d.ID_VENDEDOR,
        NUM_RESOLUCION(d.ID_DOCUMENTO) AS NUM_RESOLUCION,
        VALOR_TOT_MCIA(d.ID_DOCUMENTO) AS TOT_MERCANCIA,
        v.PLAZO_PAGO,
        v.TOT_RETEFUENTE,
        v.VLR_RETENCION_IVA,
        v.ID_CLIENTE_CONTADO,
        -- Tercero Regular
        t.COD_TERCERO AS TERCERO_NIT,
        CAST('' AS VARCHAR2(1)) AS TERCERO_DV,
        t.NOM_TERCERO AS TERCERO_NOMBRE,
        t.NOM_NEGOCIO AS TERCERO_TRADING_NAME,
        t.E_MAIL AS TERCERO_EMAIL,
        t.DIR AS TERCERO_DIRECCION,
        t.TELS AS TERCERO_TELEFONO,
        t.ID_MUNICIPIO_DIAN AS TERCERO_CIUDAD,
        t.ID_REGIMEN AS TERCERO_ID_REGIMEN,
        NOM_DEPTO(SUBSTR(t.ID_MUNICIPIO, 1, 2)) AS TERCERO_DEPARTAMENTO_NOMBRE,
        NOM_MUNICIPIO_DIAN(t.ID_MUNICIPIO_DIAN) AS TERCERO_MUNICIPIO_NOMBRE,
        -- Cliente Creado por Ventas de Contado
        cc.COD_CLIENTE_CONTADO AS CONTADO_NIT,
        CAST('' AS VARCHAR2(1)) AS CONTADO_DV,
        cc.NOM_CLIENTE_CONTADO AS CONTADO_NOMBRE,
        cc.DIR AS CONTADO_DIRECCION,
        cc.TELS AS CONTADO_TELEFONO,
        '11001' AS CONTADO_CIUDAD,
        'CUNDINAMARCA' AS CONTADO_DEPARTAMENTO_NOMBRE,
        'BOGOTA' AS CONTADO_MUNICIPIO_NOMBRE,
        -- Campos de Representación Gráfica y Auditoría
        NOM_PERSONA(d.ID_VENDEDOR) AS VENDEDOR_NOMBRE_LARGO,
        s.OBSER2 AS DOCUMENT_FOOTER_TEXT,
        s.DIRECCION AS DOCUMENT_HEADER_TEXT_1,
        NOM_PERSONA(d.ID_RESPONSABLE) AS CREADO_POR,
        NOM_MUNICIPIO(s.ID_MUNICIPIO) AS STORE_HOUSE,
        NVL(CAJAS_DOC(d.ID_DOCUMENTO), 0) AS TOTAL_BOXES,
        NVL(PESO_DOC(d.ID_DOCUMENTO), 0) AS WEIGHT,
        (d.ID_VENDEDOR || '/' || NOM_PERSONA(d.ID_VENDEDOR) || '/cel:' || SUBSTR(DIRTEL_PERSONA(d.ID_VENDEDOR,'T'), 1, 12)) AS ZONE,
        s.OBSER1 AS NOTE_2,
        PARAMETRO_CAR('BAPP_TX3', SYSDATE, '1') AS NOTE_3,
        PARAMETRO_CAR('BAPP_TX4', SYSDATE, '1') AS NOTE_4,
        ('Email: ' || LOWER(s.ESLOGAN)) AS NOTE_5,
        PARAMETRO_CAR('BAPP_TX5', SYSDATE, '1') AS NOTE_6,
        PARAMETRO_CAR('FAC_NOMBRE_FACTURA', SYSDATE, '1') AS NOTE_7
    FROM 
        CO_DOCUMENTOS d
    LEFT JOIN 
        CT_VENTAS v ON d.ID_DOCUMENTO = v.ID_DOCUMENTO
    LEFT JOIN 
        CO_TERCEROS t ON d.ID_TERCERO = t.ID_TERCERO
    LEFT JOIN 
        CT_CLIENTE_CONTADOS cc ON v.ID_CLIENTE_CONTADO = cc.ID_CLIENTE_CONTADO
    LEFT JOIN 
        SG_SISTEMAS s ON d.ID_SISTEMA = s.ID_SISTEMA
    WHERE 
        d.ID_DOCUMENTO = %s
    """

    items_sql = """
    SELECT 
        i.ID_ARTICULO,
        a.REFERENCIA AS REFERENCIA,
        a.NOM_ARTICULO AS NOM_ARTICULO,
        a.ID_GRUPO AS GRUPO,
        i.CANTIDAD,
        i.VLR_UNITARIO,
        i.PORC_DESCUENTO_1,
        i.VLR_IVA AS VLR_IVA_LINEA,
        i.IMPOCONSUMO AS VLR_IMPOCONSUMO_LINEA,
        i.ID_GRAVAMEN,
        GRAVAMEN(i.ID_GRAVAMEN) AS PORC_IVA
    FROM 
        IN_MOV_INVENTARIOS i
    JOIN 
        IN_ARTICULOS a ON i.ID_ARTICULO = a.ID_ARTICULO
    WHERE 
        i.ID_DOCUMENTO = %s
    ORDER BY 
        a.ID_GRUPO, a.REFERENCIA
    """

    taxes_sql = """
    SELECT 
        CAMPO, 
        SUM(VALOR) AS TAX_AMOUNT
    FROM CO_DOCUMENTO_ITEMS
    WHERE ID_DOCUMENTO = %s
      AND CAMPO IS NOT NULL
    GROUP BY CAMPO
    """

    with connection.cursor() as cursor:
        # Extraer Header
        cursor.execute(header_sql, [id_documento])
        header_row = cursor.fetchone()
        
        if not header_row:
            raise ValueError(f"Documento de Facturación '{id_documento}' no encontrado en Oracle.")
            
        columns = [col[0] for col in cursor.description]
        base_header = dict(zip(columns, header_row))

        # Extraer Items
        cursor.execute(items_sql, [id_documento])
        items_rows = cursor.fetchall()
        item_columns = [col[0] for col in cursor.description]
        
        items_data = [dict(zip(item_columns, row)) for row in items_rows]

        # Extraer Totales de Impuestos Exactos
        cursor.execute(taxes_sql, [id_documento])
        taxes_rows = cursor.fetchall()
        taxes_columns = [col[0] for col in cursor.description]
        
        raw_taxes_data = [dict(zip(taxes_columns, row)) for row in taxes_rows]
        
        # Estructurar la información extraída de los impuestos puros (Dictionary Builder)
        # 1. Agrupar los valores puros
        tax_values = {row.get('CAMPO'): float(row.get('TAX_AMOUNT') or 0.0) for row in raw_taxes_data}
        
        # 2. Calcular la base global sumando todos los campos MCIA (gravado + excluido)
        global_base = sum(val for campo, val in tax_values.items() if campo.startswith('MCIA'))
        
        taxes_data = {}
        for campo, amount in tax_values.items():
            if campo.startswith('MCIA'):
                # Identifica el número de IVA correspondiente. Ej: MCIA2 -> TOT_IVA2
                suffix = campo.replace('MCIA', '')
                iva_campo = f'TOT_IVA{suffix}'
                iva_amount = tax_values.get(iva_campo, 0.0)
                
                percent = (iva_amount / amount * 100) if amount > 0 else 0.0
                
                # Se mapea siempre a `TOT_IVAx` que es lo que espera `payload_builder.py`
                taxes_data[iva_campo] = {
                    'amount': iva_amount,
                    'base': amount,
                    'percent': percent
                }
            
            elif campo == 'RTE_COMPRAS':
                percent = (amount / global_base * 100) if global_base > 0 else 0.0
                taxes_data['RTE_COMPRAS'] = {
                    'amount': amount,
                    'base': global_base,
                    'percent': percent
                }
                
            elif campo == 'TOT_IMPOCONSUMO':
                # Base teórica para impoconsumo, asignada desde global_base
                taxes_data['TOT_IMPOCONSUMO'] = {
                    'amount': amount,
                    'base': global_base,
                    'percent': 0.0
                }

    # --- Lógica de Trasposición (DTO Normalizer) ---
    header_data = {
        'ID_DOCUMENTO': base_header.get('ID_DOCUMENTO'),
        'NUM_DOCUMENTO': base_header.get('NUM_DOCUMENTO'),
        'FCH_DOCUMENTO': base_header.get('FCH_DOCUMENTO').strftime("%Y-%m-%dT%H:%M:%S") if base_header.get('FCH_DOCUMENTO') else None,
        'OBSER': base_header.get('OBSER'),
        'TOT_DOCUMENTO': float(base_header.get('TOT_DOCUMENTO') or 0.0),
        'VENDEDOR': base_header.get('ID_VENDEDOR'),
        'NUM_RESOLUCION': base_header.get('NUM_RESOLUCION'),
        'TOT_MERCANCIA': float(base_header.get('TOT_MERCANCIA') or 0.0),
        'PLAZO_PAGO': int(base_header.get('PLAZO_PAGO') or 0),
        
        # Nuevos campos de gráfica y auditoría general
        'VENDEDOR_NOMBRE_LARGO': base_header.get('VENDEDOR_NOMBRE_LARGO', ''),
        'DOCUMENT_FOOTER_TEXT': base_header.get('DOCUMENT_FOOTER_TEXT', ''),
        'DOCUMENT_HEADER_TEXT_1': base_header.get('DOCUMENT_HEADER_TEXT_1', ''),
        'CREADO_POR': base_header.get('CREADO_POR', ''),
        'STORE_HOUSE': base_header.get('STORE_HOUSE', ''),
        'TOTAL_BOXES': base_header.get('TOTAL_BOXES', 0),
        'WEIGHT': base_header.get('WEIGHT', 0.0),
        'ZONE': base_header.get('ZONE', ''),
        'NOTE_2': base_header.get('NOTE_2', ''),
        'NOTE_3': base_header.get('NOTE_3', ''),
        'NOTE_4': base_header.get('NOTE_4', ''),
        'NOTE_5': base_header.get('NOTE_5', ''),
        'NOTE_6': base_header.get('NOTE_6', ''),
        'NOTE_7': base_header.get('NOTE_7', '')
    }

    # Resolución del Adquirente (Contado vs Tercero)
    if base_header.get('ID_CLIENTE_CONTADO'):
        nit = str(base_header.get('CONTADO_NIT') or '')
        header_data['NIT_TERCERO'] = nit
        header_data['DIGITO_VERIFICACION'] = base_header.get('CONTADO_DV') if base_header.get('CONTADO_DV') else calculate_dv(nit)
        header_data['RAZON_SOCIAL'] = base_header.get('CONTADO_NOMBRE')
        header_data['NOMBRE_COMERCIAL'] = base_header.get('CONTADO_NOMBRE') # Fallback as it is contado
        header_data['EMAIL'] = '' 
        header_data['DIRECCION'] = base_header.get('CONTADO_DIRECCION')
        header_data['TELEFONO'] = base_header.get('CONTADO_TELEFONO')
        header_data['ID_MUNICIPIO_DIAN'] = base_header.get('CONTADO_CIUDAD')
        header_data['DEPARTAMENTO_NOMBRE'] = base_header.get('CONTADO_DEPARTAMENTO_NOMBRE')
        header_data['MUNICIPIO_NOMBRE'] = base_header.get('CONTADO_MUNICIPIO_NOMBRE')
        # Todo cliente de mostrador se asume Consumidor Final
        header_data['REGIMEN'] = '4' 
    else:
        nit = str(base_header.get('TERCERO_NIT') or '')
        header_data['NIT_TERCERO'] = nit
        header_data['DIGITO_VERIFICACION'] = base_header.get('TERCERO_DV') if base_header.get('TERCERO_DV') else calculate_dv(nit)
        header_data['RAZON_SOCIAL'] = base_header.get('TERCERO_NOMBRE')
        header_data['NOMBRE_COMERCIAL'] = base_header.get('TERCERO_TRADING_NAME') or base_header.get('TERCERO_NOMBRE')
        header_data['EMAIL'] = base_header.get('TERCERO_EMAIL', '')
        header_data['DIRECCION'] = base_header.get('TERCERO_DIRECCION')
        header_data['TELEFONO'] = base_header.get('TERCERO_TELEFONO')
        header_data['ID_MUNICIPIO_DIAN'] = base_header.get('TERCERO_CIUDAD')
        header_data['DEPARTAMENTO_NOMBRE'] = base_header.get('TERCERO_DEPARTAMENTO_NOMBRE')
        header_data['MUNICIPIO_NOMBRE'] = base_header.get('TERCERO_MUNICIPIO_NOMBRE')
        
        # Mapeo pragmático de Regímenes preestablecidos
        id_regimen_db = base_header.get('TERCERO_ID_REGIMEN')
        # Si no tiene régimen asignado, asumir 2 (Responsable) por consistencia de negocio usual
        header_data['REGIMEN'] = str(id_regimen_db) if id_regimen_db else '2'

    return header_data, items_data, taxes_data
