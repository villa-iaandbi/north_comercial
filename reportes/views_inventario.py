# -*- coding: utf-8 -*-
import json
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.core.serializers.json import DjangoJSONEncoder
from .services_inventario import InventarioService

logger = logging.getLogger(__name__)


def control_inventarios_view(request):
    """
    Vista de controlador para el Tablero de Control de Inventarios (SPEC-004).
    Maneja la extracción de filtros, consulta del servicio, serialización JSON para SSR (ADR-015)
    y soporte de peticiones asíncronas AJAX.
    """
    # 1. Extracción de filtros dinámicos (grupo, linea, estado)
    filtros_aplicados = {}

    grupo_val = request.GET.get('grupo')
    if grupo_val and str(grupo_val).strip() and str(grupo_val).strip().upper() != 'TODOS':
        filtros_aplicados['grupo'] = str(grupo_val).strip()

    linea_val = request.GET.get('linea')
    if linea_val and str(linea_val).strip() and str(linea_val).strip().upper() != 'TODOS':
        filtros_aplicados['linea'] = str(linea_val).strip()

    estado_val = request.GET.get('estado')
    if estado_val and str(estado_val).strip() and str(estado_val).strip().upper() != 'TODOS':
        filtros_aplicados['estado'] = str(estado_val).strip()

    filtros_actuales = {
        'grupo': filtros_aplicados.get('grupo', ''),
        'linea': filtros_aplicados.get('linea', ''),
        'estado': filtros_aplicados.get('estado', ''),
    }

    # 2. Llamadas a la capa de servicio
    dashboard_data = InventarioService.get_dashboard_data(filtros_aplicados)

    if hasattr(InventarioService, 'get_filtros_disponibles'):
        filtros_disponibles = InventarioService.get_filtros_disponibles()
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
        'filtros_disponibles': filtros_disponibles,
        'filtros': filtros_disponibles,    # Alias
        'filtros_actuales': filtros_actuales,
        'filtros_aplicados': filtros_aplicados,
    }

    return render(request, 'reportes/control_inventarios.html', context)
