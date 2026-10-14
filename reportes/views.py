# -*- coding: utf-8 -*-
import json
import logging
import re
from decimal import Decimal
from django.shortcuts import render
from django.http import JsonResponse
from django.db import connection
from django.utils import timezone
from pos.models import PosTurno, PosTicketHeader
from .services_analisis_ventas import VentasClientesAnalyticsService

logger = logging.getLogger(__name__)


def format_cop(value) -> str:
    """Formatea valores numericos a Pesos Colombianos (COP)."""
    if value is None:
        dec_val = Decimal('0')
    elif isinstance(value, Decimal):
        dec_val = value
    else:
        try:
            dec_val = Decimal(str(value))
        except Exception:
            dec_val = Decimal('0')

    formatted = f"{int(round(dec_val)):,}"
    return "$ " + formatted.replace(",", ".")


def dashboard_view(request):
    """Vista principal del Dashboard del Vendedor / Supervisor."""
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

    query_kpis = """
    SELECT 
        NVL(SUM(d.TOT_DOCUMENTO), 0) AS TOT_VENTAS,
        COUNT(DISTINCT d.ID_DOCUMENTO) AS CANT_PEDIDOS,
        COUNT(DISTINCT d.ID_TERCERO) AS IMPACTOS
    FROM CO_DOCUMENTOS d
    WHERE d.ESTADO_DOC = 'GRABADO'
    """
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

            query_agrupada = """
            SELECT * FROM (
                SELECT 
                    NVL(p.NOM_TERCERO, 'PROVEEDOR GENERAL') AS PROVEEDOR,
                    NVL(f.NOM_FAMILIA, 'FAMILIA GENERAL') AS FAMILIA,
                    NVL(l.NOM_LINEA, 'LINEA GENERAL') AS LINEA,
                    SUM(m.CANTIDAD) AS CANTIDAD_TOTAL,
                    SUM(m.CANTIDAD * m.VLR_UNITARIO) AS TOT_MERCANCIA
                FROM IN_MOV_INVENTARIOS m
                LEFT JOIN IN_ARTICULOS a ON m.ID_ARTICULO = a.ID_ARTICULO
                LEFT JOIN CO_TERCEROS p ON a.ID_PROVEEDOR = p.ID_TERCERO
                LEFT JOIN IN_FAMILIAS f ON a.ID_FAMILIA = f.ID_FAMILIA
                LEFT JOIN IN_LINEAS l ON a.ID_LINEA = l.ID_LINEA
                GROUP BY 
                    NVL(p.NOM_TERCERO, 'PROVEEDOR GENERAL'),
                    NVL(f.NOM_FAMILIA, 'FAMILIA GENERAL'),
                    NVL(l.NOM_LINEA, 'LINEA GENERAL')
                ORDER BY TOT_MERCANCIA DESC
            ) WHERE ROWNUM <= 20
            """
            cursor.execute(query_agrupada)
            for r in cursor.fetchall():
                agrupacion_productos.append({
                    'proveedor': r[0],
                    'familia': r[1],
                    'linea': r[2],
                    'cantidad': float(r[3] or 0),
                    'tot_mercancia': int(round(Decimal(str(r[4] or 0)))),
                    'tot_mercancia_cop': format_cop(r[4])
                })

            query_chart = """
            SELECT 
                TO_CHAR(d.FCH_DOCUMENTO, 'DD/MM') AS DIA,
                SUM(d.TOT_DOCUMENTO) AS VENTA_DIA
            FROM CO_DOCUMENTOS d
            WHERE d.ESTADO_DOC = 'GRABADO'
              AND TO_CHAR(d.FCH_DOCUMENTO, 'YYYY-MM') = TO_CHAR(SYSDATE, 'YYYY-MM')
            GROUP BY TO_CHAR(d.FCH_DOCUMENTO, 'DD/MM')
            ORDER BY MIN(d.FCH_DOCUMENTO)
            """
            cursor.execute(query_chart)
            for r in cursor.fetchall():
                ventas_tendencia.append({
                    'dia': r[0],
                    'monto': float(r[1] or 0)
                })

    except Exception as e:
        logger.error(f"Error en Dashboard: {e}")
        tickets_local = PosTicketHeader.objects.all()
        if tickets_local.exists():
            tot_loc = sum((t.tot_ticket for t in tickets_local), Decimal('0'))
            cnt_loc = tickets_local.count()
            imp_loc = len(set(t.id_tercero for t in tickets_local))
            total_ventas += tot_loc
            cant_pedidos += cnt_loc
            impactos_clientes += imp_loc
            if cant_pedidos > 0:
                drop_size = total_ventas / Decimal(str(cant_pedidos))

    context = {
        'periodo': periodo,
        'total_ventas': total_ventas,
        'total_ventas_cop': format_cop(total_ventas),
        'cant_pedidos': cant_pedidos,
        'drop_size': drop_size,
        'drop_size_cop': format_cop(drop_size),
        'impactos_clientes': impactos_clientes,
        'agrupacion_productos': agrupacion_productos,
        'ventas_tendencia': ventas_tendencia,
    }
    return render(request, 'reportes/dashboard.html', context)


