# -*- coding: utf-8 -*-
"""
SPEC-013: CRUD de parámetros de Información Exógena DIAN (tabla CO_PARAM_EXOGENA).

Convenciones del proyecto (Oracle 11g):
  * No se usa slicing de QuerySets (.first(), .get(), [:n], .exists()) porque Django lo traduce a
    FETCH FIRST ... ROWS ONLY (Oracle 12c+) y en 11g produce ORA-00933. La paginación se hace
    sobre la lista evaluada (la tabla de parámetros es pequeña) y las búsquedas puntuales con
    list(queryset).
  * El borrado es LÓGICO: ESTADO = 'I'. Se ofrece reactivar ('A').
"""
import json
import logging

from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import services_exogena
from .forms import ParametroExogenaForm
from .models import ParametroExogena

logger = logging.getLogger(__name__)

POR_PAGINA_DEFECTO = 25
POR_PAGINA_PERMITIDOS = (25, 50, 100)
MENSAJE_ERROR_BD = 'No fue posible completar la operación en la base de datos. Intente de nuevo.'


# ---------------------------------------------------------------------- #
# Utilidades internas
# ---------------------------------------------------------------------- #
def _serializar(parametro):
    """Convierte un ParametroExogena en un dict JSON-serializable (incluye URLs de acción)."""
    tope = parametro.tope_cuantia_menor
    return {
        'id': parametro.pk,
        'ano_fiscal': parametro.ano_fiscal or '',
        'formato_dian': parametro.formato_dian or '',
        'concepto_dian': parametro.concepto_dian or '',
        'cuenta_contable': parametro.cuenta_contable or '',
        'origen_cubo': parametro.origen_cubo or '',
        'origen_cubo_display': parametro.get_origen_cubo_display() if parametro.origen_cubo else '',
        'columna_xml': parametro.columna_xml or '',
        'tope_cuantia_menor': str(tope) if tope is not None else '0',
        'estado': parametro.estado or '',
        'observaciones': parametro.observaciones or '',
        'url_editar': reverse('reportes:exogena_parametro_editar', args=[parametro.pk]),
        'url_inactivar': reverse('reportes:exogena_parametro_inactivar', args=[parametro.pk]),
        'url_reactivar': reverse('reportes:exogena_parametro_reactivar', args=[parametro.pk]),
    }


def _obtener_parametro(pk):
    """Busca por PK sin .get()/.first() (compatibilidad Oracle 11g). Retorna None si no existe."""
    resultado = list(ParametroExogena.objects.filter(pk=pk))
    return resultado[0] if resultado else None


def _leer_json(request):
    """Parsea el cuerpo JSON de la petición. Lanza ValueError si es inválido."""
    try:
        cuerpo = json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('El cuerpo de la petición no es un JSON válido.')
    if not isinstance(cuerpo, dict):
        raise ValueError('El cuerpo de la petición debe ser un objeto JSON.')
    return cuerpo


def _errores_form(form):
    """Aplana los errores del formulario a {campo: 'mensaje1 mensaje2'}; '__all__' = errores globales."""
    errores = {}
    for campo, lista in form.errors.get_json_data().items():
        errores[campo] = ' '.join(e['message'] for e in lista)
    return errores


def _consultar(request):
    """
    Aplica filtros (q, ano, formato, estado) y paginación a partir de request.GET.
    Retorna el payload completo que consume la interfaz Alpine.js.
    """
    q = (request.GET.get('q') or '').strip()
    ano = (request.GET.get('ano') or '').strip()
    formato = (request.GET.get('formato') or '').strip()
    estado = (request.GET.get('estado') or ParametroExogena.ESTADO_ACTIVO).strip().upper()

    queryset = ParametroExogena.objects.all()

    if q:
        queryset = queryset.filter(
            Q(formato_dian__icontains=q) |
            Q(cuenta_contable__icontains=q) |
            Q(concepto_dian__icontains=q)
        )
    if ano:
        queryset = queryset.filter(ano_fiscal=ano)
    if formato:
        queryset = queryset.filter(formato_dian=formato)
    if estado in (ParametroExogena.ESTADO_ACTIVO, ParametroExogena.ESTADO_INACTIVO):
        queryset = queryset.filter(estado=estado)
    else:
        estado = 'TODOS'

    queryset = queryset.order_by('-ano_fiscal', 'formato_dian', 'concepto_dian', 'cuenta_contable', 'id_parametro')

    try:
        por_pagina = int(request.GET.get('por_pagina', POR_PAGINA_DEFECTO))
    except (TypeError, ValueError):
        por_pagina = POR_PAGINA_DEFECTO
    if por_pagina not in POR_PAGINA_PERMITIDOS:
        por_pagina = POR_PAGINA_DEFECTO

    paginador = Paginator(list(queryset), por_pagina)
    pagina = paginador.get_page(request.GET.get('page'))  # tolera page inválida/fuera de rango

    # Opciones de los dropdowns: sobre toda la tabla (independientes de los filtros activos).
    anos = sorted(
        {a for a in ParametroExogena.objects.values_list('ano_fiscal', flat=True).distinct() if a},
        reverse=True,
    )
    formatos = sorted(
        {f for f in ParametroExogena.objects.values_list('formato_dian', flat=True).distinct() if f}
    )

    return {
        'ok': True,
        'items': [_serializar(p) for p in pagina.object_list],
        'paginacion': {
            'pagina': pagina.number,
            'total_paginas': paginador.num_pages,
            'total_registros': paginador.count,
            'por_pagina': por_pagina,
            'tiene_anterior': pagina.has_previous(),
            'tiene_siguiente': pagina.has_next(),
        },
        'filtros': {'q': q, 'ano': ano, 'formato': formato, 'estado': estado},
        'opciones': {'anos': anos, 'formatos': formatos},
    }


