import logging
from decimal import Decimal
from django.db import transaction
from core.tenant import get_oracle_connection
from django.utils import timezone
from core.models import (
    CoDocumento, CtVenta, InMovInventario, CoDocumentoItem
)
from pos.models import PosTurno, PosTicketHeader, PosTicketDetail
from services.legacy_repository import get_system_parameter

logger = logging.getLogger(__name__)

def _leer_parametro_car(oracle, parametro, id_sistema):
    """PARAMETRO_CAR(parametro, SYSDATE, id_sistema) de Oracle; '' si no está configurado."""
    with oracle.cursor() as cursor:
        cursor.execute("SELECT PARAMETRO_CAR(%s, SYSDATE, %s) FROM DUAL", [parametro, id_sistema])
        row = cursor.fetchone()
    return str(row[0]).strip() if row and row[0] is not None else ''


def _preparar_tipo_doc_tef(oracle, id_sistema, id_ano):
    """
    Resuelve (solo lectura) el tipo de documento y la plantilla contable de los vouchers TEF.
    COD_POS_TEF -> CO_TIPO_DOCUMENTOS (cod_tipo_documento -> id_tipo_documento) -> CO_PLANTILLAS -> CO_PLANTILLA_ITEMS.
    Lanza ValueError (antes de escribir nada) si falta algún eslabón.
    """
    cod_tef = _leer_parametro_car(oracle, 'COD_POS_TEF', id_sistema)
    if not cod_tef:
        raise ValueError(
            "Cierre Z abortado: el parámetro COD_POS_TEF no está configurado (vacío) en SG_PARAMETROS. "
            "Hay ventas con tarjeta (TEF) y no se puede generar su documento contable."
        )

    with oracle.cursor() as cursor:
        cursor.execute(
            "SELECT ID_TIPO_DOCUMENTO FROM CO_TIPO_DOCUMENTOS "
            "WHERE COD_TIPO_DOCUMENTO = %s AND ID_SISTEMA = %s AND ID_ANO = %s",
            [cod_tef, id_sistema, id_ano]
        )
        row = cursor.fetchone()
        # Igual que facturación masiva: si no hay equivalencia por código se usa el valor como ID.
        id_tipo_doc = str(row[0]) if row else cod_tef

        cursor.execute(
            "SELECT ID_PLANTILLA, ID_DOC_CONSECUTIVO FROM CO_PLANTILLAS "
            "WHERE ID_TIPO_DOCUMENTO = %s ORDER BY ID_PLANTILLA",
            [id_tipo_doc]
        )
        plantilla = cursor.fetchone()
        if not plantilla:
            raise ValueError(
                f"Cierre Z abortado: no hay plantilla en CO_PLANTILLAS para el tipo de documento TEF "
                f"'{id_tipo_doc}' (COD_POS_TEF='{cod_tef}')."
            )
        id_plantilla = str(plantilla[0])
        id_doc_consec_plantilla = str(plantilla[1]) if plantilla[1] else None

        cursor.execute(
            "SELECT DEBE_HABER, ID_CENTRO_COSTO, ID_CUENTA, CAMPO FROM CO_PLANTILLA_ITEMS "
            "WHERE ID_PLANTILLA = %s ORDER BY ORDEN, ID_ITEM",
            [id_plantilla]
        )
        items = [
            {'debe_haber': r[0], 'id_centro_costo': r[1], 'id_cuenta': r[2], 'campo': r[3]}
            for r in cursor.fetchall()
        ]

    if not any(i['debe_haber'] == 'D' for i in items) or not any(i['debe_haber'] == 'H' for i in items):
        raise ValueError(
            f"Cierre Z abortado: la plantilla {id_plantilla} del tipo TEF '{id_tipo_doc}' debe tener "
            f"al menos una línea Débito y una Crédito (Datáfonos en Tránsito POS vs Caja POS)."
        )
    if any(i['debe_haber'] not in ('D', 'H') or not i['id_cuenta'] for i in items):
        raise ValueError(
            f"Cierre Z abortado: la plantilla {id_plantilla} contiene líneas sin DEBE_HABER o sin cuenta."
        )
    if sum(1 for i in items if i['debe_haber'] == 'D') != sum(1 for i in items if i['debe_haber'] == 'H'):
        raise ValueError(
            f"Cierre Z abortado: la plantilla {id_plantilla} del tipo TEF '{id_tipo_doc}' no balancea "
            f"(distinto número de líneas Débito y Crédito); el voucher TEF lleva el mismo valor en cada línea."
        )
    return {
        'cod': cod_tef, 'id_tipo_documento': id_tipo_doc, 'id_plantilla': id_plantilla,
        'id_doc_consecutivo': id_doc_consec_plantilla, 'items': items,
    }