def cartera_view(request):
    """Informe de Cartera y Recaudos del Dia."""
    total_cartera = Decimal('0')
    cartera_corriente = Decimal('0')
    cartera_1_30 = Decimal('0')
    cartera_31_60 = Decimal('0')
    cartera_61_90 = Decimal('0')
    cartera_90_mas = Decimal('0')
    recaudos_dia = Decimal('0')
    clientes_cartera = []

    query_cartera_edades = """
    SELECT 
        NVL(SUM(vlr_saldo), 0) AS TOTAL_CARTERA,
        NVL(SUM(CASE WHEN fch_vencimiento >= TRUNC(SYSDATE) THEN vlr_saldo ELSE 0 END), 0) AS CORRIENTE,
        NVL(SUM(CASE WHEN TRUNC(SYSDATE) - fch_vencimiento BETWEEN 1 AND 30 THEN vlr_saldo ELSE 0 END), 0) AS DIAS_1_30,
        NVL(SUM(CASE WHEN TRUNC(SYSDATE) - fch_vencimiento BETWEEN 31 AND 60 THEN vlr_saldo ELSE 0 END), 0) AS DIAS_31_60,
        NVL(SUM(CASE WHEN TRUNC(SYSDATE) - fch_vencimiento BETWEEN 61 AND 90 THEN vlr_saldo ELSE 0 END), 0) AS DIAS_61_90,
        NVL(SUM(CASE WHEN TRUNC(SYSDATE) - fch_vencimiento > 90 THEN vlr_saldo ELSE 0 END), 0) AS DIAS_90_MAS
    FROM (
        SELECT 
            d.ID_TERCERO,
            d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0) AS VLR_SALDO,
            d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30) AS FCH_VENCIMIENTO
        FROM CO_DOCUMENTOS d
        JOIN CT_VENTAS v ON d.ID_DOCUMENTO = v.ID_DOCUMENTO
        WHERE d.ESTADO_DOC = 'GRABADO'
          AND (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) > 0
    )
    """

    query_recaudos = """
    SELECT NVL(SUM(VALOR), 0)
    FROM CO_DOCUMENTO_ITEMS
    WHERE CAMPO = 'CAJA'
      AND DEBE_HABER = 'D'
      AND TRUNC(FCH_DOCUMENTO) = TRUNC(SYSDATE)
    """

    query_top_cartera = """
    SELECT * FROM (
        SELECT 
            t.ID_TERCERO,
            t.NOM_TERCERO,
            SUM(d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) AS SALDO_TOTAL,
            SUM(CASE WHEN (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) >= TRUNC(SYSDATE) THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END) AS CORRIENTE,
            SUM(CASE WHEN TRUNC(SYSDATE) - (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) BETWEEN 1 AND 30 THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END) AS DIAS_1_30,
            SUM(CASE WHEN TRUNC(SYSDATE) - (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) BETWEEN 31 AND 60 THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END) AS DIAS_31_60,
            SUM(CASE WHEN TRUNC(SYSDATE) - (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) BETWEEN 61 AND 90 THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END) AS DIAS_61_90,
            SUM(CASE WHEN TRUNC(SYSDATE) - (d.FCH_DOCUMENTO + NVL(v.PLAZO_PAGO, 30)) > 90 THEN (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) ELSE 0 END) AS DIAS_90_MAS
        FROM CO_DOCUMENTOS d
        JOIN CT_VENTAS v ON d.ID_DOCUMENTO = v.ID_DOCUMENTO
        JOIN CO_TERCEROS t ON d.ID_TERCERO = t.ID_TERCERO
        WHERE d.ESTADO_DOC = 'GRABADO'
          AND (d.TOT_DOCUMENTO - NVL(v.TOT_RECAUDADO, 0)) > 0
        GROUP BY t.ID_TERCERO, t.NOM_TERCERO
        ORDER BY SALDO_TOTAL DESC
    ) WHERE ROWNUM <= 25
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_cartera_edades)
            row = cursor.fetchone()
            if row:
                total_cartera = Decimal(str(row[0] or 0))
                cartera_corriente = Decimal(str(row[1] or 0))
                cartera_1_30 = Decimal(str(row[2] or 0))
                cartera_31_60 = Decimal(str(row[3] or 0))
                cartera_61_90 = Decimal(str(row[4] or 0))
                cartera_90_mas = Decimal(str(row[5] or 0))

            cursor.execute(query_recaudos)
            row_rec = cursor.fetchone()
            if row_rec:
                recaudos_dia = Decimal(str(row_rec[0] or 0))

            cursor.execute(query_top_cartera)
            for r in cursor.fetchall():
                clientes_cartera.append({
                    'id_tercero': r[0],
                    'nom_tercero': r[1],
                    'saldo_total': format_cop(r[2]),
                    'corriente': format_cop(r[3]),
                    'dias_1_30': format_cop(r[4]),
                    'dias_31_60': format_cop(r[5]),
                    'dias_61_90': format_cop(r[6]),
                    'dias_90_mas': format_cop(r[7]),
                })
    except Exception as e:
        logger.error(f"Error en Cartera: {e}")

    context = {
        'total_cartera': total_cartera,
        'total_cartera_cop': format_cop(total_cartera),
        'cartera_corriente': format_cop(cartera_corriente),
        'cartera_1_30': format_cop(cartera_1_30),
        'cartera_31_60': format_cop(cartera_31_60),
        'cartera_61_90': format_cop(cartera_61_90),
        'cartera_90_mas': format_cop(cartera_90_mas),
        'recaudos_dia': recaudos_dia,
        'recaudos_dia_cop': format_cop(recaudos_dia),
        'clientes_cartera': clientes_cartera,
    }
    return render(request, 'reportes/cartera.html', context)


def cierre_caja_view(request):
    """Informe de Cierre Z / Arqueo de Caja POS."""
    turno_id = request.GET.get('turno_id')
    caja_id = request.GET.get('caja_id', 'CAJA-01')

    if turno_id:
        turno = PosTurno.objects.filter(pk=turno_id).first()
    else:
        turno = PosTurno.objects.filter(
            caja_id=caja_id).order_by('-id_turno').first()

    if not turno:
        context = {'turno_encontrado': False,
                   'message': 'No se encontraron turnos de caja registrados.'}
        return render(request, 'reportes/cierre_caja.html', context)

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
        except Exception:
            efectivo_declarado = None

    context = {
        'turno_encontrado': True,
        'turno': turno,
        'tickets_count': tickets.count(),
        'base_economica': base_economica,
        'base_economica_cop': format_cop(base_economica),
        'tot_efectivo': tot_efectivo,
        'tot_efectivo_cop': format_cop(tot_efectivo),
        'tot_tarjeta': tot_tarjeta,
        'tot_tarjeta_cop': format_cop(tot_tarjeta),
        'tot_transferencia': tot_transferencia,
        'tot_transferencia_cop': format_cop(tot_transferencia),
        'tot_puntos': tot_puntos,
        'tot_puntos_cop': format_cop(tot_puntos),
        'tot_ventas': tot_ventas,
        'tot_ventas_cop': format_cop(tot_ventas),
        'efectivo_esperado': efectivo_esperado,
        'efectivo_esperado_cop': format_cop(efectivo_esperado),
        'efectivo_declarado': efectivo_declarado,
        'efectivo_declarado_cop': format_cop(efectivo_declarado) if efectivo_declarado is not None else None,
        'descuadre': descuadre,
        'descuadre_cop': format_cop(descuadre),
        'es_cuadrado': (descuadre == Decimal('0')) if efectivo_declarado is not None else True,
        'todos_turnos': PosTurno.objects.order_by('-id_turno')[:10]
    }
    return render(request, 'reportes/cierre_caja.html', context)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(round(obj))
        return super(DecimalEncoder, self).default(obj)


def analisis_ventas_clientes_view(request):
    """Vista para el dashboard de analisis de ventas por clientes (SPEC-001)."""
    service_for_filters = VentasClientesAnalyticsService()
    all_filtros = service_for_filters.get_filtros_disponibles()

    is_ajax = request.headers.get(
        'x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    solo_matriz = request.GET.get('solo_matriz') == '1'

    if is_ajax:
        request_filtros = {
            'anio': request.GET.get('anio'),
            'mes': request.GET.get('mes'),
            'zona_vendedor': request.GET.get('zona') or request.GET.get('vendedor'),
            'proveedor': request.GET.get('proveedor'),
            'linea': request.GET.get('linea'),
            'canal': request.GET.get('canal'),
        }
        # Aplicación estricta de ADR-014 en parámetros para cruce de matriz
        request_filtros['departamento'] = request.GET.get('departamento')
        request_filtros['municipio'] = request.GET.get('municipio')
        request_filtros['barrio'] = request.GET.get('barrio')

        applied_filtros = {k: v for k,
                           v in request_filtros.items() if v and v != 'todos'}
    else:
        if all_filtros.get('anios'):
            applied_filtros = {'anio': all_filtros['anios'][0]}
        else:
            applied_filtros = {}

    service = VentasClientesAnalyticsService(applied_filtros)

    # --- ADR-013: RUTINA AISLADA DE PAGINACIÓN DE MATRIZ ---
    if solo_matriz:
        offset = int(request.GET.get('offset_matriz', 0))
        limit = int(request.GET.get('limit_matriz', 50))
        matriz_pivot = service.get_matriz_pivot_mensual(
            offset=offset, limit=limit)

        filas_html = ""
        total_filas = 0

        if not matriz_pivot.empty:
            matriz_formateada = matriz_pivot.applymap(
                lambda x: f"$ {int(x):,}".replace(",", ".") if isinstance(
                    x, (int, float)) and x > 0 else ("$ 0" if isinstance(x, (int, float)) else x)
            )
            html_temp = matriz_formateada.to_html(
                header=False, border=0, escape=False)
            match = re.search(r'<tbody>(.*?)</tbody>', html_temp, re.DOTALL)
            if match:
                filas_html = match.group(1)
                filas_html = filas_html.replace(
                    '<tr>', '<tr class="hover:bg-gray-800/60 transition-colors">')
                filas_html = filas_html.replace(
                    '<th>', '<td class="p-2.5 text-left font-bold text-gray-200">')
                filas_html = filas_html.replace(
                    '<td>', '<td class="p-2.5 text-right font-mono text-gray-300">')
            total_filas = len(matriz_pivot)

        return JsonResponse({
            'nuevas_filas_html': filas_html,
            'total_filas': total_filas,
            'has_more': total_filas == limit
        })

    # --- RUTINA DE CARGA INICIAL Y REDIBUJO COMPLETO DE FILTROS ---
    datos_tablero = service.get_datos_tablero()

    offset = int(request.GET.get('offset_matriz', 0))
    limit = int(request.GET.get('limit_matriz', 50))
    matriz_pivot = service.get_matriz_pivot_mensual(offset=offset, limit=limit)

    filas_html = ""
    has_more = False

    if not matriz_pivot.empty:
        matriz_formateada = matriz_pivot.applymap(
            lambda x: f"$ {int(x):,}".replace(",", ".") if isinstance(
                x, (int, float)) and x > 0 else ("$ 0" if isinstance(x, (int, float)) else x)
        )
        html_temp = matriz_formateada.to_html(
            header=False, border=0, escape=False)
        match = re.search(r'<tbody>(.*?)</tbody>', html_temp, re.DOTALL)
        if match:
            filas_html = match.group(1)
            filas_html = filas_html.replace(
                '<tr>', '<tr class="hover:bg-gray-800/60 transition-colors">')
            filas_html = filas_html.replace(
                '<th>', '<td class="p-2.5 text-left font-bold text-gray-200">')
            filas_html = filas_html.replace(
                '<td>', '<td class="p-2.5 text-right font-mono text-gray-300">')
        has_more = len(matriz_pivot) == limit

    payload = {
        'canal': datos_tablero['canal'],
        'treemap': datos_tablero['treemap'],
        'deptos': datos_tablero['deptos'],
        'municipios': datos_tablero['municipios'],
        'barrios': datos_tablero['barrios'],
        'rows_html': filas_html,
        'has_more': has_more
    }

    if is_ajax:
        return JsonResponse(payload, encoder=DecimalEncoder)

    datos_iniciales_json = json.dumps(payload, cls=DecimalEncoder)

    context = {
        'filtros': all_filtros,
        'datos_iniciales_json': datos_iniciales_json,
    }
    return render(request, 'reportes/analisis_ventas_clientes.html', context)


def api_top_clientes_zona(request):
    """Retorna el Top 10 de clientes filtrado por zona geográfica para SPEC-001."""
    tipo = request.GET.get('tipo', '').upper()
    valor = request.GET.get('valor', '').strip()

    filtros = {
        'anio': request.GET.get('anio'),
        'mes': request.GET.get('mes'),
        'zona_vendedor': request.GET.get('zona'),
        'proveedor': request.GET.get('proveedor'),
        'linea': request.GET.get('linea'),
        'canal': request.GET.get('canal'),
    }

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
        # ADR-012: Delegación absoluta al motor relacional de Oracle (sin loops en Python)
        service = VentasClientesAnalyticsService(filtros=filtros)
        top_clientes_raw, total_venta_zona = service.get_top_clientes(limit=10)

        top_clientes = []
        for c in top_clientes_raw:
            venta_val = c['venta']
            part = round((venta_val / total_venta_zona) * 100,
                         2) if total_venta_zona > 0 else 0
            top_clientes.append({
                'cliente': c['cliente'],
                'venta': venta_val,
                'participacion': part
            })

        # CRÍTICO: Se corrigió la llave para sincronizar con la espera del JS (`result.clientes`)
        return JsonResponse({'status': 'ok', 'clientes': top_clientes})
    except Exception as e:
        logger.error(f"Error en api_top_clientes_zona: {e}")
        return JsonResponse({'status': 'error', 'clientes': [], 'detalle': str(e)})
