import logging
from decimal import Decimal
from django.shortcuts import render
from django.db import connection
from django.utils import timezone
from pos.models import PosTurno, PosTicketHeader

logger = logging.getLogger(__name__)

def format_cop(value) -> str:
    """Formatea valores numéricos a Pesos Colombianos (COP) utilizando decimal.Decimal."""
    if value is None:
        dec_val = Decimal('0.00')
    elif isinstance(value, Decimal):
        dec_val = value
    else:
        try:
            dec_val = Decimal(str(value))
        except Exception:
            dec_val = Decimal('0.00')
            
    formatted = f"{dec_val:,.2f}"
    return "$ " + formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def dashboard_view(request):
    """
    Vista principal del Dashboard del Vendedor / Supervisor:
    KPIs: Total Ventas, Drop Size, Impactos (Clientes con compra).
    Tabla Resumen: Ventas agrupadas por Proveedor, Familia y Línea.
    """
    periodo = request.GET.get('periodo', 'mes') # 'dia', 'mes', 'ano'
    now_dt = timezone.now()
    if timezone.is_aware(now_dt):
        now_dt = timezone.localtime(now_dt)
    
    # 1. Indicadores KPIs (Ventas, Drop Size, Impactos)
    total_ventas = Decimal('0.00')
    cant_pedidos = 0
    drop_size = Decimal('0.00')
    impactos_clientes = 0
    agrupacion_productos = []
    ventas_tendencia = []

    # Consulta a Oracle 11g
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

            # Tabla Agrupada por Proveedor, Familia y Línea
            query_agrupada = """
            SELECT * FROM (
                SELECT 
                    NVL(p.NOM_TERCERO, 'PROVEEDOR GENERAL') AS PROVEEDOR,
                    NVL(f.NOM_FAMILIA, 'FAMILIA GENERAL') AS FAMILIA,
                    NVL(l.NOM_LINEA, 'LÍNEA GENERAL') AS LINEA,
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
                    NVL(l.NOM_LINEA, 'LÍNEA GENERAL')
                ORDER BY TOT_MERCANCIA DESC
            ) WHERE ROWNUM <= 20
            """
            cursor.execute(query_agrupada)
            rows_ag = cursor.fetchall()
            for r in rows_ag:
                agrupacion_productos.append({
                    'proveedor': r[0],
                    'familia': r[1],
                    'linea': r[2],
                    'cantidad': float(r[3] or 0),
                    'tot_mercancia': Decimal(str(r[4] or 0)),
                    'tot_mercancia_cop': format_cop(r[4])
                })

            # Tendencia Diaria del Mes (Chart.js)
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
            rows_ch = cursor.fetchall()
            for r in rows_ch:
                ventas_tendencia.append({
                    'dia': r[0],
                    'monto': float(r[1] or 0)
                })

    except Exception as e:
        logger.error(f"Error al consultar métricas del Dashboard en Oracle: {e}")
        # Complemento local desde SQLite (Tickets POS)
        tickets_local = PosTicketHeader.objects.all()
        if tickets_local.exists():
            tot_loc = sum((t.tot_ticket for t in tickets_local), Decimal('0.00'))
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
    """
    Informe de Cartera y Recaudos del Día:
    - Agrupación de saldos por edades (Corriente, 1-30, 31-60, 61-90, 90+ días).
    - Recaudos del día actual.
    - Tabla detallada por Cliente (CO_TERCEROS).
    """
    total_cartera = Decimal('0.00')
    cartera_corriente = Decimal('0.00')
    cartera_1_30 = Decimal('0.00')
    cartera_31_60 = Decimal('0.00')
    cartera_61_90 = Decimal('0.00')
    cartera_90_mas = Decimal('0.00')
    recaudos_dia = Decimal('0.00')
    clientes_cartera = []

    # Consulta de Edades de Cartera a Oracle 11g
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
            rows_top = cursor.fetchall()
            for r in rows_top:
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
        logger.error(f"Error al consultar Informe de Cartera en Oracle: {e}")

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
    """
    Informe de Cierre Z / Arqueo de Caja POS:
    Cruza la base económica inicial con los tickets emitidos y medios de pago
    para calcular el 'Efectivo Esperado' vs. 'Efectivo Declarado' y alertar descuadres.
    """
    turno_id = request.GET.get('turno_id')
    caja_id = request.GET.get('caja_id', 'CAJA-01')
    
    if turno_id:
        turno = PosTurno.objects.filter(pk=turno_id).first()
    else:
        turno = PosTurno.objects.filter(caja_id=caja_id).order_by('-id_turno').first()

    if not turno:
        context = {'turno_encontrado': False, 'message': 'No se encontraron turnos de caja registrados.'}
    return render(request, 'reportes/cierre_caja.html', context)