def _consecutivo_tipo_doc(oracle, id_tipo_doc, id_doc_consecutivo=None):
    """
    Número de documento vía PL/SQL CONSECUTIVO_TIPO_DOC (mismo mecanismo de facturación masiva),
    en la conexión/transacción del tenant. Retorna (num_documento, id_doc_consecutivo).
    """
    oracle.ensure_connection()
    raw_cursor = oracle.connection.cursor()
    try:
        out_val = raw_cursor.var(str, 50)
        raw_cursor.callproc('CONSECUTIVO_TIPO_DOC', [id_tipo_doc, id_doc_consecutivo, out_val])
        numero = out_val.getvalue()
        id_consec = id_doc_consecutivo
        if not id_consec:
            raw_cursor.execute(
                "SELECT ID_DOC_CONSECUTIVO FROM CO_TIPO_DOC_CONSECUTIVOS "
                "WHERE ID_TIPO_DOCUMENTO = :1 AND ROWNUM = 1", [id_tipo_doc]
            )
            row = raw_cursor.fetchone()
            id_consec = row[0] if row else None
    finally:
        raw_cursor.close()
    if not numero:
        raise ValueError(
            f"Cierre Z abortado: CONSECUTIVO_TIPO_DOC no devolvió número para el tipo TEF '{id_tipo_doc}'. "
            f"Verifique que el tipo de documento tenga un consecutivo asociado."
        )
    return str(numero), id_consec


