# -*- coding: utf-8 -*-
import json
import logging
import re
import datetime
import pytz
from decimal import Decimal
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
import csv
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from django.views.decorators.http import require_POST
from django.db import connection
from django.db.models import Q
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder
from core.models import InBodega, CoTercero, PvProveedor, CoCentroCosto, InGravamen, CoDocumento, CoTipoDocumento
from pos.models import PosTurno, PosTicketHeader

from .services_analisis_ventas import VentasClientesAnalyticsService
from .services_analisis_productos import VentasProductosAnalyticsService
from .services_analisis_ejecutivo import AnalisisEjecutivoService
from .services_compras import ServicioComprasSugeridas, generar_orden_compra, anular_orden_compra
from .services_analisis_financiero import ServicioAnalisisFinanciero, estructurar_balance_comprobacion

logger = logging.getLogger(__name__)



def bandeja_ordenes_compra(request):
    """
    Muestra un histórico de las últimas 50 órdenes de compra (documento 135).
    """
    # 1. Extraemos los IDs correspondientes al año actual o histórico
    ids_135 = CoTipoDocumento.objects.filter(cod_tipo_documento='135').values_list('id_tipo_documento', flat=True)
    
    # 2. Armamos el QuerySet base optimizado con JOIN al tercero
    query_ordenes = CoDocumento.objects.select_related('id_tercero').filter(
        id_tipo_documento__in=ids_135
    ).order_by('-fch_documento', '-num_documento')
    
    # 3. FORZAMOS la evaluación ejecutando list() sobre el queryset completo, y luego aplicamos el slice de Python
    ordenes_lista = list(query_ordenes)[:50]
    
    context = {
        'ordenes': ordenes_lista
    }
    return render(request, 'reportes/bandeja_ordenes_compra.html', context)


def format_cop(value) -> str:
    if value is None:
        dec_val = Decimal('0')
    elif isinstance(value, Decimal):
        dec_val = value
    else:
        try:
            dec_val = Decimal(str(value))
        except:
            dec_val = Decimal('0')
    return "$ " + f"{int(round(dec_val)):,}".replace(",", ".")


def dashboard_view(request):
    periodo = request.GET.get('periodo', 'mes')
    now_dt = timezone.now()
    if timezone.is_aware(now_dt):
        now_dt = timezone.localtime(now_dt)

    total_ventas = Decimal('0')
    cant_pedidos = 0
    drop_size = Decimal('0')
    impactos_clientes = 0
    agrupacion_productos = []
    ventas_tendencia = []

    query_kpis = "SELECT NVL(SUM(d.TOT_DOCUMENTO), 0), COUNT(DISTINCT d.ID_DOCUMENTO), COUNT(DISTINCT d.ID_TERCERO) FROM CO_DOCUMENTOS d WHERE d.ESTADO_DOC = 'GRABADO'"
    params_kpis = []
    if periodo == 'dia':
        query_kpis += " AND TRUNC(d.FCH_DOCUMENTO) = TRUNC(SYSDATE)"
    elif periodo == 'mes':
        query_kpis += " AND TO_CHAR(d.FCH_DOCUMENTO, 'YYYY-MM') = TO_CHAR(SYSDATE, 'YYYY-MM')"
    elif periodo == 'ano':
        query_kpis += " AND TO_CHAR(d.FCH_DOCUMENTO, 'YYYY') = TO_CHAR(SYSDATE, 'YYYY')"

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_kpis, params_kpis)
            row = cursor.fetchone()
            if row:
                total_ventas = Decimal(str(row[0] or 0))
                cant_pedidos = int(row[1] or 0)
                impactos_clientes = int(row[2] or 0)
                if cant_pedidos > 0:
                    drop_size = total_ventas / Decimal(str(cant_pedidos))

            query_agrupada = """SELECT * FROM (SELECT NVL(p.NOM_TERCERO, 'PROVEEDOR GENERAL'), NVL(f.NOM_FAMILIA, 'FAMILIA GENERAL'), NVL(l.NOM_LINEA, 'LINEA GENERAL'), SUM(m.CANTIDAD), SUM(m.CANTIDAD * m.VLR_UNITARIO) AS TOT FROM IN_MOV_INVENTARIOS m LEFT JOIN IN_ARTICULOS a ON m.ID_ARTICULO = a.ID_ARTICULO LEFT JOIN CO_TERCEROS p ON a.ID_PROVEEDOR = p.ID_TERCERO LEFT JOIN IN_FAMILIAS f ON a.ID_FAMILIA = f.ID_FAMILIA LEFT JOIN IN_LINEAS l ON a.ID_LINEA = l.ID_LINEA GROUP BY NVL(p.NOM_TERCERO, 'PROVEEDOR GENERAL'), NVL(f.NOM_FAMILIA, 'FAMILIA GENERAL'), NVL(l.NOM_LINEA, 'LINEA GENERAL') ORDER BY TOT DESC) WHERE ROWNUM <= 20"""
            cursor.execute(query_agrupada)
            for r in cursor.fetchall():
                agrupacion_productos.append({'proveedor': r[0], 'familia': r[1], 'linea': r[2], 'cantidad': float(
                    r[3] or 0), 'tot_mercancia': int(round(Decimal(str(r[4] or 0)))), 'tot_mercancia_cop': format_cop(r[4])})

            query_chart = "SELECT TO_CHAR(d.FCH_DOCUMENTO, 'DD/MM'), SUM(d.TOT_DOCUMENTO) FROM CO_DOCUMENTOS d WHERE d.ESTADO_DOC = 'GRABADO' AND TO_CHAR(d.FCH_DOCUMENTO, 'YYYY-MM') = TO_CHAR(SYSDATE, 'YYYY-MM') GROUP BY TO_CHAR(d.FCH_DOCUMENTO, 'DD/MM') ORDER BY MIN(d.FCH_DOCUMENTO)"
            cursor.execute(query_chart)
            for r in cursor.fetchall():
                ventas_tendencia.append(
                    {'dia': r[0], 'monto': float(r[1] or 0)})
    except Exception as e:
        logger.error(f"Error en Dashboard: {e}")

    context = {'periodo': periodo, 'total_ventas': total_ventas, 'total_ventas_cop': format_cop(total_ventas), 'cant_pedidos': cant_pedidos, 'drop_size': drop_size, 'drop_size_cop': format_cop(
        drop_size), 'impactos_clientes': impactos_clientes, 'agrupacion_productos': agrupacion_productos, 'ventas_tendencia': ventas_tendencia}
    return render(request, 'reportes/dashboard.html', context)