from django.http import JsonResponse
from .services_analisis_ventas import VentasClientesAnalyticsService
import json

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def analisis_ventas_clientes_view(request):
    """
    Vista para el dashboard de analisis de ventas por clientes (SPEC-001).
    """
    service_for_filters = VentasClientesAnalyticsService()
    all_filtros = service_for_filters.get_filtros_disponibles()

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    
    if is_ajax:
        request_filtros = {
            'anio': request.GET.get('anio'),
            'zona_vendedor': request.GET.get('zona') or request.GET.get('vendedor'),
            'proveedor': request.GET.get('proveedor'),
            'linea': request.GET.get('linea'),
             'canal': request.GET.get('canal'),
             'mes': request.GET.get('mes'),
        }
        applied_filtros = {k: v for k, v in request_filtros.items() if v and v != 'todos'}
    else:
        # Carga inicial: aplicar filtro del anio mas reciente por defecto
        if all_filtros.get('anios'):
            applied_filtros = {'anio': all_filtros['anios'][0]}
        else:
            applied_filtros = {}

    service = VentasClientesAnalyticsService(applied_filtros)
    datos_tablero = service.get_datos_tablero()
    matriz_pivot = service.get_matriz_pivot_mensual()

    # Convertir el DataFrame de pivot a HTML
    if not matriz_pivot.empty:
        # Formatear valores a enteros con separador de miles
        matriz_pivot = matriz_pivot.applymap(lambda x: f"{int(x):,}".replace(",", "."))
        matriz_html = matriz_pivot.to_html(classes="w-full text-left text-sm", border=0, escape=False)
        matriz_html = matriz_html.replace('<table border="1" class="dataframe">', '<table class="w-full text-left text-sm">')
        matriz_html = matriz_html.replace('<thead>', '<thead class="bg-gray-100 uppercase text-xs text-gray-700 font-extrabold border-b sticky top-0">')
        matriz_html = matriz_html.replace('<th>', '<th class="p-3">')
        matriz_html = matriz_html.replace('<tbody>', '<tbody class="divide-y divide-gray-200">')
        matriz_html = matriz_html.replace('<tr>', '<tr class="hover:bg-gray-50">')
        matriz_html = matriz_html.replace('<td>', '<td class="p-3 text-right font-mono">')
    else:
        matriz_html = '<div class="p-4 text-center text-gray-500">No hay datos disponibles para los filtros seleccionados.</div>'

    payload = {
        'canal': datos_tablero['canal'],
        'municipios': datos_tablero['municipios'],
        'treemap': datos_tablero['treemap'],
        'matriz_html': matriz_html
    }

    if is_ajax:
        return JsonResponse(payload, encoder=DecimalEncoder)

    # Para GET inicial: serializar con DecimalEncoder para evitar 'Decimal is not defined' en JavaScript
    datos_iniciales_json = json.dumps(payload, cls=DecimalEncoder)

    context = {
        'filtros': all_filtros,
        'datos_iniciales_json': datos_iniciales_json,
    }
    return render(request, 'reportes/analisis_ventas_clientes.html', context)
