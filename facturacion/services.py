from django.db import connection, transaction
import decimal
import logging
from django.utils import timezone
import pytz
from datetime import datetime
from core.models import (
    InGravamen, SgParametro, SgUsuarioCentroCosto, CoRetencion, InArticulo,
    MvPedidosNorth, CoDocumento, CtVenta, InMovInventario, CoDocumentoItem, CoTipoDocumento,
    CoPlantilla, CoPlantillaItem, CtVendedor, InComisionGrupo, InArticuloUnidadMedida
)

logger = logging.getLogger(__name__)

def obtener_consecutivo(tipo_documento, doc_consecutivo=None):
    # Extraemos un cursor crudo 100% puro desde el socket de oracledb
    raw_cursor = connection.connection.cursor()
    print(f"Disparando PL/SQL 'CONSECUTIVO_TIPO_DOC' con TIPO: '{tipo_documento}', CONSECUTIVO: '{doc_consecutivo}'")
    try:
        out_val = raw_cursor.var(str, 50)
        raw_cursor.callproc('CONSECUTIVO_TIPO_DOC', [tipo_documento, doc_consecutivo, out_val])
        numero_generado = out_val.getvalue()
        
        # Recuperar ID del consecutivo que a veces devuelve NULL directo
        raw_cursor.execute("SELECT ID_DOC_CONSECUTIVO FROM CO_TIPO_DOC_CONSECUTIVOS WHERE ID_TIPO_DOCUMENTO = :1 AND ROWNUM = 1", [tipo_documento])
        row = raw_cursor.fetchone()
        id_consecutivo = row[0] if row else None
        
        return numero_generado, id_consecutivo
    finally:
        raw_cursor.close()

def obtener_id_secuencia():
    with connection.cursor() as cursor:
        cursor.execute("SELECT SEC_DOCUMENTO.NEXTVAL FROM DUAL")
        return cursor.fetchone()[0]

