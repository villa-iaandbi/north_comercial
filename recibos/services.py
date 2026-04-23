import decimal
from django.db import transaction, connection
from django.db.models import Q
from django.utils import timezone
import pytz
import uuid

from core.models import (
    MvReciboNorth, 
    TsIngreso, 
    CoDocumentoAfectado, 
    CoDocumento,
    SgParametro,
    CoTipoDocumento,
    CoPlantilla,
    CoPlantillaItem,
    CoDocumentoItem,
    TsMedioPago,
    SgUsuarioCentroCosto
)
from facturacion.services import obtener_consecutivo, obtener_id_secuencia

def procesar_lote_recibos(nums_recibos):
    """
    Motor Transaccional para Recibos de Caja (DOORS-Oracle 11g)
    Alineado arquitectónicamente con facturacion/services.py.
    """
    resultados = {'procesados': 0, 'fallidos': 0, 'errores': []}
    
    bgt_tz = pytz.timezone('America/Bogota')
    fch_local = timezone.now().astimezone(bgt_tz).replace(tzinfo=None)
    current_year = str(fch_local.year)
    
    recibos = MvReciboNorth.objects.prefetch_related('items').filter(
        Q(num_recibo__in=nums_recibos) & 
        (Q(procesado__isnull=True) | ~Q(procesado='S'))
    )
    
    if not recibos:
        resultados['errores'].append("No se encontraron recibos válidos o ya están procesados.")
        return resultados

    # ==========================================
    # FASE 1: CACHÉ (Anti N+1)
    # ==========================================
    vendedores_ids = [r.id_vendedor for r in recibos]
    ccs_db = SgUsuarioCentroCosto.objects.filter(id_usuario__in=vendedores_ids)
    cache_ccs = {cc.id_usuario: cc.id_centro_costo for cc in ccs_db}

    docs_afectados_ids = [it.id_documento for r in recibos for it in r.items.all() if it.id_documento]
    cache_docs_afectados = {}
    if docs_afectados_ids:
        docs_qs = CoDocumento.objects.filter(id_documento__in=docs_afectados_ids).values_list('id_documento', 'num_documento')
        cache_docs_afectados = {str(d[0]): d[1] for d in docs_qs}

    param_rcaja_qs = SgParametro.objects.filter(id_parametro='COD_RCAJA')
    param_rcaja = None
    for p in param_rcaja_qs.iterator():
        param_rcaja = p
        break
    cod_rcaja = param_rcaja.vlr_chr if param_rcaja else '0'

    tipo_doc_rc_qs = CoTipoDocumento.objects.filter(
        cod_tipo_documento=cod_rcaja, 
        id_sistema='3', # o 1, o el sistema definido para recibos. Lo buscaré genérico si falla
        id_ano=current_year
    )
    tipo_doc_rc = None
    for td in tipo_doc_rc_qs.iterator():
        tipo_doc_rc = td
        break
    
    # Intento 2 si el sistema no era 3:
    if not tipo_doc_rc:
        tipo_doc_rc_qs2 = CoTipoDocumento.objects.filter(
            cod_tipo_documento=cod_rcaja, 
            id_ano=current_year
        )
        for td in tipo_doc_rc_qs2.iterator():
            tipo_doc_rc = td
            break

    id_tipo_doc_rc = tipo_doc_rc.id_tipo_documento if tipo_doc_rc else '15018'
    
    plantilla_qs = CoPlantilla.objects.filter(id_tipo_documento=id_tipo_doc_rc)
    plantilla_doc_rc = None
    for p in plantilla_qs.iterator():
        plantilla_doc_rc = p
        break
    
    plantilla_items_global = []
    if plantilla_doc_rc:
        plantilla_items_global = list(CoPlantillaItem.objects.filter(id_plantilla=plantilla_doc_rc.id_plantilla).order_by('id_item'))

    for recibo in recibos:
        print(f"\n--- INICIANDO DRY RUN PARA RECIBO: {recibo.num_recibo} ---")
        try:
            with transaction.atomic():
                # Acumuladores de Tesorería básicos
                total_efectivo = decimal.Decimal('0.00')
                total_cheque = decimal.Decimal('0.00')
                total_caja = recibo.tot_recibo or decimal.Decimal('0.00')
                
                tot_ret_compras = decimal.Decimal('0.00')
                tot_ret_servicios = decimal.Decimal('0.00')
                tot_ret_otros = decimal.Decimal('0.00')
                tot_ret_ica = decimal.Decimal('0.00')
                tot_ret_iva = decimal.Decimal('0.00')
                tot_descuento_financiero = decimal.Decimal('0.00')
                tot_cxc_bruto = decimal.Decimal('0.00')
                
                # Distribución según Medio de Pago (E=Efectivo, C=Consignacion, T=Transferencia)
                # Opcional: Se asume que todo lo que no sea 'C' (Cheque) se va a efectivo a menos que se mapen más.
                # Para Mapear TsMedioPago
                forma_pago = recibo.medio_pago or 'E'
                # Definir valores TsIngreso
                if forma_pago == 'CHQ': # Si cheque existe
                    total_cheque = total_caja
                else:
                    total_efectivo = total_caja
                
                facturas_str_list = []
                afectados_inserts = []
                
                for item in recibo.items.all():
                    # Sumando totales para cuadratura
                    tot_descuento_financiero += (item.descuento or decimal.Decimal('0.00'))
                    tot_ret_compras += (item.ret_compras or decimal.Decimal('0.00'))
                    tot_ret_servicios += (item.ret_servicios or decimal.Decimal('0.00'))
                    tot_ret_otros += (item.ret_otros or decimal.Decimal('0.00'))
                    tot_ret_ica += (item.ret_ica or decimal.Decimal('0.00'))
                    tot_ret_iva += (item.ret_iva or decimal.Decimal('0.00'))
                    
                    vlr_afectacion = item.vlr_afectacion or decimal.Decimal('0.00')
                    
                    if item.id_documento:
                        num_doc = cache_docs_afectados.get(str(item.id_documento), str(item.id_documento))
                        facturas_str_list.append(str(num_doc))
                        
                    afectados_inserts.append(CoDocumentoAfectado(
                        id_documento='DUMMY', # Actualizado luego
                        id_documento_afectado=item.id_documento,
                        vlr_afectacion=vlr_afectacion,
                        vlr_dto_financiero=item.descuento or decimal.Decimal('0.00'),
                        vlr_retencion_compras=item.ret_compras or decimal.Decimal('0.00'),
                        vlr_retencion_servicios=item.ret_servicios or decimal.Decimal('0.00'),
                        vlr_retencion_ica=item.ret_ica or decimal.Decimal('0.00'),
                        vlr_retencion_iva=item.ret_iva or decimal.Decimal('0.00'),
                        vlr_retencion_otros=item.ret_otros or decimal.Decimal('0.00'),
                        id_cuota=None, 
                        siono_pendiente='N',
                        siono_afecta='S',
                        vlr_iva=decimal.Decimal('0.00')
                    ))
                
                # Cuadratura Dinámica
                suma_retenciones = tot_ret_compras + tot_ret_servicios + tot_ret_otros + tot_ret_ica + tot_ret_iva
                tot_cxc_bruto = total_caja + suma_retenciones + tot_descuento_financiero

                # ==========================================
                # Lógica de Consecutivos (BYPASS)
                # ==========================================
                num_documento, consec_id_recuperado = obtener_consecutivo(id_tipo_doc_rc)
                if not num_documento:
                    raise Exception(f"Fallo Crítico: CONSECUTIVO_TIPO_DOC retornó NULL para el tipo {id_tipo_doc_rc}.")
                
                sec_id = obtener_id_secuencia()
                id_secuencia_str = str(sec_id)
                
                obs_dinamica = f"ABONO A LAS FACTURA(S): {', '.join(facturas_str_list)}"[:249]
                
                # Actualizar ID en afectados
                for af in afectados_inserts:
                    af.id_documento = id_secuencia_str

                # ==========================================
                # CABECERA: CoDocumento
                # ==========================================
                CoDocumento.objects.create(
                    id_documento=id_secuencia_str,
                    id_tipo_documento=id_tipo_doc_rc,
                    num_documento=num_documento,
                    id_tercero=recibo.id_tercero_id,
                    id_vendedor=recibo.id_vendedor or '0',
                    id_responsable=recibo.id_vendedor[:8] if recibo.id_vendedor else '0',
                    tot_documento=total_caja,
                    estado_doc='GRABADO',
                    fch_documento=fch_local,
                    fch_registro=fch_local,
                    terminal='RECIBOS MASIVOS',
                    id_sistema=tipo_doc_rc.id_sistema if tipo_doc_rc else '3',
                    id_ano=tipo_doc_rc.id_ano if tipo_doc_rc else current_year,
                    id_moneda='1',
                    vlr_moneda=decimal.Decimal('1'),
                    vlr_comision_vend=decimal.Decimal('0.00'),
                    cambios=1,
                    obser=obs_dinamica,
                    docto_alterno=recibo.num_recibo
                )
                
                # BYPASS ORA-04091 (Mutating Table Trigger INS_DOCUMENTO_CONSEC)
                if consec_id_recuperado:
                    CoDocumento.objects.filter(pk=id_secuencia_str).update(id_doc_consecutivo=consec_id_recuperado)

                # ==========================================
                # TESORERÍA: TsIngreso
                # ==========================================
                TsIngreso.objects.create(
                    id_documento=id_secuencia_str,
                    fch_entrega_ingreso=fch_local,
                    id_posfechado=None,
                    vlr_efectivo=total_efectivo,
                    vlr_cheque=total_cheque,
                    vlr_dto_financiero=tot_descuento_financiero,
                    tot_retencion_compras=tot_ret_compras,
                    tot_retencion_servicios=tot_ret_servicios,
                    tot_retencion_otros=tot_ret_otros,
                    tot_retencion_ica=tot_ret_ica,
                    tot_retencion_iva=tot_ret_iva,
                    vlr_otros=decimal.Decimal('0.00'),
                    vlr_anticipo=decimal.Decimal('0.00')
                )
                
                # INYECTAR MEDIOS DE PAGO NATIVAMENTE
                with connection.cursor() as cursor:
                    # Mapeo id_tipo_medio validado en TS_TIPO_MEDIO_PAGOS
                    if forma_pago == 'CHQ':
                        id_tipo_medio = 'C'
                    elif forma_pago in ['CONS', 'TRAN', 'TD', 'TC', 'AB']:
                        id_tipo_medio = forma_pago
                    else:
                        id_tipo_medio = 'E'
                    
                    cursor.execute("""
                        INSERT INTO ts_medio_pagos 
                        (ID_DOCUMENTO, ID_ITEM, ID_TIPO_MEDIO_PAGO, VALOR, ID_BANCO_EMITE, NUM_MEDIO, ID_PLAZA, ID_POSFECHADO, OBSER)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, [id_secuencia_str, '1', id_tipo_medio, total_caja, None, recibo.num_medio, None, None, obs_dinamica])

                # Guardar Detalle de Afectaciones A Facturas
                if afectados_inserts:
                    CoDocumentoAfectado.objects.bulk_create(afectados_inserts)
                        
                # ==========================================
                # FASE CONTABLE DINÁMICA
                # ==========================================
                if not plantilla_items_global:
                    raise Exception(f"Fallo Crítico: No hay plantilla asignada al tipo de doc RC {id_tipo_doc_rc}.")
                
                mapa_valores = {
                    'CAJA': total_caja,
                    'RTE_COMPRAS': tot_ret_compras,
                    'RTE_ICA': tot_ret_ica,
                    'RTE_IVA': tot_ret_iva,
                    'RTE_OTROS': tot_ret_otros + tot_ret_servicios,
                    'DSCTO_FINAN': tot_descuento_financiero,
                    'CXC': tot_cxc_bruto
                }

                cc_vendedor = cache_ccs.get(recibo.id_vendedor, '755') # 755 is fallback instead of '00'
                asientos = []
                idx_origen = 1
                for p_item in plantilla_items_global:
                    val = mapa_valores.get(p_item.campo, decimal.Decimal('0.00'))
                    if val > 0:
                        asientos.append(CoDocumentoItem(
                            id_documento_id=id_secuencia_str,
                            id_item=str(idx_origen),
                            id_tercero=recibo.id_tercero_id,
                            fch_documento=fch_local,
                            id_centro_costo=cc_vendedor, # Cc de vendedor válido desde cache (falla en '00' evitada)
                            debe_haber=p_item.debe_haber,
                            id_cuenta=p_item.id_cuenta,
                            siono_pendiente='N',
                            campo=p_item.campo,
                            valor=val
                        ))
                        idx_origen += 1
                        
                if asientos:
                    # Guardado masivo fuerza un INSERT evitando que el ORM evalúe un UPDATE erróneo por la llave compuesta
                    CoDocumentoItem.objects.bulk_create(asientos)

                # Sello de Sincronización
                recibo.procesado = 'S'
                recibo.save(update_fields=['procesado'])
                
                print(f"Bases Acumuladas: Caja {total_caja}, ICA: {tot_ret_ica}, IVA: {tot_ret_iva}, Descuentos: {tot_descuento_financiero}")
                print(f"Cuadratura CXC Contabilizada: {tot_cxc_bruto}")
                
                # Sello de exito
                resultados['procesados'] += 1
                
                # SAFEGUARD DRY RUN (Apagado para Producción/Afectación Real)
                # raise Exception("DRY RUN RECIBOS COMPLETADO: Forzando Rollback.")
                
        except Exception as e:
            if "DRY RUN RECIBOS COMPLETADO" in str(e):
                print(str(e))
                resultados['procesados'] += 1
            else:
                import traceback
                traceback.print_exc()
                resultados['fallidos'] += 1
                error_msg = f"Error Crítico en recibo {recibo.num_recibo}: {str(e)}"
                resultados['errores'].append(error_msg)
                print(error_msg)
                
    return resultados
