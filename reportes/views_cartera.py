# -*- coding: utf-8 -*-
import json
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.core.serializers.json import DjangoJSONEncoder
from .services_cartera import CarteraService

logger = logging.getLogger(__name__)


def control_cartera_view(request):
    """
    Controlador para el Tablero de Cartera y Recaudos (SPEC-005).
    Maneja la extracción de filtros, consulta del servicio CarteraService,
    soporte de actualización asíncrona vía AJAX y serialización SSR (ADR-015).
    """
    # 1. Extracción de filtros dinámicos (vendedor, cliente, estado_mora)
    filtros_aplicados = {}

    vendedor_val = request.GET.get('vendedor') or request.GET.get('vendedor_codigo')
    if vendedor_val and str(vendedor_val).strip() and str(vendedor_val).strip().upper() != 'TODOS':
        filtros_aplicados['vendedor_codigo'] = str(vendedor_val).strip()

    cliente_val = request.GET.get('cliente') or request.GET.get('cliente_codigo')
    if cliente_val and str(cliente_val).strip() and str(cliente_val).strip().upper() != 'TODOS':
        filtros_aplicados['cliente_codigo'] = str(cliente_val).strip()

    estado_val = request.GET.get('estado_mora') or request.GET.get('estado')
    if estado_val and str(estado_val).strip() and str(estado_val).strip().upper() != 'TODOS':
        filtros_aplicados['estado_mora'] = str(estado_val).strip()

    filtros_actuales = {
        'vendedor': filtros_aplicados.get('vendedor_codigo', ''),
        'cliente': filtros_aplicados.get('cliente_codigo', ''),
        'estado_mora': filtros_aplicados.get('estado_mora', ''),
    }

    # 2. Invocación de la capa de servicio
    dashboard_data = CarteraService.get_dashboard_data(filtros_aplicados)

    if hasattr(CarteraService, 'get_filtros_disponibles'):
        filtros_disponibles = CarteraService.get_filtros_disponibles()
    else:
        filtros_disponibles = {}

    # 3. Soporte para actualización asíncrona vía AJAX
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    if is_ajax:
        return JsonResponse(dashboard_data, encoder=DjangoJSONEncoder)

    # 4. Serialización JSON segura para inyección SSR (ADR-015)
    dashboard_json = json.dumps(dashboard_data, cls=DjangoJSONEncoder)

    context = {
        'dashboard_json': dashboard_json,
        'contexto_json': dashboard_json,  # Alias de compatibilidad SSR
        'top_clientes': dashboard_data.get('grafico_clientes', {}).get('items', []),
        'top_clientes_list': dashboard_data.get('grafico_clientes', {}).get('items', []),
        'top_vendedores': dashboard_data.get('grafico_vendedores', {}).get('items', []),
        'top_vendedores_list': dashboard_data.get('grafico_vendedores', {}).get('items', []),
        'filtros_disponibles': filtros_disponibles,
        'filtros': filtros_disponibles,    # Alias
        'filtros_actuales': filtros_actuales,
        'filtros_aplicados': filtros_aplicados,
    }

    return render(request, 'reportes/control_cartera.html', context)