def cartera_view(request):
    total_cartera = Decimal('0')
    cartera_corriente = Decimal('0')
    cartera_1_30 = Decimal('0')
    cartera_31_60 = Decimal('0')
    cartera_61_90 = Decimal('0')
    cartera_90_mas = Decimal('0')
    recaudos_dia = Decimal('0')
    clientes_cartera = []

    query_cartera_edades = "SELECT NVL(SUM(vlr_saldo), 0), NVL(SUM(CASE WHEN fch_vencimiento >= TRUNC(SYSDATE) THEN vlr_saldo ELSE 0 END), 0), NVL(SUM(CASE WHEN TRUNC(SYSDATE) - fch_vencimiento BETWEEN 1 AND 30 THEN vlr_saldo ELSE 0 END), 0), NVL(SUM(CASE WHEN TRUNC(SYSDATE) - fch_vencimiento BETWEEN 31 AND 60 THEN vlr_saldo ELSE 0 END), 0), NVL(SUM(CASE WHEN TRUNC(SYSDATE) - fch_vencimiento BETWEEN 61 AND 90 THEN vlr_saldo ELSE 0 END), 0), NVL(SUM(CASE WHEN TRUNC(SYSDATE) - fch_vencimiento > 90 THEN vlr_saldo ELSE 0 END), 0) FROM (SELECT d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0) AS VLR_SALDO, d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30) AS FCH_VENCIMIENTO FROM CO_DOCUMENTOS d JOIN CT_VENTAS v ON d.ID_DOCUMENTO = v.ID_DOCUMENTO WHERE d.ESTADO_DOC = 'GRABADO' AND (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) > 0)"
    query_recaudos = "SELECT NVL(SUM(VALOR), 0) FROM CO_DOCUMENTO_ITEMS WHERE CAMPO = 'CAJA' AND DEBE_HABER = 'D' AND TRUNC(FCH_DOCUMENTO) = TRUNC(SYSDATE)"
    query_top_cartera = "SELECT * FROM (SELECT t.ID_TERCERO, t.NOM_TERCERO, SUM(d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) AS SALDO_TOTAL, SUM(CASE WHEN (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) >= TRUNC(SYSDATE) THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END), SUM(CASE WHEN TRUNC(SYSDATE) - (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) BETWEEN 1 AND 30 THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END), SUM(CASE WHEN TRUNC(SYSDATE) - (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) BETWEEN 31 AND 60 THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END), SUM(CASE WHEN TRUNC(SYSDATE) - (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) BETWEEN 61 AND 90 THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END), SUM(CASE WHEN TRUNC(SYSDATE) - (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) > 90 THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END) FROM CO_DOCUMENTOS d JOIN CT_VENTAS v ON d.ID_DOCUMENTO = v.ID_DOCUMENTO JOIN CO_TERCEROS t ON d.ID_TERCERO = t.ID_TERCERO WHERE d.ESTADO_DOC = 'GRABADO' AND (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) > 0 GROUP BY t.ID_TERCERO, t.NOM_TERCERO ORDER BY SALDO_TOTAL DESC) WHERE ROWNUM <= 25"

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_cartera_edades)
            row = cursor.fetchone()
            if row:
                total_cartera, cartera_corriente, cartera_1_30, cartera_31_60, cartera_61_90, cartera_90_mas = (
                    Decimal(str(x or 0)) for x in row)
            cursor.execute(query_recaudos)
            row_rec = cursor.fetchone()
            if row_rec:
                recaudos_dia = Decimal(str(row_rec[0] or 0))
            cursor.execute(query_top_cartera)
            for r in cursor.fetchall():
                clientes_cartera.append({'id_tercero': r[0], 'nom_tercero': r[1], 'saldo_total': format_cop(r[2]), 'corriente': format_cop(
                    r[3]), 'dias_1_30': format_cop(r[4]), 'dias_31_60': format_cop(r[5]), 'dias_61_90': format_cop(r[6]), 'dias_90_mas': format_cop(r[7])})
    except Exception as e:
        logger.error(f"Error en Cartera: {e}")

    context = {'total_cartera': total_cartera, 'total_cartera_cop': format_cop(total_cartera), 'cartera_corriente': format_cop(cartera_corriente), 'cartera_1_30': format_cop(cartera_1_30), 'cartera_31_60': format_cop(
        cartera_31_60), 'cartera_61_90': format_cop(cartera_61_90), 'cartera_90_mas': format_cop(cartera_90_mas), 'recaudos_dia': recaudos_dia, 'recaudos_dia_cop': format_cop(recaudos_dia), 'clientes_cartera': clientes_cartera}
    return render(request, 'reportes/cartera.html', context)