def procesar_lote_pedidos(pedidos_qs):
    resultados = {
        'exitosos': 0,
        'fallidos': 0,
        'errores': []
    }

    bogota_tz = pytz.timezone('America/Bogota')
    fch_local = datetime.now(bogota_tz).replace(tzinfo=None) # Fecha limpia local para Oracle 11g
    current_year = str(fch_local.year)

    # ==========================================
    # FASE 1: CACHÉ (Anti N+1)
    # ==========================================
    
    cache_gravamenes = {g.id_gravamen: g.porc_gravamen for g in InGravamen.objects.all()}

    parametros_claves = [
        'EXISTENCIA_BOD', 'COD_VEN_CREDITO', 'CAUSA_RTEFUENTE', 
        'RTE_COMPRAS', 'RTE_AGRICOLA', 'RTE_ICA', 'RTE_IVA', 'COD_BODEGA_DEFECTO'
    ]
    params_db = SgParametro.objects.filter(id_parametro__in=parametros_claves)
    cache_parametros = {p.id_parametro: p.vlr_chr for p in params_db}
    
    causa_rtefuente = cache_parametros.get('CAUSA_RTEFUENTE', 'NO')
    cod_ven_credito = cache_parametros.get('COD_VEN_CREDITO', '201')
    cod_bodega_defecto = cache_parametros.get('COD_BODEGA_DEFECTO', '1').lstrip('0') or '1'
    tipo_existencia = cache_parametros.get('EXISTENCIA_BOD', '1')
    
    id_rte_compras = cache_parametros.get('RTE_COMPRAS')
    id_rte_agricola = cache_parametros.get('RTE_AGRICOLA')
    id_rte_ica = cache_parametros.get('RTE_ICA')
    id_rte_iva = cache_parametros.get('RTE_IVA')

    tipo_doc_qs = CoTipoDocumento.objects.filter(
        cod_tipo_documento=cod_ven_credito, 
        id_sistema='1', 
        id_ano=current_year
    )
    
    tipo_doc_obj = None
    for td in tipo_doc_qs.iterator():
        tipo_doc_obj = td
        break

    id_tipo_doc_credito = tipo_doc_obj.id_tipo_documento if tipo_doc_obj else cod_ven_credito
    
    plantilla_qs = CoPlantilla.objects.filter(id_tipo_documento=id_tipo_doc_credito)
    plantilla_doc = None
    for pd in plantilla_qs.iterator():
        plantilla_doc = pd
        break
        
    plantilla_items_global = list(CoPlantillaItem.objects.filter(id_plantilla=plantilla_doc.id_plantilla)) if plantilla_doc else []

    vendedores_ids = list(set([p.id_vendedor for p in pedidos_qs if p.id_vendedor]))
    ccs_db = SgUsuarioCentroCosto.objects.filter(id_usuario__in=vendedores_ids)
    cache_ccs = {cc.id_usuario: cc.id_centro_costo for cc in ccs_db}

    # Caché de Comisiones
    vendedores_db = CtVendedor.objects.filter(id_vendedor__in=vendedores_ids)
    map_vend_comision = {v.id_vendedor: v.id_comision for v in vendedores_db if v.id_comision}
    comisiones_db = InComisionGrupo.objects.filter(id_comision__in=map_vend_comision.values(), id_sistema='1')
    map_comisiones = {(c.id_comision, c.id_grupo): c.porc_comision for c in comisiones_db}

    # Caché de Unidad de Medida
    articulos_pedidos = [item.id_articulo_id for p in pedidos_qs for item in p.items.all()]
    um_db = InArticuloUnidadMedida.objects.filter(id_articulo__in=articulos_pedidos)
    map_um = {}
    for um in um_db:
        if um.id_articulo not in map_um:
            map_um[um.id_articulo] = []
        map_um[um.id_articulo].append(um)

    cache_retenciones = {r.id_retencion: r for r in CoRetencion.objects.all()}

    # ==========================================
    # FASE 2: AGRUPACIÓN POR CLIENTE
    # ==========================================
    pedidos_por_cliente = {}
    for pedido in pedidos_qs:
        tercero_id = pedido.id_tercero_id if pedido.id_tercero_id else '0'
        if tercero_id not in pedidos_por_cliente:
            pedidos_por_cliente[tercero_id] = []
        pedidos_por_cliente[tercero_id].append(pedido)

    for tercero_id, lista_pedidos in pedidos_por_cliente.items():
        nums_pedidos = [p.num_pedido for p in lista_pedidos]
        str_pedidos = ", ".join(nums_pedidos)
        primer_pedido = lista_pedidos[0]

        print(f"--- INICIANDO DRY RUN PARA CLIENTE: {tercero_id} (Pedidos: {str_pedidos}) ---")
        
        try:
            with transaction.atomic():
                timestamp_actual = fch_local
                
                total_mercancia = decimal.Decimal('0.00')
                total_iva = decimal.Decimal('0.00')
                base_retencion_agricola = decimal.Decimal('0.00')
                base_retencion_compras = decimal.Decimal('0.00')
                
                total_comision_cabecera = decimal.Decimal('0.00')
                total_costo_ventas = decimal.Decimal('0.00')
                total_impoconsumo_factura = decimal.Decimal('0.00')
                
                mcia1 = mcia2 = mcia3 = mcia4 = mcia5 = decimal.Decimal('0.00')
                tot_iva2 = tot_iva3 = tot_iva4 = tot_iva5 = decimal.Decimal('0.00')
                
                lista_precio_pedido = None
                
                items_consolidados = {} # Key: (id_articulo, vlr_unitario)
                movimientos_inventario = []
                cc_vendedor = cache_ccs.get(primer_pedido.id_vendedor, '00')
                
                # Consolidar Ítems
                for pedido in lista_pedidos:
                    for item in pedido.items.select_related('id_articulo').all():
                        articulo = item.id_articulo
                        cant_a_facturar = item.cantidad
                        
                        if articulo.siono_existencia_negativa == 'N':
                            if articulo.existencia <= 0:
                                continue  
                            if cant_a_facturar > articulo.existencia:
                                cant_a_facturar = articulo.existencia
                                
                        if cant_a_facturar <= 0:
                            continue
                            
                        if not lista_precio_pedido:
                            lista_precio_pedido = item.lista

                        clave_item = (articulo.id_articulo, item.vlr_unitario)
                        if clave_item not in items_consolidados:
                            items_consolidados[clave_item] = {
                                'articulo': articulo,
                                'cantidad': decimal.Decimal('0.00'),
                                'vlr_unitario': item.vlr_unitario,
                                'lista': item.lista
                            }
                        items_consolidados[clave_item]['cantidad'] += cant_a_facturar
                
                if not items_consolidados:
                    resultados['fallidos'] += len(lista_pedidos)
                    resultados['errores'].append(f"Grupo Cliente {tercero_id} (Pedidos {str_pedidos}): Sin stock disponible o total neto en cero. Omitido.")
                    continue

                # ==========================================
                # FASE 3: REGLAS FÍSICAS Y TRIBUTARIAS 
                # ==========================================
                idx_mov = 1
                es_cliente_excluido = (getattr(primer_pedido.id_tercero, 'siono_iva', 'N') == 'S')
                
                for clave_item, data in items_consolidados.items():
                    articulo = data['articulo']
                    cant_total = data['cantidad']
                    vlr_unitario = data['vlr_unitario']
                    
                    subtotal = cant_total * vlr_unitario
                    
                    valor_impoconsumo_linea = cant_total * (articulo.impoconsumo or decimal.Decimal('0.00'))
                    
                    if es_cliente_excluido:
                        porc_iva = decimal.Decimal('0.00')
                    else:
                        porc_iva = cache_gravamenes.get(articulo.id_gravamen, decimal.Decimal('0.00'))
                        
                    vlr_iva_linea = subtotal * (porc_iva / decimal.Decimal('100.00'))
                    
                    unidad_medida = None
                    lista_um = map_um.get(articulo.id_articulo, [])
                    for um in lista_um:
                        if um.tipo_unidad_medida == 'E':
                            unidad_medida = um.id_unidad_medida
                            break
                    if not unidad_medida and lista_um:
                        unidad_medida = lista_um[0].id_unidad_medida
                        
                    if es_cliente_excluido:
                        gravamen_str = '1'
                    else:
                        gravamen_str = articulo.id_gravamen if articulo.id_gravamen else '1'
                        
                    if gravamen_str == '1':
                        mcia1 += subtotal
                    elif gravamen_str == '2':
                        mcia2 += subtotal
                        tot_iva2 += vlr_iva_linea
                    elif gravamen_str == '3':
                        mcia3 += subtotal
                        tot_iva3 += vlr_iva_linea
                    elif gravamen_str == '4':
                        mcia4 += subtotal
                        tot_iva4 += vlr_iva_linea
                    elif gravamen_str == '5':
                        mcia5 += subtotal
                        tot_iva5 += vlr_iva_linea
                    else:
                        mcia1 += subtotal

                    total_mercancia += subtotal
                    total_iva += vlr_iva_linea
                    total_impoconsumo_factura += valor_impoconsumo_linea
                    
                    if articulo.siono_producto_agricola == 'S':
                        base_retencion_agricola += subtotal
                    else:
                        base_retencion_compras += subtotal
                    
                    # Comisiones
                    id_comi = map_vend_comision.get(primer_pedido.id_vendedor)
                    porc_comision = map_comisiones.get((id_comi, articulo.id_grupo), decimal.Decimal('0.00')) if id_comi else decimal.Decimal('0.00')
                    vlr_comision_linea = (subtotal * porc_comision) / decimal.Decimal('100.00')
                    total_comision_cabecera += vlr_comision_linea

                    # Costos
                    vlr_promedio = articulo.vlr_promedio or decimal.Decimal('0.00')
                    vlr_reposicion = articulo.vlr_reposicion or decimal.Decimal('0.00')
                    total_costo_ventas += (vlr_promedio * cant_total)
                        
                    movimientos_inventario.append(InMovInventario(
                        id_item=str(idx_mov),
                        id_documento_id=None,
                        id_articulo=articulo.id_articulo,
                        fch_documento=timestamp_actual,
                        entra_sale='S',
                        cantidad=cant_total,
                        vlr_unitario=vlr_unitario,
                        vlr_iva=vlr_iva_linea,
                        impoconsumo=valor_impoconsumo_linea,
                        vlr_promedio_ini=vlr_promedio,
                        vlr_reposicion=vlr_reposicion,
                        vlr_comision_vend=vlr_comision_linea,
                        saldo=cant_total,
                        obser=articulo.nom_articulo,
                        existencia=articulo.existencia - cant_total,
                        id_centro_costo=cc_vendedor,
                        id_bodega=cod_bodega_defecto,
                        id_unidad_medida=unidad_medida,
                        id_sistema='1',
                        id_gravamen=articulo.id_gravamen
                    ))
                        
                    idx_mov += 1
                    
                    print(f"Ítem Consolidado {articulo.id_articulo}: Cantidad ajustada: {cant_total}, IVA calculado: {vlr_iva_linea}")

                # ==========================================
                # FASE 4: EL PUNTO DE NO RETORNO
                # ==========================================
                print(f"Acumulados -> Agrícola: {base_retencion_agricola}, Compras: {base_retencion_compras}")
                
                num_documento, consec_id_recuperado = obtener_consecutivo(id_tipo_doc_credito)
                print(f"Consecutivo obtenido de Oracle PL/SQL: NUM={num_documento}, ID={consec_id_recuperado}")

                if not num_documento:
                    raise Exception("Fallo Crítico: El Procedimiento de Consecutivos (CONSECUTIVO_TIPO_DOC) retornó NULL. Revisa en DB que el prefijo y tipo de documento estén asociados.")
                
                sec_id = obtener_id_secuencia()
                id_documento = str(sec_id)
                
                # ==========================================
                # FASE 5: RETENCIONES FINALES E INSERCIÓN
                # ==========================================
                tot_rte_compras = decimal.Decimal('0.00')
                tot_rte_agricola = decimal.Decimal('0.00')
                tot_rte_ica = decimal.Decimal('0.00')
                tot_rte_iva = decimal.Decimal('0.00')
                
                if causa_rtefuente == 'SI':
                    # Lógica de Cascada (ReteFuente)
                    # Paso A y B: Evaluación Agrícola y Desbordamiento
                    if id_rte_agricola and id_rte_agricola in cache_retenciones:
                        rte_obj_agr = cache_retenciones[id_rte_agricola]
                        if base_retencion_agricola >= rte_obj_agr.vlr_base:
                            tot_rte_agricola = base_retencion_agricola * (rte_obj_agr.porc_retencion / decimal.Decimal('100.00'))
                        else:
                            # Desbordamiento
                            base_retencion_compras += base_retencion_agricola
                            base_retencion_agricola = decimal.Decimal('0.00')

                    # Paso C: Evaluación Compras
                    if id_rte_compras and id_rte_compras in cache_retenciones:
                        rte_obj_com = cache_retenciones[id_rte_compras]
                        if base_retencion_compras >= rte_obj_com.vlr_base:
                            tot_rte_compras = base_retencion_compras * (rte_obj_com.porc_retencion / decimal.Decimal('100.00'))
                            
                    # Unificación de Bases para ICA e IVA
                    base_total = total_mercancia
                    
                    if id_rte_ica and id_rte_ica in cache_retenciones:
                        rte_obj_ica = cache_retenciones[id_rte_ica]
                        if base_total >= rte_obj_ica.vlr_base:
                            tot_rte_ica = base_total * (rte_obj_ica.porc_retencion / decimal.Decimal('100.00'))
                            
                    if id_rte_iva and id_rte_iva in cache_retenciones:
                        # El ReteIVA solo aplica si el cliente pertenece al régimen 3
                        cliente_obj = primer_pedido.id_tercero
                        if getattr(cliente_obj, 'id_regimen', None) == '3':
                            rte_obj_iva = cache_retenciones[id_rte_iva]
                            if base_total >= rte_obj_iva.vlr_base:
                                tot_rte_iva = total_iva * (rte_obj_iva.porc_retencion / decimal.Decimal('100.00'))

                tot_retefuente = tot_rte_compras + tot_rte_agricola + tot_rte_ica
                gran_total = total_mercancia + total_iva + total_impoconsumo_factura - (tot_retefuente + tot_rte_iva)
                
                # REDONDEO DE CABECERA (Header-Level Rounding) a Enteros más cercanos (Cero Decimales)
                total_mercancia_red = total_mercancia.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                total_iva_red = total_iva.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                
                tot_rte_compras_red = tot_rte_compras.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                tot_rte_agricola_red = tot_rte_agricola.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                tot_rte_ica_red = tot_rte_ica.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                tot_rte_iva_red = tot_rte_iva.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                
                tot_retefuente_red = tot_retefuente.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                gran_total_red = gran_total.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                
                total_comision_cabecera_red = total_comision_cabecera.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                total_costo_ventas_red = total_costo_ventas.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                total_impoconsumo_factura_red = total_impoconsumo_factura.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)
                
                print(f"Bases Finales -> Agrícola: {base_retencion_agricola}, Compras (incl. desborde): {base_retencion_compras}, Base Total (ICA/IVA): {base_total}")
                print(f"Total Factura a insertar (Redondeado): {gran_total_red} (RTE_COM: {tot_rte_compras_red}, RTE_AGR: {tot_rte_agricola_red}, RTE_ICA: {tot_rte_ica_red}, RTE_IVA: {tot_rte_iva_red})")
                
                obs_dinamica = f"Facturacion Masiva fact. No. {num_documento} de los pedido(s): {str_pedidos}"
                
                doc = CoDocumento.objects.create(
                    id_documento=id_documento,
                    id_tipo_documento=id_tipo_doc_credito,
                    num_documento=num_documento,
                    id_tercero=tercero_id,
                    id_vendedor=primer_pedido.id_vendedor or '0',
                    id_responsable=primer_pedido.id_vendedor[:8] if primer_pedido.id_vendedor else '0', 
                    tot_documento=gran_total_red,
                    estado_doc='GRABADO',
                    fch_documento=timestamp_actual,
                    fch_registro=timestamp_actual,
                    terminal='FACTURACION MASIVA',
                    id_sistema='1',
                    id_ano=current_year,
                    id_moneda='1',
                    vlr_moneda=decimal.Decimal('1'),
                    vlr_comision_vend=total_comision_cabecera_red,
                    cambios=1,
                    obser=obs_dinamica,
                    docto_alterno=primer_pedido.num_pedido
                )
                
                # BYPASS ORA-04091 (Mutating Table Trigger INS_DOCUMENTO_CONSEC) en Oracle 11g
                CoDocumento.objects.filter(pk=id_documento).update(id_doc_consecutivo=consec_id_recuperado)
                
                CtVenta.objects.create(
                    id_documento=doc,
                    tot_mercancia=total_mercancia_red,
                    tot_iva=total_iva_red,
                    tot_retefuente=tot_retefuente_red,
                    vlr_retencion_iva=tot_rte_iva_red,
                    tot_otr_impuestos=total_impoconsumo_factura_red,
                    tot_descuento=decimal.Decimal('0.00'),
                    vlr_venta=gran_total_red,
                    plazo_pago=primer_pedido.plazo_pago or 0,
                    id_precio=lista_precio_pedido or '1',
                    fch_entrega_mcia=timestamp_actual
                )
                
                for mov in movimientos_inventario:
                    mov.id_documento = doc
                InMovInventario.objects.bulk_create(movimientos_inventario)
                
                # ==========================================
                # FASE 6: ASIENTOS CONTABLES DINÁMICOS
                # ==========================================
                if not plantilla_items_global:
                    raise Exception(f"Fallo Crítico: No hay plantilla en CO_PLANTILLAS asignada al tipo de documento {id_tipo_doc_credito}.")
                
                mapa_valores = {
                    'MCIA1': mcia1.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP),
                    'MCIA2': mcia2.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP),
                    'MCIA3': mcia3.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP),
                    'MCIA4': mcia4.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP),
                    'MCIA5': mcia5.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP),
                    'TOT_IVA2': tot_iva2.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP),
                    'TOT_IVA3': tot_iva3.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP),
                    'TOT_IVA4': tot_iva4.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP),
                    'TOT_IVA5': tot_iva5.quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP),
                    'COSTO_VENTA': total_costo_ventas_red if total_costo_ventas_red > 0 else decimal.Decimal('0'),
                    'CXC': gran_total_red,
                    'TOT_IMPOCONSUMO': total_impoconsumo_factura_red,
                    'RECUPERACION_COSTO': total_costo_ventas_red if total_costo_ventas_red > 0 else decimal.Decimal('0'),
                    'RTE_COMPRAS': tot_rte_compras_red,
                    'RTE_AGRICOLA': tot_rte_agricola_red,
                    'RTE_ICA': tot_rte_ica_red,
                    'RTE_OTROS': tot_rte_iva_red
                }
                
                asientos = []
                idx_origen = 1  
                
                for p_item in plantilla_items_global:
                    val = mapa_valores.get(p_item.campo, decimal.Decimal('0'))
                    if val > 0:
                        asientos.append(CoDocumentoItem(
                            id_documento=doc,
                            id_item=str(idx_origen),
                            id_tercero=tercero_id,
                            fch_documento=timestamp_actual,
                            id_centro_costo=cc_vendedor, 
                            debe_haber=p_item.debe_haber,
                            id_cuenta=p_item.id_cuenta,
                            siono_pendiente='N',
                            campo=p_item.campo,
                            valor=val
                        ))
                        idx_origen += 1
                        
                # ---------------------------------------------
                # Balanceo Automático de Redondeos 
                # ---------------------------------------------
                if asientos:
                    suma_debitos = sum(a.valor for a in asientos if a.debe_haber == 'D')
                    suma_creditos = sum(a.valor for a in asientos if a.debe_haber == 'H')
                    diferencia = suma_debitos - suma_creditos
                    
                    if diferencia != decimal.Decimal('0.00'):
                        item_costo = next((p for p in plantilla_items_global if p.campo == 'COSTO_VENTA'), None)
                        if item_costo:
                            faltante_dh = 'H' if diferencia > 0 else 'D'
                            ajuste_val = abs(diferencia)
                            
                            print(f"Detectado descuadre contable de {ajuste_val}. Balanceando mediante COSTO_VENTA ({faltante_dh}).")
                            
                            asientos.append(CoDocumentoItem(
                                id_documento=doc,
                                id_item=str(idx_origen),
                                id_tercero=tercero_id,
                                fch_documento=timestamp_actual,
                                id_centro_costo=cc_vendedor,
                                debe_haber=faltante_dh,
                                id_cuenta=item_costo.id_cuenta,
                                siono_pendiente='N',
                                campo='COSTO_VENTA',
                                valor=ajuste_val
                            ))
                            idx_origen += 1

                    CoDocumentoItem.objects.bulk_create(asientos)
                
                for p in lista_pedidos:
                    p.procesado = 'S'
                    p.save()
                
                resultados['exitosos'] += len(lista_pedidos)
                
        except Exception as e:
            msg_error = str(e)
            logger.error(f"Capturado en Cliente {tercero_id} (Pedidos {str_pedidos}): {msg_error}")
            resultados['fallidos'] += len(lista_pedidos)
            resultados['errores'].append(f"Grupo Cliente {tercero_id} (Pedidos {str_pedidos}): {msg_error}")
            continue

    return resultados