# ---------------------------------------------------------------------- #
# Vistas
# ---------------------------------------------------------------------- #
@ensure_csrf_cookie
@require_GET
def exogena_parametros_view(request):
    """Página principal: sirve el HTML y la primera carga de datos (sin llamada AJAX inicial)."""
    try:
        datos_iniciales = _consultar(request)
    except DatabaseError:
        logger.exception('SPEC-013: error consultando CO_PARAM_EXOGENA')
        datos_iniciales = {
            'ok': False,
            'error': MENSAJE_ERROR_BD,
            'items': [],
            'paginacion': {'pagina': 1, 'total_paginas': 1, 'total_registros': 0,
                           'por_pagina': POR_PAGINA_DEFECTO, 'tiene_anterior': False, 'tiene_siguiente': False},
            'filtros': {'q': '', 'ano': '', 'formato': '', 'estado': ParametroExogena.ESTADO_ACTIVO},
            'opciones': {'anos': [], 'formatos': []},
        }

    contexto = {
        'form': ParametroExogenaForm(),
        'datos_iniciales': datos_iniciales,
        'url_api_lista': reverse('reportes:exogena_parametros_api'),
        'url_api_crear': reverse('reportes:exogena_parametro_crear'),
    }
    return render(request, 'reportes/configuracion_exogena.html', contexto)


@require_GET
def exogena_parametros_api(request):
    """JSON: listado filtrado y paginado (buscador, año fiscal, formato DIAN, estado)."""
    try:
        return JsonResponse(_consultar(request))
    except DatabaseError:
        logger.exception('SPEC-013: error consultando CO_PARAM_EXOGENA')
        return JsonResponse({'ok': False, 'error': MENSAJE_ERROR_BD}, status=500)


@require_POST
def exogena_parametro_crear(request):
    """JSON: crea un parámetro. Responde 400 con errores por campo si la validación falla."""
    try:
        datos = _leer_json(request)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    try:
        form = ParametroExogenaForm(datos)
        if not form.is_valid():
            return JsonResponse({'ok': False, 'errores': _errores_form(form)}, status=400)
        with transaction.atomic():
            parametro = form.save()
    except DatabaseError:
        logger.exception('SPEC-013: error creando parámetro de exógena')
        return JsonResponse({'ok': False, 'error': MENSAJE_ERROR_BD}, status=500)

    return JsonResponse({'ok': True, 'mensaje': 'Parámetro creado correctamente.',
                         'item': _serializar(parametro)}, status=201)


@require_POST
def exogena_parametro_editar(request, pk):
    """JSON: actualiza un parámetro existente."""
    try:
        datos = _leer_json(request)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    try:
        parametro = _obtener_parametro(pk)
        if parametro is None:
            return JsonResponse({'ok': False, 'error': 'El parámetro no existe.'}, status=404)

        form = ParametroExogenaForm(datos, instance=parametro)
        if not form.is_valid():
            return JsonResponse({'ok': False, 'errores': _errores_form(form)}, status=400)
        with transaction.atomic():
            parametro = form.save()
    except DatabaseError:
        logger.exception('SPEC-013: error editando parámetro de exógena id=%s', pk)
        return JsonResponse({'ok': False, 'error': MENSAJE_ERROR_BD}, status=500)

    return JsonResponse({'ok': True, 'mensaje': 'Parámetro actualizado correctamente.',
                         'item': _serializar(parametro)})


def _cambiar_estado(pk, nuevo_estado, mensaje_ok):
    """Borrado/reactivación LÓGICA: UPDATE ESTADO sin tocar el resto de columnas."""
    try:
        with transaction.atomic():
            filas = ParametroExogena.objects.filter(pk=pk).update(estado=nuevo_estado)
    except DatabaseError:
        logger.exception('SPEC-013: error cambiando estado a %s del parámetro id=%s', nuevo_estado, pk)
        return JsonResponse({'ok': False, 'error': MENSAJE_ERROR_BD}, status=500)

    if not filas:
        return JsonResponse({'ok': False, 'error': 'El parámetro no existe.'}, status=404)
    return JsonResponse({'ok': True, 'mensaje': mensaje_ok})


