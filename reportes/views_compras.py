# -*- coding: utf-8 -*-
import datetime
import json
import logging
import pytz
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db.models import Q
from django.core.serializers.json import DjangoJSONEncoder
from core.models import InBodega, CoTercero, PvProveedor, CoCentroCosto
from .services_compras import ServicioComprasSugeridas, generar_orden_compra

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

    # Bodegas disponibles desde InBodega
    try:
        bodegas = list(InBodega.objects.values('id_bodega', 'nom_bodega'))
    except Exception as e:
        logger.warning(f"Error al consultar InBodega: {e}")
        bodegas = []

    # Plazo de pago por defecto desde PvProveedor según el parámetro GET 'grupo'
    plazo_pago_defecto = 30
    if grupo:
        try:
            qs_prov = PvProveedor.objects.filter(
                Q(id_tercero__id_tercero__iexact=grupo) | Q(id_tercero__nom_tercero__icontains=grupo)
            ).values('plazo_pago')
            for prov in qs_prov.iterator():
                if prov.get('plazo_pago') is not None:
                    plazo_pago_defecto = int(prov['plazo_pago'])
                break
        except Exception as e:
            logger.warning(f"Error al consultar plazo_pago en PvProveedor para '{grupo}': {e}")
            plazo_pago_defecto = 30

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    if is_ajax:
        return JsonResponse(
            {
                'items': datos_tablero,
                'filtros_activos': filtros_activos,
                'bodegas': bodegas,
                'plazo_pago_defecto': plazo_pago_defecto,
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
        'bodegas': bodegas,
        'plazo_pago_defecto': plazo_pago_defecto,
    }

    return render(request, 'north_comercial/tableros/compras_sugeridas.html', context)


@require_POST
def api_guardar_orden_compra(request):
    """
    Endpoint transaccional para guardar una Orden de Compra desde el Tablero de Compras Sugeridas (SPEC-007).
    """
    try:
        payload = json.loads(request.body)
        user_obj = getattr(request, 'user', None)
        raw_user_id = getattr(user_obj, 'id', None)
        id_usuario = str(raw_user_id) if raw_user_id is not None else '1'

        resultado = generar_orden_compra(payload, id_usuario=id_usuario)
        return JsonResponse(resultado)
    except Exception as e:
        logger.exception(f"Error general en api_guardar_orden_compra: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def crear_orden_compra(request):
    """
    Vista para la pantalla de creación / confirmación de Orden de Compra (SPEC-007).
    """
    current_year = str(datetime.datetime.now(pytz.timezone('America/Bogota')).year)
    bodegas = list(InBodega.objects.values('id_bodega', 'nom_bodega'))
    centros_costo = list(
        CoCentroCosto.objects.filter(id_ano=current_year).values('id_centro_costo', 'cod_centro_costo', 'nom_centro_costo')
    )

    qs_proveedores = (
        PvProveedor.objects.filter(activo_inactivo='A')
        .select_related('id_tercero')
        .values(
            'id_tercero',
            'id_tercero__cod_tercero',
            'id_tercero__nom_tercero',
            'plazo_pago',
            'id_tercero__siono_iva',
        )
    )
    proveedores = [
        {
            'id_tercero': p['id_tercero'],
            'cod_tercero': p['id_tercero__cod_tercero'],
            'nom_tercero': p['id_tercero__nom_tercero'],
            'plazo_pago': p['plazo_pago'],
            'siono_iva': p['id_tercero__siono_iva'] or 'N',
        }
        for p in qs_proveedores
    ]

    contexto = {
        'bodegas': bodegas,
        'centros_costo': centros_costo,
        'proveedores': proveedores,
    }
    return render(request, 'reportes/crear_orden_compra.html', contexto)


