# -*- coding: utf-8 -*-
import json
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.core.serializers.json import DjangoJSONEncoder
from .services_compras import ServicioComprasSugeridas

logger = logging.getLogger(__name__)


def _parse_int_param(raw_val, default: int) -> int:
    if raw_val is None or str(raw_val).strip() == '':
        return default
    try:
        val = int(str(raw_val).strip())
        return val if val >= 0 else default
    except (ValueError, TypeError):
        return default


def tablero_compras_sugeridas(request):
    """
    Controlador para el Tablero de Compras Inteligentes / Compras Sugeridas (SPEC-007).
    Cumple con ADR-015 empaquetando el dataset inicial en JSON con DjangoJSONEncoder.
    """
    raw_grupo = request.GET.get('grupo') or request.GET.get('grupo_codigo') or ''
    grupo = str(raw_grupo).strip()
    if grupo.upper() == 'TODOS':
        grupo = ''

    dias_sugeridos = _parse_int_param(request.GET.get('dias_sugeridos'), default=30)
    stock_minimo = _parse_int_param(request.GET.get('stock_minimo'), default=15)

    filtros_activos = {
        'grupo_codigo': grupo,
        'grupo': grupo,
        'dias_sugeridos': dias_sugeridos,
        'stock_minimo': stock_minimo,
    }

    servicio = ServicioComprasSugeridas(
        filtros={
            'grupo_codigo': grupo,
            'dias_sugeridos': dias_sugeridos,
            'stock_minimo': stock_minimo,
        }
    )

    filtros_disponibles = servicio.get_filtros_disponibles()
    datos_tablero = servicio.get_datos_tablero()

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    if is_ajax:
        return JsonResponse(
            {
                'items': datos_tablero,
                'filtros_activos': filtros_activos,
            },
            encoder=DjangoJSONEncoder,
        )

    datos_iniciales_json = json.dumps(datos_tablero, cls=DjangoJSONEncoder)

    context = {
        'datos_iniciales_json': datos_iniciales_json,
        'contexto_json': datos_iniciales_json,
        'filtros_disponibles': filtros_disponibles,
        'grupos': filtros_disponibles,
        'opciones_grupos': filtros_disponibles,
        'filtros_activos': filtros_activos,
        'filtros_actuales': filtros_activos,
        'filtros': {
            'grupos': filtros_disponibles,
            'grupo_codigo': grupo,
            'grupo': grupo,
            'dias_sugeridos': dias_sugeridos,
            'stock_minimo': stock_minimo,
        },
        'grupo': grupo,
        'grupo_codigo': grupo,
        'dias_sugeridos': dias_sugeridos,
        'stock_minimo': stock_minimo,
    }

    return render(request, 'north_comercial/tableros/compras_sugeridas.html', context)