def cierre_caja_view(request):
    turno_id = request.GET.get('turno_id')
    caja_id = request.GET.get('caja_id', 'CAJA-01')
    turno = PosTurno.objects.filter(pk=turno_id).first(
    ) if turno_id else PosTurno.objects.filter(caja_id=caja_id).order_by('-id_turno').first()
    if not turno:
        return render(request, 'reportes/cierre_caja.html', {'turno_encontrado': False, 'message': 'No turnos'})

    tickets = turno.tickets.all()
    base_economica = turno.base_economica
    tot_efectivo = sum((t.pago_efectivo for t in tickets), Decimal('0'))
    tot_tarjeta = sum((t.pago_tarjeta for t in tickets), Decimal('0'))
    tot_transferencia = sum(
        (t.pago_transferencia for t in tickets), Decimal('0'))
    tot_puntos = sum((t.pago_puntos for t in tickets), Decimal('0'))
    tot_ventas = sum((t.tot_ticket for t in tickets), Decimal('0'))
    efectivo_esperado = base_economica + tot_efectivo

    raw_declarado = request.GET.get('efectivo_declarado')
    efectivo_declarado = None
    descuadre = Decimal('0')
    if raw_declarado is not None:
        try:
            efectivo_declarado = Decimal(str(raw_declarado))
            descuadre = efectivo_declarado - efectivo_esperado
        except:
            pass

    context = {'turno_encontrado': True, 'turno': turno, 'tickets_count': tickets.count(), 'base_economica': base_economica, 'base_economica_cop': format_cop(base_economica), 'tot_efectivo': tot_efectivo, 'tot_efectivo_cop': format_cop(tot_efectivo), 'tot_tarjeta': tot_tarjeta, 'tot_tarjeta_cop': format_cop(tot_tarjeta), 'tot_transferencia': tot_transferencia, 'tot_transferencia_cop': format_cop(tot_transferencia), 'tot_puntos': tot_puntos, 'tot_puntos_cop': format_cop(tot_puntos), 'tot_ventas': tot_ventas,
               'tot_ventas_cop': format_cop(tot_ventas), 'efectivo_esperado': efectivo_esperado, 'efectivo_esperado_cop': format_cop(efectivo_esperado), 'efectivo_declarado': efectivo_declarado, 'efectivo_declarado_cop': format_cop(efectivo_declarado) if efectivo_declarado is not None else None, 'descuadre': descuadre, 'descuadre_cop': format_cop(descuadre), 'es_cuadrado': (descuadre == Decimal('0')) if efectivo_declarado is not None else True, 'todos_turnos': PosTurno.objects.order_by('-id_turno')[:10]}
    return render(request, 'reportes/cierre_caja.html', context)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(round(obj))
        return super(DecimalEncoder, self).default(obj)

