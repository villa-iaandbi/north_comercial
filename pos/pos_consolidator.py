import logging
from decimal import Decimal
from django.db import connection, transaction
from django.utils import timezone
from core.models import (
    CoDocumento, CtVenta, InMovInventario, CoDocumentoItem
)
from pos.models import PosTurno, PosTicketHeader, PosTicketDetail
from services.legacy_repository import get_system_parameter
from facturacion.dian_async import encolar_transmision_dian

logger = logging.getLogger(__name__)

def consolidar_cierre_z(turno_id):
    """
    Ejecuta el Cierre Z de un turno de caja:
    1. Agrupa los tickets confirmados del turno por ID_ARTICULO.
    2. Crea un ÚNICO documento maestro en Oracle (CO_DOCUMENTOS + CT_VENTAS).
    3. Inserta un ÚNICO registro consolidado por artículo en IN_MOV_INVENTARIOS (Kardex).
    4. Genera el comprobante contable maestro en CO_DOCUMENTO_ITEMS.
    5. Actualiza el turno a 'CERRADO'.
    6. Orquesta la Transmisión Asíncrona a la DIAN en segundo plano vía Django-Q2.
    """
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

    with transaction.atomic(using='default'):
        with connection.cursor() as cursor:
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
        with connection.cursor() as cursor:
            cursor.execute(sql_co_doc, [
                vID_DOC, id_tipo_doc, vID_DOC, now_naive,
                tercero_pos, id_vendedor, id_vendedor, float(tot_general),
                f"CIERRE Z TURNO #{turno.id_turno} CAJA {turno.caja_id}",
                'GRABADO', now_naive, 'NORTH-LOCAL', '1', id_ano
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
                    0.00, id_centro_costo, id_bodega, '1'
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

    # 5. Marcar tickets como consolidados y cerrar turno
    tickets_pendientes.update(consolidado_cierre=True)
    turno.estado = 'CERRADO'
    turno.fch_cierre = timezone.now()
    turno.tot_ventas_efectivo = tot_efectivo
    turno.tot_ventas_tarjeta = tot_tarjeta
    turno.tot_ventas_transferencia = tot_transferencia
    turno.id_documento_cierre = vID_DOC
    turno.save()

    # 6. Orquestar la Transmisión Asíncrona a la DIAN mediante Django-Q2
    dian_task_id = None
    if vID_DOC:
        try:
            dian_task_id = encolar_transmision_dian(vID_DOC)
            logger.info(f"Transmisión asíncrona DIAN encolada para Cierre Z Doc '{vID_DOC}' con Task ID: {dian_task_id}")
        except Exception as e:
            logger.error(f"Fallo al encolar transmisión DIAN en Cierre Z para Doc '{vID_DOC}': {e}")

    logger.info(f"Cierre Z exitoso. Turno #{turno_id} -> Doc Oracle: {vID_DOC}")
    return {
        'status': 'success',
        'message': f'Cierre Z procesado exitosamente. Documento maestro: {vID_DOC}. Transmisión DIAN encolada.',
        'id_documento': vID_DOC,
        'dian_task_id': dian_task_id,
        'tot_general': float(tot_general),
        'tot_mercancia': float(tot_mercancia_general),
        'tot_iva': float(tot_iva_general),
        'items_count': len(articulos_agrupados)
    }