def consolidar_cierre_z(turno_id, id_sistema='1'):
    """
    Ejecuta el Cierre Z de un turno de caja:
    1. Agrupa los tickets confirmados del turno por ID_ARTICULO.
    2. Crea un ÚNICO documento maestro en Oracle (CO_DOCUMENTOS + CT_VENTAS).
    3. Inserta un ÚNICO registro consolidado por artículo en IN_MOV_INVENTARIOS (Kardex).
    4. Genera el comprobante contable maestro en CO_DOCUMENTO_ITEMS.
    5. Actualiza el turno a 'CERRADO'.
    La transmisión DIAN se hace ticket a ticket al sincronizar (pos.views); el Cierre Z solo consolida en el ERP.
    """
    id_sistema = str(id_sistema or '1').strip() or '1'
    turno = PosTurno.objects.get(pk=turno_id)
    if turno.estado == 'CERRADO':
        return {
            'status': 'error',
            'message': f'El turno #{turno_id} ya se encuentra cerrado.'
        }

    tickets_pendientes = turno.tickets.filter(consolidado_cierre=False)
    if not tickets_pendientes.exists():
        turno.estado = 'CERRADO'
        turno.fch_cierre = timezone.now()
        turno.save()
        return {
            'status': 'success',
            'message': f'Turno #{turno_id} cerrado sin ventas registradas.',
            'id_documento': None
        }

    # 1. Agrupar ítems por ID_ARTICULO
    articulos_agrupados = {}
    tot_mercancia_general = Decimal('0.00')
    tot_iva_general = Decimal('0.00')
    tot_general = Decimal('0.00')
    tot_efectivo = Decimal('0.00')
    tot_tarjeta = Decimal('0.00')
    tot_transferencia = Decimal('0.00')

    for ticket in tickets_pendientes:
        tot_mercancia_general += ticket.tot_mercancia
        tot_iva_general += ticket.tot_iva
        tot_general += ticket.tot_ticket
        tot_efectivo += ticket.pago_efectivo
        tot_tarjeta += ticket.pago_tarjeta
        tot_transferencia += ticket.pago_transferencia

        for item in ticket.items.all():
            art_id = item.id_articulo
            if art_id not in articulos_agrupados:
                articulos_agrupados[art_id] = {
                    'id_articulo': art_id,
                    'referencia': item.referencia,
                    'nom_articulo': item.nom_articulo,
                    'cantidad': Decimal('0.00'),
                    'tot_linea': Decimal('0.00'),
                    'vlr_iva': Decimal('0.00'),
                    'vlr_unitario': item.vlr_unitario,
                    'porc_iva': item.porc_iva
                }
            articulos_agrupados[art_id]['cantidad'] += item.cantidad
            articulos_agrupados[art_id]['tot_linea'] += item.tot_linea
            articulos_agrupados[art_id]['vlr_iva'] += item.vlr_iva

    # 2. Generar Consecutivo de Documento en Oracle
    vID_DOC = None
    now_dt = timezone.now()
    if timezone.is_aware(now_dt):
        now_dt = timezone.localtime(now_dt)
    now_naive = now_dt.replace(tzinfo=None)
    id_ano = str(now_naive.year)
    id_tipo_doc = get_system_parameter('POS_TIPO_DOC', 'Z1')
    id_vendedor = get_system_parameter('POS_VENDEDOR_DEF', '01')
    id_bodega = get_system_parameter('POS_BODEGA_DEF', '01')
    id_centro_costo = get_system_parameter('POS_CC_DEF', '01')
    tercero_pos = '222222222222'

    # Oracle del tenant activo (no el alias 'default' del .env).
    oracle = get_oracle_connection()

    # Tiquetes con tarjeta (TEF): 1 documento contable por voucher (excluye valor 0 / anulados).
    tickets_tef = list(tickets_pendientes.filter(pago_tarjeta__gt=0).order_by('fch_ticket', 'ticket_id'))
    tef_cfg = None
    if tickets_tef:
        # Solo lectura y antes de escribir: si falta COD_POS_TEF / plantilla se aborta sin tocar Oracle.
        tef_cfg = _preparar_tipo_doc_tef(oracle, id_sistema, id_ano)
    tef_docs_count = 0
    tef_total = Decimal('0.00')

    with transaction.atomic(using=oracle.alias):
        with oracle.cursor() as cursor:
            cursor.execute("SELECT SEC_DOCUMENTO.NEXTVAL FROM DUAL")
            row = cursor.fetchone()
            if row:
                vID_DOC = str(row[0])
            else:
                raise ValueError("No se pudo obtener SEC_DOCUMENTO.NEXTVAL de Oracle.")

        sql_co_doc = """
        INSERT INTO co_documentos (
            ID_DOCUMENTO, ID_TIPO_DOCUMENTO, NUM_DOCUMENTO, FCH_DOCUMENTO,
            ID_TERCERO, ID_VENDEDOR, ID_RESPONSABLE, TOT_DOCUMENTO,
            OBSER, ESTADO_DOC, FCH_REGISTRO, TERMINAL, ID_SISTEMA, ID_ANO
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        with oracle.cursor() as cursor:
            cursor.execute(sql_co_doc, [
                vID_DOC, id_tipo_doc, vID_DOC, now_naive,
                tercero_pos, id_vendedor, id_vendedor, float(tot_general),
                f"CIERRE Z TURNO #{turno.id_turno} CAJA {turno.caja_id}",
                'GRABADO', now_naive, 'NORTH-LOCAL', id_sistema, id_ano
            ])

            sql_ct_ventas = """
            INSERT INTO ct_ventas (
                ID_DOCUMENTO, TOT_MERCANCIA, TOT_IVA, TOT_RETEFUENTE,
                VLR_RETENCION_IVA, TOT_DESCUENTO, VLR_VENTA, PLAZO_PAGO, ID_CLIENTE_CONTADO
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql_ct_ventas, [
                vID_DOC, float(tot_mercancia_general), float(tot_iva_general),
                0.00, 0.00, 0.00, float(tot_general), 0, tercero_pos
            ])

            # 3. Inserción en in_mov_inventarios (Un registro por cada artículo agrupado)
            sql_in_mov = """
            INSERT INTO in_mov_inventarios (
                ID_ITEM, ID_DOCUMENTO, ID_ARTICULO, FCH_DOCUMENTO, ENTRA_SALE,
                CANTIDAD, VLR_UNITARIO, PORC_DESCUENTO_1, VLR_IVA, IMPOCONSUMO,
                VLR_PROMEDIO_INI, EXISTENCIA, ID_CENTRO_COSTO, ID_BODEGA, ID_SISTEMA
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            item_seq = 1
            for art_id, info in articulos_agrupados.items():
                cursor.execute(sql_in_mov, [
                    str(item_seq), vID_DOC, art_id, now_naive, 'S',
                    float(info['cantidad']), float(info['vlr_unitario']), 0.00,
                    float(info['vlr_iva']), 0.00, float(info['vlr_unitario']),
                    0.00, id_centro_costo, id_bodega, id_sistema
                ])
                item_seq += 1

            # 4. Inserción del asiento contable consolidado en co_documento_items
            sql_co_item = """
            INSERT INTO co_documento_items (
                ID_DOCUMENTO, ID_ITEM, ID_TERCERO, FCH_DOCUMENTO, ID_CENTRO_COSTO,
                DEBE_HABER, ID_CUENTA, CAMPO, VALOR
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            cuenta_caja = get_system_parameter('POS_CTA_CAJA', '110505')
            cuenta_ventas = get_system_parameter('POS_CTA_VENTAS', '413505')
            cuenta_iva = get_system_parameter('POS_CTA_IVA', '240805')

            c_item = 1
            cursor.execute(sql_co_item, [
                vID_DOC, str(c_item), tercero_pos, now_naive, id_centro_costo,
                'D', cuenta_caja, 'CAJA', float(tot_general)
            ])
            c_item += 1

            cursor.execute(sql_co_item, [
                vID_DOC, str(c_item), tercero_pos, now_naive, id_centro_costo,
                'H', cuenta_ventas, 'MCIA', float(tot_mercancia_general)
            ])
            c_item += 1

            if tot_iva_general > Decimal('0.00'):
                cursor.execute(sql_co_item, [
                    vID_DOC, str(c_item), tercero_pos, now_naive, id_centro_costo,
                    'H', cuenta_iva, 'IVA', float(tot_iva_general)
                ])

        # 4b. Documentos TEF (Tiempo 1): un voucher contable + un registro TS_RECAUDOS_TEF por tiquete con tarjeta.
        #     Trasladan el valor de tarjeta de Caja POS a Datáfonos en Tránsito POS según la plantilla COD_POS_TEF.
        if tef_cfg:
            sql_tef_doc = """
            INSERT INTO co_documentos (
                ID_DOCUMENTO, ID_TIPO_DOCUMENTO, NUM_DOCUMENTO, FCH_DOCUMENTO,
                ID_TERCERO, ID_VENDEDOR, ID_RESPONSABLE, TOT_DOCUMENTO,
                OBSER, DOCTO_ALTERNO, ESTADO_DOC, FCH_REGISTRO, TERMINAL, ID_SISTEMA, ID_ANO
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            sql_tef_item = """
            INSERT INTO co_documento_items (
                ID_DOCUMENTO, ID_ITEM, ID_TERCERO, FCH_DOCUMENTO, ID_CENTRO_COSTO,
                DEBE_HABER, ID_CUENTA, CAMPO, VALOR
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            sql_tef_recaudo = """
            INSERT INTO ts_recaudos_tef (
                ID_DOCUMENTO, ID_DOCUMENTO_VENTA, CAJA_ID, NUM_TICKET, CANAL,
                NUM_APROBACION, FRANQUICIA, TIPO_TARJETA, ULTIMOS4, NUM_RECIBO,
                VLR_BRUTO, ESTADO, OBSER
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            caja_ref = str(turno.caja_id or '')[:20]

            for tk in tickets_tef:
                valor_tarjeta = tk.pago_tarjeta
                num_ticket = tk.numero_ticket
                aprob = str(tk.tef_aprobacion or '').strip()
                franq = str(tk.tef_franquicia or '').strip()

                with oracle.cursor() as cursor:
                    cursor.execute("SELECT SEC_DOCUMENTO.NEXTVAL FROM DUAL")
                    row = cursor.fetchone()
                    if not row:
                        raise ValueError("No se pudo obtener SEC_DOCUMENTO.NEXTVAL de Oracle (documento TEF).")
                    id_doc_tef = str(row[0])

                num_doc_tef, id_consec_tef = _consecutivo_tipo_doc(
                    oracle, tef_cfg['id_tipo_documento'], tef_cfg['id_doc_consecutivo']
                )

                with oracle.cursor() as cursor:
                    cursor.execute(sql_tef_doc, [
                        id_doc_tef, tef_cfg['id_tipo_documento'], num_doc_tef, now_naive,
                        tercero_pos, id_vendedor, id_vendedor, float(valor_tarjeta),
                        f"TEF POS {num_ticket} APROB:{aprob or 'N/A'} {franq}".strip()[:250],
                        aprob[:15] or None,
                        'GRABADO', now_naive, 'NORTH-LOCAL', id_sistema, id_ano
                    ])
                    if id_consec_tef:
                        # Bypass ORA-04091 (trigger de consecutivos): se asigna tras el INSERT, como en facturación.
                        cursor.execute(
                            "UPDATE co_documentos SET ID_DOC_CONSECUTIVO = %s WHERE ID_DOCUMENTO = %s",
                            [str(id_consec_tef), id_doc_tef]
                        )

                    # Líneas de la plantilla: cada línea Débito/Crédito lleva el valor de la tarjeta (voucher de 2 patas).
                    for n, pi in enumerate(tef_cfg['items'], start=1):
                        campo = str(pi['campo'] or '')[:17]
                        campo = f"{campo}-{n}" if sum(1 for x in tef_cfg['items'] if x['campo'] == pi['campo']) > 1 else campo
                        cursor.execute(sql_tef_item, [
                            id_doc_tef, str(n), tercero_pos, now_naive,
                            pi['id_centro_costo'] or id_centro_costo,
                            pi['debe_haber'], pi['id_cuenta'], campo[:20] or None, float(valor_tarjeta)
                        ])

                    cursor.execute(sql_tef_recaudo, [
                        id_doc_tef, vID_DOC, caja_ref, str(num_ticket or '')[:40], 'TEF',
                        (aprob if aprob else f"SIN-APROB-{num_ticket}")[:50],
                        franq[:50] or None, None,
                        str(tk.tef_ultimos4 or '').strip()[:10] or None,
                        str(tk.tef_recibo or '').strip()[:50] or None,
                        float(valor_tarjeta), 'PENDIENTE',
                        str(tk.tef_respuesta_raw or '').strip()[:250] or None
                    ])
                tef_docs_count += 1
                tef_total += valor_tarjeta

    # 5. Marcar tickets como consolidados y cerrar turno
    tickets_pendientes.update(consolidado_cierre=True)
    turno.estado = 'CERRADO'
    turno.fch_cierre = timezone.now()
    turno.tot_ventas_efectivo = tot_efectivo
    turno.tot_ventas_tarjeta = tot_tarjeta
    turno.tot_ventas_transferencia = tot_transferencia
    turno.id_documento_cierre = vID_DOC
    turno.save()

    # La transmisión DIAN ya no se encola aquí: se hace ticket a ticket (evita doble facturación).
    dian_task_id = None

    logger.info(f"Cierre Z exitoso. Turno #{turno_id} -> Doc Oracle: {vID_DOC}")
    return {
        'status': 'success',
        'message': f'Cierre Z procesado exitosamente. Documento maestro: {vID_DOC}. Consolidado en ERP.',
        'id_documento': vID_DOC,
        'dian_task_id': dian_task_id,
        'tot_general': float(tot_general),
        'tot_mercancia': float(tot_mercancia_general),
        'tot_iva': float(tot_iva_general),
        'items_count': len(articulos_agrupados),
        'tef_docs_count': tef_docs_count,
        'tef_total': float(tef_total)
    }