# --- SPEC-001 ---


def analisis_ventas_clientes_view(request):
    service_for_filters = VentasClientesAnalyticsService()
    all_filtros = service_for_filters.get_filtros_disponibles()
    is_ajax = request.headers.get(
        'x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    solo_matriz = request.GET.get('solo_matriz') == '1'

    if is_ajax:
        request_filtros = {'anio': request.GET.get('anio'), 'mes': request.GET.get('mes'), 'zona_vendedor': request.GET.get('zona') or request.GET.get('vendedor'), 'proveedor': request.GET.get(
            'proveedor'), 'linea': request.GET.get('linea'), 'canal': request.GET.get('canal'), 'departamento': request.GET.get('departamento'), 'municipio': request.GET.get('municipio'), 'barrio': request.GET.get('barrio')}
        applied_filtros = {k: v for k,
                           v in request_filtros.items() if v and v != 'todos'}
    else:
        applied_filtros = {'anio': all_filtros['anios'][0]} if all_filtros.get(
            'anios') else {}

    service = VentasClientesAnalyticsService(applied_filtros)

    if solo_matriz:
        offset = int(request.GET.get('offset_matriz', 0))
        limit = int(request.GET.get('limit_matriz', 50))
        matriz_pivot = service.get_matriz_pivot_mensual(
            offset=offset, limit=limit)
        filas_html = ""
        total_filas = 0
        if not matriz_pivot.empty:
            matriz_formateada = matriz_pivot.applymap(lambda x: f"$ {int(x):,}".replace(",", ".") if isinstance(
                x, (int, float)) and x > 0 else ("$ 0" if isinstance(x, (int, float)) else x))
            html_temp = matriz_formateada.to_html(
                header=False, border=0, escape=False)
            match = re.search(r'<tbody>(.*?)</tbody>', html_temp, re.DOTALL)
            if match:
                filas_html = match.group(1).replace('<tr>', '<tr class="hover:bg-gray-700 transition-colors">').replace(
                    '<th>', '<td class="py-1 px-2 text-left font-bold text-gray-200">').replace('<td>', '<td class="py-1 px-2 text-right font-mono text-gray-300">')
            total_filas = len(matriz_pivot)
        return JsonResponse({'nuevas_filas_html': filas_html, 'total_filas': total_filas, 'has_more': total_filas == limit})

    datos_tablero = service.get_datos_tablero()
    offset = int(request.GET.get('offset_matriz', 0))
    limit = int(request.GET.get('limit_matriz', 50))
    matriz_pivot = service.get_matriz_pivot_mensual(offset=offset, limit=limit)
    filas_html = ""
    has_more = False

    if not matriz_pivot.empty:
        matriz_formateada = matriz_pivot.applymap(lambda x: f"$ {int(x):,}".replace(",", ".") if isinstance(
            x, (int, float)) and x > 0 else ("$ 0" if isinstance(x, (int, float)) else x))
        html_temp = matriz_formateada.to_html(
            header=False, border=0, escape=False)
        match = re.search(r'<tbody>(.*?)</tbody>', html_temp, re.DOTALL)
        if match:
            filas_html = match.group(1).replace('<tr>', '<tr class="hover:bg-gray-700 transition-colors">').replace(
                '<th>', '<td class="py-1 px-2 text-left font-bold text-gray-200">').replace('<td>', '<td class="py-1 px-2 text-right font-mono text-gray-300">')
        has_more = len(matriz_pivot) == limit

    payload = {'canal': datos_tablero['canal'], 'treemap': datos_tablero['treemap'], 'deptos': datos_tablero['deptos'],
               'municipios': datos_tablero['municipios'], 'barrios': datos_tablero['barrios'], 'rows_html': filas_html, 'has_more': has_more}
    if is_ajax:
        return JsonResponse(payload, encoder=DecimalEncoder)
    return render(request, 'reportes/analisis_ventas_clientes.html', {'filtros': all_filtros, 'datos_iniciales_json': json.dumps(payload, cls=DecimalEncoder)})


def api_top_clientes_zona(request):
    tipo = request.GET.get('tipo', '').upper()
    valor = request.GET.get('valor', '').strip()
    filtros = {'anio': request.GET.get('anio'), 'mes': request.GET.get('mes'), 'zona_vendedor': request.GET.get(
        'zona'), 'proveedor': request.GET.get('proveedor'), 'linea': request.GET.get('linea'), 'canal': request.GET.get('canal')}
    if tipo == 'DEPARTAMENTO' and valor:
        filtros['departamento'] = valor
    elif tipo == 'MUNICIPIO' and valor:
        filtros['municipio'] = valor
    elif tipo == 'BARRIO' and valor:
        filtros['barrio'] = valor
    elif not tipo:
        filtros['departamento'] = request.GET.get('departamento')
        filtros['municipio'] = request.GET.get('municipio')
        filtros['barrio'] = request.GET.get('barrio')
    filtros = {k: v for k, v in filtros.items() if v and v != 'todos'}

    try:
        service = VentasClientesAnalyticsService(filtros=filtros)
        top_clientes_raw, total_venta_zona = service.get_top_clientes(limit=20)
        top_clientes = [{'cliente': c['cliente'], 'venta': c['venta'], 'participacion': round(
            (c['venta'] / total_venta_zona) * 100, 2) if total_venta_zona > 0 else 0} for c in top_clientes_raw]
        return JsonResponse({'status': 'ok', 'clientes': top_clientes})
    except Exception as e:
        return JsonResponse({'status': 'error', 'clientes': [], 'detalle': str(e)})

# --- SPEC-002 ---


def analisis_ventas_productos_view(request):
    service_for_filters = VentasProductosAnalyticsService()
    all_filtros = service_for_filters.get_filtros_disponibles()
    is_ajax = request.headers.get(
        'x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    solo_resumen = request.GET.get('solo_resumen') == '1'

    if is_ajax:
        request_filtros = {'anio': request.GET.get('anio'), 'mes': request.GET.get('mes'), 'zona_vendedor': request.GET.get('zona') or request.GET.get(
            'vendedor'), 'proveedor': request.GET.get('proveedor'), 'linea': request.GET.get('linea'), 'canal': request.GET.get('canal')}
        applied_filtros = {k: v for k,
                           v in request_filtros.items() if v and v != 'todos'}
    else:
        applied_filtros = {'anio': all_filtros['anios'][0]} if all_filtros.get(
            'anios') else {}

    service = VentasProductosAnalyticsService(applied_filtros)

    # Manejo de Paginación para Tabla Resumen
    if solo_resumen:
        offset = int(request.GET.get('offset_resumen', 0))
        limit = int(request.GET.get('limit_resumen', 100))
        articulos = service.get_resumen_articulos_paginado(
            offset=offset, limit=limit)
        filas_html = ""
        for item in articulos:
            filas_html += f"""<tr class="hover:bg-gray-700 transition-colors">
                <td class="py-1 px-2 text-left font-medium text-gray-200 truncate max-w-[200px]" title="{item['articulo']}">{item['articulo']}</td>
                <td class="py-1 px-2 text-right font-mono font-bold text-gray-100">{format_cop(item['venta'])}</td>
                <td class="py-1 px-2 text-right font-mono font-semibold text-blue-400">{item['rentabilidad']} %</td>
                <td class="py-1 px-2 text-right font-mono text-gray-300">{int(item['kilos']):,} Kg</td>
                <td class="py-1 px-2 text-right font-mono text-gray-400">{item['q_clientes']}</td>
                <td class="py-1 px-2 text-right font-mono text-gray-400">{item['q_facturas']}</td>
            </tr>"""
        return JsonResponse({'nuevas_filas_html': filas_html, 'total_filas': len(articulos), 'has_more': len(articulos) == limit})

    datos_tablero = service.get_datos_tablero()
    articulos_iniciales = service.get_resumen_articulos_paginado(
        offset=0, limit=100)
    filas_resumen_html = ""

    if articulos_iniciales:
        for item in articulos_iniciales:
            filas_resumen_html += f"""<tr class="hover:bg-gray-700 transition-colors">
                <td class="py-1 px-2 text-left font-medium text-gray-200 truncate max-w-[200px]" title="{item['articulo']}">{item['articulo']}</td>
                <td class="py-1 px-2 text-right font-mono font-bold text-gray-100">{format_cop(item['venta'])}</td>
                <td class="py-1 px-2 text-right font-mono font-semibold text-blue-400">{item['rentabilidad']} %</td>
                <td class="py-1 px-2 text-right font-mono text-gray-300">{str(int(item['kilos'])).replace(',', '.')} Kg</td>
                <td class="py-1 px-2 text-right font-mono text-gray-400">{item['q_clientes']}</td>
                <td class="py-1 px-2 text-right font-mono text-gray-400">{item['q_facturas']}</td>
            </tr>"""
    else:
        filas_resumen_html = '<tr><td colspan="6" class="py-4 text-center text-gray-500 font-sans">No hay datos para los filtros seleccionados.</td></tr>'

    payload = {
        'comparativo_proveedor': datos_tablero['comparativo_proveedor'],
        'cubrimiento_zona': datos_tablero['cubrimiento_zona'],
        'total_productos_activos': datos_tablero['total_productos_activos'],
        'ventas_grupo': datos_tablero['ventas_grupo'],
        'ventas_linea': datos_tablero['ventas_linea'],
        'kilos_mes': datos_tablero['kilos_mes'],
        'filas_resumen_html': filas_resumen_html,
        'has_more': len(articulos_iniciales) == 100
    }

    if is_ajax:
        return JsonResponse(payload, encoder=DecimalEncoder)
    return render(request, 'reportes/analisis_ventas_productos.html', {'filtros': all_filtros, 'datos_iniciales_json': json.dumps(payload, cls=DecimalEncoder)})


def api_top_productos_dinamico(request):
    dimension = request.GET.get('dimension', 'PROVEEDOR').upper()
    valor = request.GET.get('valor', '').strip()
    filtros = {'anio': request.GET.get('anio'), 'mes': request.GET.get('mes'), 'zona_vendedor': request.GET.get(
        'zona'), 'proveedor': request.GET.get('proveedor'), 'linea': request.GET.get('linea'), 'canal': request.GET.get('canal')}
    filtros = {k: v for k, v in filtros.items() if v and v != 'todos'}
    try:
        service = VentasProductosAnalyticsService(filtros=filtros)
        top_productos = service.get_top_productos_dinamico(
            dimension=dimension, valor=valor, limit=20)
        return JsonResponse({'status': 'ok', 'productos': top_productos})
    except Exception as e:
        return JsonResponse({'status': 'error', 'productos': [], 'detalle': str(e)})


# --- SPEC-003: Tablero Ejecutivo de Análisis Ventas Global y Rentabilidad ---


def analisis_ventas_global_view(request):
    """
    Controlador para el Tablero Ejecutivo de Ventas Global y Rentabilidad (SPEC-003).
    Cumple con ADR-015 inyectando todo el dataset preprocesado en JSON mediante DjangoJSONEncoder.
    """
    filtros_disponibles = AnalisisEjecutivoService.get_filtros_disponibles()

    # 1. Extracción de Año
    anio_actual = request.GET.get('anio')
    if not anio_actual or not str(anio_actual).strip() or str(anio_actual).strip().upper() == 'TODOS':
        if filtros_disponibles.get('anios'):
            anio_actual = str(filtros_disponibles['anios'][0])
        else:
            anio_actual = str(datetime.date.today().year)
    else:
        anio_actual = str(anio_actual).strip()

    # 2. Extracción de Filtros Dinámicos (zona, proveedor, linea)
    filtros = {}
    zona_val = request.GET.get('zona') or request.GET.get('vendedor')
    if zona_val and str(zona_val).strip() and str(zona_val).strip().upper() != 'TODOS':
        filtros['zona'] = str(zona_val).strip()

    proveedor_val = request.GET.get('proveedor')
    if proveedor_val and str(proveedor_val).strip() and str(proveedor_val).strip().upper() != 'TODOS':
        filtros['proveedor'] = str(proveedor_val).strip()

    linea_val = request.GET.get('linea')
    if linea_val and str(linea_val).strip() and str(linea_val).strip().upper() != 'TODOS':
        filtros['linea'] = str(linea_val).strip()

    # 3. Llamadas a la Capa de Servicio
    kpis = AnalisisEjecutivoService.get_kpis_comparativos(anio_actual, filtros)
    curvas = AnalisisEjecutivoService.get_curvas_mensuales(anio_actual, filtros)
    historico = AnalisisEjecutivoService.get_historico_12_anios(anio_actual, filtros)

    # 4. Construcción del Diccionario Maestro (Payload)
    payload = {
        'anio_actual': int(anio_actual),
        'anio_previo': int(anio_actual) - 1,
        'kpis': kpis,
        'curvas': curvas,
        'historico': historico,
        'filtros_aplicados': {
            'anio': anio_actual,
            'zona': filtros.get('zona', ''),
            'proveedor': filtros.get('proveedor', ''),
            'linea': filtros.get('linea', ''),
        }
    }

    # Soporte para actualización asíncrona vía AJAX
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    if is_ajax:
        return JsonResponse(payload, encoder=DjangoJSONEncoder)

    # Serialización JSON para SSR (ADR-015)
    datos_json = json.dumps(payload, cls=DjangoJSONEncoder)

    context = {
        'filtros': filtros_disponibles,
        'filtros_aplicados': payload['filtros_aplicados'],
        'anio_actual': anio_actual,
        'contexto_json': datos_json,
        'datos_json': datos_json,
        'datos_iniciales_json': datos_json,
    }
    return render(request, 'reportes/analisis_ventas_global.html', context)


# --- SPEC-007: Tablero de Compras Inteligentes / Compras Sugeridas ---


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
    for item in datos_tablero:
        item.setdefault('id_gravamen', item.get('ID_GRAVAMEN') or '1')
        item.setdefault('ID_GRAVAMEN', item.get('id_gravamen') or '1')

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

    gravamenes_dict = {str(g.id_gravamen).strip(): float(g.porc_gravamen) for g in InGravamen.objects.all()}
    gravamenes_json = json.dumps(gravamenes_dict)

    contexto = {
        'bodegas': bodegas,
        'centros_costo': centros_costo,
        'proveedores': proveedores,
        'gravamenes_json': gravamenes_json,
    }
    return render(request, 'reportes/crear_orden_compra.html', contexto)


@require_POST
def api_anular_orden_compra(request):
    try:
        # 1. Parsear el body del request como JSON (ya que fetch envía application/json)
        data = json.loads(request.body)

        id_documento = data.get('id_documento')
        motivo = data.get('motivo')
        id_usuario = request.user.id if request.user.is_authenticated else '1'

        # 2. Validar datos requeridos
        if not id_documento or not motivo:
            return JsonResponse({
                'success': False, 
                'error': 'El ID del documento y el motivo son obligatorios.'
            }, status=400)

        # 3. Llamar al servicio transaccional
        resultado = anular_orden_compra(id_documento, motivo, id_usuario)

        # 4. Retornar respuesta
        if resultado.get('success'):
            return JsonResponse(resultado)
        else:
            return JsonResponse(resultado, status=400)

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False, 
            'error': 'Formato JSON inválido en la petición.'
        }, status=400)
    except Exception as e:
        import traceback
        logger.exception(f"Error interno en api_anular_orden_compra: {e}")
        # Retornar siempre JSON para que Alpine.js pueda leer el mensaje de error
        return JsonResponse({
            'success': False, 
            'error': f"Error interno: {str(e)}"
        }, status=500)


