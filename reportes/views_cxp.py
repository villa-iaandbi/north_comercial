# -*- coding: utf-8 -*-
import json
import logging
from decimal import Decimal
from django.shortcuts import render
from django.http import JsonResponse
from django.core.serializers.json import DjangoJSONEncoder
from .services_cxp import CxpService

logger = logging.getLogger(__name__)


def control_cxp_view(request):
    """
    Controlador para el Tablero de Cuentas por Pagar - CxP (SPEC-006).
    Extrae filtros, invoca CxpService.get_dashboard_data() y serializa
    arreglos con DjangoJSONEncoder cumpliendo ADR-015.
    """
    filtros_aplicados = {}

    proveedor_val = request.GET.get('proveedor') or request.GET.get('proveedor_codigo')
    if proveedor_val and str(proveedor_val).strip() and str(proveedor_val).strip().upper() != 'TODOS':
        filtros_aplicados['proveedor_codigo'] = str(proveedor_val).strip()

    estado_val = request.GET.get('estado_mora') or request.GET.get('estado')
    if estado_val and str(estado_val).strip() and str(estado_val).strip().upper() != 'TODOS':
        filtros_aplicados['estado_mora'] = str(estado_val).strip()

    filtros_actuales = {
        'proveedor': filtros_aplicados.get('proveedor_codigo', ''),
        'estado_mora': filtros_aplicados.get('estado_mora', ''),
    }

    dashboard_data = CxpService.get_dashboard_data(filtros_aplicados)
    filtros_disponibles = (
        CxpService.get_filtros_disponibles()
        if hasattr(CxpService, 'get_filtros_disponibles')
        else {}
    )

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    if is_ajax:
        return JsonResponse(dashboard_data, encoder=DjangoJSONEncoder)

    dashboard_json = json.dumps(dashboard_data, cls=DjangoJSONEncoder)
    pago_legal_raw = dashboard_data.get('pago_legal', [0, 0, 0, 0, 0])
    pago_inteligente_raw = dashboard_data.get('pago_inteligente', [0, 0, 0, 0, 0])
    top_proveedores_raw = dashboard_data.get('top_proveedores', [])
    radar_descuentos_raw = dashboard_data.get('radar_descuentos', [])
    cronograma_regular_raw = dashboard_data.get('cronograma_regular', [])
    recaudo_prom_semanal = dashboard_data.get('recaudo_promedio_semanal', Decimal('0.0'))

    context = {
        'dashboard_json': dashboard_json,
        'contexto_json': dashboard_json,
        # Escalares directos para plantillas Django
        'deuda_total': dashboard_data.get('deuda_total', Decimal('0.0')),
        'cxp_vencida': dashboard_data.get('cxp_vencida', Decimal('0.0')),
        'porcentaje_vencida': dashboard_data.get('porcentaje_vencida', Decimal('0.0')),
        'ahorro_potencial': dashboard_data.get('ahorro_potencial', Decimal('0.0')),
        'dpo': dashboard_data.get('dpo', Decimal('0.0')),
        'dpo_promedio': dashboard_data.get('dpo_promedio', Decimal('0.0')),
        'recaudo_promedio_semanal': recaudo_prom_semanal,
        'promedio_ingreso_semanal': recaudo_prom_semanal,
        'kpis': dashboard_data.get('kpis', {}),
        # Arreglos serializados con DjangoJSONEncoder (ADR-015)
        'pago_estrategico': json.dumps(pago_legal_raw, cls=DjangoJSONEncoder),
        'pago_legal': json.dumps(pago_legal_raw, cls=DjangoJSONEncoder),
        'pago_inteligente': json.dumps(pago_inteligente_raw, cls=DjangoJSONEncoder),
        'top_proveedores': json.dumps(top_proveedores_raw, cls=DjangoJSONEncoder),
        'radar_descuentos': json.dumps(radar_descuentos_raw, cls=DjangoJSONEncoder),
        'cronograma_regular': json.dumps(cronograma_regular_raw, cls=DjangoJSONEncoder),
        # Listas nativas auxiliares de respaldo
        'pago_estrategico_list': pago_legal_raw,
        'pago_legal_list': pago_legal_raw,
        'pago_inteligente_list': pago_inteligente_raw,
        'top_proveedores_list': top_proveedores_raw,
        'radar_descuentos_list': radar_descuentos_raw,
        'cronograma_regular_list': cronograma_regular_raw,
        'filtros_disponibles': filtros_disponibles,
        'filtros': filtros_disponibles,
        'filtros_actuales': filtros_actuales,
        'filtros_aplicados': filtros_aplicados,
    }

    return render(request, 'reportes/control_cxp.html', context)
