import decimal
import logging

from core.models import CoDocumentoItem

logger = logging.getLogger(__name__)

D0 = decimal.Decimal('0.00')
D100 = decimal.Decimal('100.00')
D1 = decimal.Decimal('1')


class GeneradorContableUniversal:
    """
    Motor contable reutilizable. Migra la lógica de las FASES 5 y 6 de
    facturacion.services.procesar_lote_pedidos: cascada de retenciones,
    redondeo de cabecera (enteros, ROUND_HALF_UP), mapeo de plantilla y
    balanceo automático de descuadres contra COSTO_VENTA.

    Este módulo NO persiste nada: devuelve objetos CoDocumentoItem
    instanciados (sin guardar) y un dict con los totales calculados.
    """

    @staticmethod
    def _redondear(valor):
        return valor.quantize(D1, rounding=decimal.ROUND_HALF_UP)

    @classmethod
    def generar_asientos(cls, doc_obj, base_dict: dict, params_tributarios: dict,
                         plantilla_items: list, campo_ajuste: str = 'OTROS') -> tuple[list, dict]:
        """
        base_dict (sumatorias y datos clave):
            mcia1..mcia5, tot_iva2..tot_iva5, total_mercancia, total_iva,
            total_impoconsumo (o total_impoconsumo_factura), total_costo_ventas,
            total_comision_cabecera, base_retencion_agricola,
            base_retencion_compras, id_tercero, id_centro_costo, timestamp

        params_tributarios:
            cache_retenciones, causa_rtefuente, id_rte_agricola, id_rte_compras,
            id_rte_ica, id_rte_iva, id_regimen_cliente

        Retorna (lista_asientos, dict_totales).
        """
        # ------------------------------------------------------------------
        # Lectura de entradas
        # ------------------------------------------------------------------
        mcia1 = base_dict.get('mcia1', D0)
        mcia2 = base_dict.get('mcia2', D0)
        mcia3 = base_dict.get('mcia3', D0)
        mcia4 = base_dict.get('mcia4', D0)
        mcia5 = base_dict.get('mcia5', D0)
        tot_iva2 = base_dict.get('tot_iva2', D0)
        tot_iva3 = base_dict.get('tot_iva3', D0)
        tot_iva4 = base_dict.get('tot_iva4', D0)
        tot_iva5 = base_dict.get('tot_iva5', D0)

        total_mercancia = base_dict.get('total_mercancia', D0)
        total_iva = base_dict.get('total_iva', D0)
        if 'total_impoconsumo' in base_dict:
            total_impoconsumo_factura = base_dict['total_impoconsumo']
        else:
            total_impoconsumo_factura = base_dict.get('total_impoconsumo_factura', D0)
        total_costo_ventas = base_dict.get('total_costo_ventas', D0)
        total_comision_cabecera = base_dict.get('total_comision_cabecera', D0)

        base_retencion_agricola = base_dict.get('base_retencion_agricola', D0)
        base_retencion_compras = base_dict.get('base_retencion_compras', D0)

        tercero_id = base_dict.get('id_tercero')
        cc_vendedor = base_dict.get('id_centro_costo', '00')
        timestamp_actual = base_dict.get('timestamp')

        cache_retenciones = params_tributarios.get('cache_retenciones') or {}
        causa_rtefuente = params_tributarios.get('causa_rtefuente', 'NO')
        id_rte_agricola = params_tributarios.get('id_rte_agricola')
        id_rte_compras = params_tributarios.get('id_rte_compras')
        id_rte_ica = params_tributarios.get('id_rte_ica')
        id_rte_iva = params_tributarios.get('id_rte_iva')
        id_regimen_cliente = params_tributarios.get('id_regimen_cliente')

        # ==================================================================
        # FASE 5: RETENCIONES FINALES
        # ==================================================================
        tot_rte_compras = D0
        tot_rte_agricola = D0
        tot_rte_ica = D0
        tot_rte_iva = D0
        base_total = total_mercancia

        if causa_rtefuente == 'SI':
            # Lógica de Cascada (ReteFuente)
            # Paso A y B: Evaluación Agrícola y Desbordamiento
            if id_rte_agricola and id_rte_agricola in cache_retenciones:
                rte_obj_agr = cache_retenciones[id_rte_agricola]
                if base_retencion_agricola >= rte_obj_agr.vlr_base:
                    tot_rte_agricola = base_retencion_agricola * (rte_obj_agr.porc_retencion / D100)
                else:
                    # Desbordamiento
                    base_retencion_compras += base_retencion_agricola
                    base_retencion_agricola = D0

            # Paso C: Evaluación Compras
            if id_rte_compras and id_rte_compras in cache_retenciones:
                rte_obj_com = cache_retenciones[id_rte_compras]
                if base_retencion_compras >= rte_obj_com.vlr_base:
                    tot_rte_compras = base_retencion_compras * (rte_obj_com.porc_retencion / D100)

            # Unificación de Bases para ICA e IVA
            base_total = total_mercancia

            if id_rte_ica and id_rte_ica in cache_retenciones:
                rte_obj_ica = cache_retenciones[id_rte_ica]
                if base_total >= rte_obj_ica.vlr_base:
                    tot_rte_ica = base_total * (rte_obj_ica.porc_retencion / D100)

            if id_rte_iva and id_rte_iva in cache_retenciones:
                # El ReteIVA solo aplica si el cliente pertenece al régimen 3
                if id_regimen_cliente == '3':
                    rte_obj_iva = cache_retenciones[id_rte_iva]
                    if base_total >= rte_obj_iva.vlr_base:
                        tot_rte_iva = total_iva * (rte_obj_iva.porc_retencion / D100)

        tot_retefuente = tot_rte_compras + tot_rte_agricola + tot_rte_ica
        gran_total = total_mercancia + total_iva + total_impoconsumo_factura - (tot_retefuente + tot_rte_iva)

        # REDONDEO DE CABECERA (Header-Level Rounding) a enteros (cero decimales)
        total_mercancia_red = cls._redondear(total_mercancia)
        total_iva_red = cls._redondear(total_iva)

        tot_rte_compras_red = cls._redondear(tot_rte_compras)
        tot_rte_agricola_red = cls._redondear(tot_rte_agricola)
        tot_rte_ica_red = cls._redondear(tot_rte_ica)
        tot_rte_iva_red = cls._redondear(tot_rte_iva)

        tot_retefuente_red = cls._redondear(tot_retefuente)
        gran_total_red = cls._redondear(gran_total)

        total_comision_cabecera_red = cls._redondear(total_comision_cabecera)
        total_costo_ventas_red = cls._redondear(total_costo_ventas)
        total_impoconsumo_factura_red = cls._redondear(total_impoconsumo_factura)

        logger.info(
            "Bases Finales -> Agrícola: %s, Compras (incl. desborde): %s, Base Total (ICA/IVA): %s",
            base_retencion_agricola, base_retencion_compras, base_total
        )
        logger.info(
            "Total Factura (Redondeado): %s (RTE_COM: %s, RTE_AGR: %s, RTE_ICA: %s, RTE_IVA: %s)",
            gran_total_red, tot_rte_compras_red, tot_rte_agricola_red, tot_rte_ica_red, tot_rte_iva_red
        )

        # ==================================================================
        # FASE 6: ASIENTOS CONTABLES DINÁMICOS
        # ==================================================================
        if not plantilla_items:
            raise Exception(
                "Fallo Crítico: No hay plantilla en CO_PLANTILLAS asignada al tipo de documento "
                f"{getattr(doc_obj, 'id_tipo_documento', '')}."
            )

        mapa_valores = {
            'MCIA1': cls._redondear(mcia1),
            'MCIA2': cls._redondear(mcia2),
            'MCIA3': cls._redondear(mcia3),
            'MCIA4': cls._redondear(mcia4),
            'MCIA5': cls._redondear(mcia5),
            'TOT_IVA2': cls._redondear(tot_iva2),
            'TOT_IVA3': cls._redondear(tot_iva3),
            'TOT_IVA4': cls._redondear(tot_iva4),
            'TOT_IVA5': cls._redondear(tot_iva5),
            'COSTO_VENTA': total_costo_ventas_red if total_costo_ventas_red > 0 else decimal.Decimal('0'),
            'CXC': gran_total_red,
            'TOT_IMPOCONSUMO': total_impoconsumo_factura_red,
            'RECUPERACION_COSTO': total_costo_ventas_red if total_costo_ventas_red > 0 else decimal.Decimal('0'),
            'RTE_COMPRAS': tot_rte_compras_red,
            'RTE_AGRICOLA': tot_rte_agricola_red,
            'RTE_ICA': tot_rte_ica_red,
            'RTE_OTROS': tot_rte_iva_red,
        }

        asientos = []
        idx_origen = 1

        # Frecuencia de cada campo en la plantilla (para sufijos de unicidad)
        frecuencia_campos = {}
        for p_item in plantilla_items:
            frecuencia_campos[p_item.campo] = frecuencia_campos.get(p_item.campo, 0) + 1

        contador_uso = {}

        for p_item in plantilla_items:
            contador_uso[p_item.campo] = contador_uso.get(p_item.campo, 0) + 1
            if frecuencia_campos[p_item.campo] > 1:
                campo_guardar = f"{p_item.campo}-{contador_uso[p_item.campo]}"
            else:
                campo_guardar = p_item.campo

            val = mapa_valores.get(p_item.campo, decimal.Decimal('0'))
            if val > 0:
                asientos.append(CoDocumentoItem(
                    id_documento=doc_obj,
                    id_item=str(idx_origen),
                    id_tercero=tercero_id,
                    fch_documento=timestamp_actual,
                    id_centro_costo=cc_vendedor,
                    debe_haber=p_item.debe_haber,
                    id_cuenta=p_item.id_cuenta,
                    siono_pendiente='N',
                    campo=campo_guardar,
                    valor=val
                ))
                idx_origen += 1

        # ---------------------------------------------
        # Balanceo Automático de Redondeos
        # ---------------------------------------------
        if asientos and campo_ajuste is not None:
            suma_debitos = sum(a.valor for a in asientos if a.debe_haber == 'D')
            suma_creditos = sum(a.valor for a in asientos if a.debe_haber == 'H')
            diferencia = suma_debitos - suma_creditos

            if diferencia != D0:
                item_costo = next((p for p in plantilla_items if p.campo == campo_ajuste), None)
                if item_costo:
                    faltante_dh = 'H' if diferencia > 0 else 'D'
                    ajuste_val = abs(diferencia)

                    logger.info(
                        "Detectado descuadre contable de %s. Balanceando mediante %s (%s).",
                        ajuste_val, campo_ajuste, faltante_dh
                    )

                    asientos.append(CoDocumentoItem(
                        id_documento=doc_obj,
                        id_item=str(idx_origen),
                        id_tercero=tercero_id,
                        fch_documento=timestamp_actual,
                        id_centro_costo=cc_vendedor,
                        debe_haber=faltante_dh,
                        id_cuenta=item_costo.id_cuenta,
                        siono_pendiente='N',
                        campo=campo_ajuste,
                        valor=ajuste_val
                    ))
                    idx_origen += 1

        dict_totales = {
            # Valores sin redondear
            'tot_rte_compras': tot_rte_compras,
            'tot_rte_agricola': tot_rte_agricola,
            'tot_rte_ica': tot_rte_ica,
            'tot_rte_iva': tot_rte_iva,
            'tot_retefuente': tot_retefuente,
            'gran_total': gran_total,
            'base_retencion_agricola': base_retencion_agricola,
            'base_retencion_compras': base_retencion_compras,
            'base_total': base_total,
            # Valores redondeados (cabecera)
            'total_mercancia_red': total_mercancia_red,
            'total_iva_red': total_iva_red,
            'tot_rte_compras_red': tot_rte_compras_red,
            'tot_rte_agricola_red': tot_rte_agricola_red,
            'tot_rte_ica_red': tot_rte_ica_red,
            'tot_rte_iva_red': tot_rte_iva_red,
            'tot_retefuente_red': tot_retefuente_red,
            'gran_total_red': gran_total_red,
            'total_comision_cabecera_red': total_comision_cabecera_red,
            'total_costo_ventas_red': total_costo_ventas_red,
            'total_impoconsumo_factura_red': total_impoconsumo_factura_red,
        }

        return asientos, dict_totales