# --- SPEC-010: Tablero Gerencial ---


def tablero_gerencial_view(request):
    hoy = datetime.date.today()
    
    # 1. Leer parámetros GET o forzar los valores por defecto (Zero-Click Value)
    anio = request.GET.get('anio', str(hoy.year))
    periodo = request.GET.get('periodo', f"{hoy.month:02d}")
    id_centro_costo = request.GET.get('id_centro_costo', '') # Vacío = Todos

    # 2. Empaquetar filtros activos
    filtros_activos = {
        'anio': anio,
        'periodo': periodo,
        'id_centro_costo': id_centro_costo
    }

    # 3. Inicializar el motor financiero
    servicio = ServicioAnalisisFinanciero(filtros_activos)
    datos = servicio.get_datos_tablero()
    
    # 4. Cargar las listas desplegables pasando el año seleccionado
    filtros_disponibles = ServicioAnalisisFinanciero.get_filtros_disponibles(anio_actual=anio)
    datos_iniciales_json = json.dumps(datos, cls=DjangoJSONEncoder)

    return render(request, 'reportes/tablero_gerencial.html', {
        'datos': datos,
        'datos_iniciales_json': datos_iniciales_json,
        'filtros_disponibles': filtros_disponibles,
        'filtros_activos': filtros_activos, # Pasamos esto limpio al HTML
        'periodo_actual': servicio.periodo,
    })