@require_POST
def exogena_parametro_inactivar(request, pk):
    """JSON: 'Eliminar' lógico (ESTADO = 'I')."""
    return _cambiar_estado(pk, ParametroExogena.ESTADO_INACTIVO, 'Parámetro inactivado correctamente.')


@require_POST
def exogena_parametro_reactivar(request, pk):
    """JSON: revierte el borrado lógico (ESTADO = 'A')."""
    return _cambiar_estado(pk, ParametroExogena.ESTADO_ACTIVO, 'Parámetro reactivado correctamente.')


# ---------------------------------------------------------------------- #
# SPEC-013 Fase 2: Tablero de Auditoría Exógena (V_EXOGENA_PRECALCULADA)
# ---------------------------------------------------------------------- #
FILAS_PREVISUALIZACION = 100


def _texto_decimal(valor):
    """Decimal -> str para JSON sin pérdida de precisión."""
    return format(valor, 'f')


@ensure_csrf_cookie
@require_GET
def auditoria_exogena_view(request):
    """Página principal del tablero. Entrega los pares (año, formato) para poblar los selectores."""
    try:
        opciones = services_exogena.obtener_opciones()
        error = ''
    except DatabaseError:
        logger.exception('SPEC-013: error consultando opciones de V_EXOGENA_PRECALCULADA')
        opciones = []
        error = MENSAJE_ERROR_BD

    contexto = {
        'datos_iniciales': {
            'opciones': opciones,
            'error': error,
            'por_pagina': FILAS_PREVISUALIZACION,
            'nit_cuantias_menores': services_exogena.NIT_CUANTIAS_MENORES,
            'url_datos': reverse('reportes:auditoria_exogena_datos_api'),
            'url_excel': reverse('reportes:descargar_excel_exogena_api'),
        },
    }
    return render(request, 'reportes/auditoria_exogena.html', contexto)


@require_GET
def auditoria_exogena_datos_api(request):
    """JSON: previsualización paginada (100 filas por página) con la regla de cuantías menores aplicada."""
    try:
        datos = services_exogena.obtener_datos_procesados(
            request.GET.get('ano'), request.GET.get('formato'))
    except services_exogena.ParametrosInvalidosError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except DatabaseError:
        logger.exception('SPEC-013: error consultando V_EXOGENA_PRECALCULADA')
        return JsonResponse({'ok': False, 'error': MENSAJE_ERROR_BD}, status=500)

    paginador = Paginator(datos['filas'], FILAS_PREVISUALIZACION)
    pagina = paginador.get_page(request.GET.get('page'))  # tolera page inválida/fuera de rango
    resumen = datos['resumen']

    return JsonResponse({
        'ok': True,
        'ano': datos['ano'],
        'formato': datos['formato'],
        'items': [
            {
                'concepto_dian': f['concepto_dian'],
                'nit': f['nit'],
                'razon_social': f['razon_social'],
                'tipo_doc_dian': f['tipo_doc_dian'],
                'columna_xml': f['columna_xml'],
                'monto': _texto_decimal(f['monto']),
                'es_cuantia_menor': f['es_cuantia_menor'],
            }
            for f in pagina.object_list
        ],
        'paginacion': {
            'pagina': pagina.number,
            'total_paginas': paginador.num_pages,
            'total_registros': paginador.count,
            'por_pagina': FILAS_PREVISUALIZACION,
            'tiene_anterior': pagina.has_previous(),
            'tiene_siguiente': pagina.has_next(),
        },
        'resumen': {
            'total_filas': resumen['total_filas'],
            'filas_cuantias_menores': resumen['filas_cuantias_menores'],
            'terceros_originales': resumen['terceros_originales'],
            'terceros_consolidados': resumen['terceros_consolidados'],
            'monto_total': _texto_decimal(resumen['monto_total']),
            'monto_cuantias_menores': _texto_decimal(resumen['monto_cuantias_menores']),
            'monto_terceros_detallados': _texto_decimal(resumen['monto_terceros_detallados']),
        },
    })


@require_GET
def descargar_excel_exogena_api(request):
    """Descarga el .xlsx del formato/año seleccionados. Los errores se responden en JSON (el cliente los muestra)."""
    try:
        datos = services_exogena.obtener_datos_procesados(
            request.GET.get('ano'), request.GET.get('formato'))
    except services_exogena.ParametrosInvalidosError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except DatabaseError:
        logger.exception('SPEC-013: error consultando V_EXOGENA_PRECALCULADA para exportar')
        return JsonResponse({'ok': False, 'error': MENSAJE_ERROR_BD}, status=500)

    if not datos['filas']:
        return JsonResponse(
            {'ok': False, 'error': 'No hay información para el año y formato seleccionados.'}, status=404)

    return services_exogena.generar_excel_response(datos)