# --- SPEC-012: Balance de Comprobación ---


def vista_balance_comprobacion(request):
    hoy = datetime.date.today()

    # 1. Zero-Click Value
    anio_filtro = int(request.GET.get('anio') or hoy.year)
    mes_filtro = int(request.GET.get('mes') or hoy.month)
    id_centro_costo = request.GET.get('id_centro_costo', '')
    periodo_corte = f"{anio_filtro}{mes_filtro:02d}"

    # 2. Motor SQL O(1) con consolidación por NIT_CONSOLIDADO
    sql = """
        SELECT 
            COD_CUENTA AS codigo,
            NIT_CONSOLIDADO AS nit,
            MAX(NOM_TERCERO_SUCURSAL) AS nom_tercero, 
            SUM(SALDO_INICIAL) AS saldo_inicial,
            SUM(DEBITOS_MES) AS debitos,
            SUM(CREDITOS_MES) AS creditos,
            SUM(SALDO_FINAL) AS saldo_final
        FROM V_BI_CONTABILIDAD_DASHBOARD
        WHERE PERIODO = %(periodo)s
    """
    
    params = {'periodo': periodo_corte}

    if id_centro_costo:
        sql += " AND ID_CENTRO_COSTO = %(id_centro_costo)s"
        params['id_centro_costo'] = id_centro_costo

    sql += """
        GROUP BY 
            COD_CUENTA, 
            NIT_CONSOLIDADO
        ORDER BY COD_CUENTA, MAX(NOM_TERCERO_SUCURSAL)
    """

    # 3. Ejecutar y mapear al array
    saldos_crudos = []
    mapa_cuentas = {}
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT TRIM(COD_CUENTA), MAX(TRIM(UPPER(NOM_CUENTA)))
            FROM CO_CUENTAS
            WHERE ID_SISTEMA = '1' AND LENGTH(TRIM(COD_CUENTA)) IN (1, 2, 4, 6, 8)
            GROUP BY TRIM(COD_CUENTA)
        """)
        mapa_cuentas = {str(r[0]).strip(): str(r[1]).strip() for r in cursor.fetchall() if r[0]}

        cursor.execute(sql, params)
        columnas = [col[0].lower() for col in cursor.description]
        
        for row in cursor.fetchall():
            fila = dict(zip(columnas, row))
            
            # La tabla ya nos entrega todo listo
            ini = Decimal(str(fila.get('saldo_inicial') or 0))
            deb = Decimal(str(fila.get('debitos') or 0))
            cred = Decimal(str(fila.get('creditos') or 0))
            fin = Decimal(str(fila.get('saldo_final') or 0))
            
            saldos_crudos.append({
                'codigo': fila.get('codigo'),
                'nit': fila.get('nit'),
                'nom_tercero': fila.get('nom_tercero'),
                'saldo_inicial': ini,
                'debitos': deb,
                'creditos': cred,
                'saldo_final': fin,
            })

    # 4. Inyección a Alpine.js
    cuentas_balance = estructurar_balance_comprobacion(saldos_crudos, mapa_cuentas=mapa_cuentas)
    filtros_disponibles = ServicioAnalisisFinanciero.get_filtros_disponibles(anio_actual=str(anio_filtro))
    centros_costo = [
        {
            'id_centro_costo': c['id'],
            'id': c['id'],
            'nombre': c['nombre'],
        }
        for c in filtros_disponibles.get('centros_costo', [])
    ]

    context = {
        'balance_data': cuentas_balance,
        'centros_costo': centros_costo,
        'mes_default': mes_filtro,
        'anio_default': anio_filtro,
        'periodo_actual': periodo_corte,
    }

    return render(request, 'reportes/balance_comprobacion.html', context)


balance_comprobacion_view = vista_balance_comprobacion


def exportar_balance_terceros(request):
    periodo = request.GET.get('periodo', '202501')
    formato = request.GET.get('formato', 'excel')

    query = """
        SELECT 
            CTA_NIVEL_4, 
            COD_CUENTA, 
            NOM_CUENTA, 
            NIT_CONSOLIDADO AS NIT,
            -- Tomamos un nombre representativo entre las múltiples sucursales
            MAX(NOM_TERCERO_SUCURSAL) AS NOM_TERCERO, 
            SUM(SALDO_INICIAL) AS SALDO_INICIAL, 
            SUM(DEBITOS_MES) AS DEBITOS_MES, 
            SUM(CREDITOS_MES) AS CREDITOS_MES, 
            SUM(SALDO_FINAL) AS SALDO_FINAL
        FROM V_BI_CONTABILIDAD_DASHBOARD
        WHERE PERIODO = %s
        GROUP BY 
            CTA_NIVEL_4, 
            COD_CUENTA, 
            NOM_CUENTA, 
            NIT_CONSOLIDADO
        ORDER BY COD_CUENTA, MAX(NOM_TERCERO_SUCURSAL)
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [periodo])
        columnas = [col[0] for col in cursor.description]
        registros = cursor.fetchall()

    if formato == 'sheets':
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="Balance_Terceros_{periodo}.csv"'
        
        writer = csv.writer(response, delimiter=';')
        writer.writerow(columnas)
        for fila in registros:
            writer.writerow(fila)
        return response

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Balance_Terceros_{periodo}.xlsx"'

    wb = Workbook()
    ws = wb.active
    ws.title = f"Balance {periodo}"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")

    ws.append(columnas)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill

    for fila in registros:
        ws.append(fila)

    ws.freeze_panes = 'A2'
    wb.save(response)
    return response


# --- SPEC-011: Módulo de Conciliación Electrónica DIAN vs ERP ---
from .views_dian import (
    gestor_lotes_dian,
    upload_excel_dian,
    api_dian_get_lotes,
    api_dian_get_detalle,
    api_dian_accion_lote,
)


