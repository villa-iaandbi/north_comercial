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

    tickets = turno.tickets.all()
    
    base_economica = turno.base_economica
    tot_efectivo = sum((t.pago_efectivo for t in tickets), Decimal('0.00'))
    tot_tarjeta = sum((t.pago_tarjeta for t in tickets), Decimal('0.00'))
    tot_transferencia = sum((t.pago_transferencia for t in tickets), Decimal('0.00'))
    tot_puntos = sum((t.pago_puntos for t in tickets), Decimal('0.00'))
    tot_ventas = sum((t.tot_ticket for t in tickets), Decimal('0.00'))

    efectivo_esperado = base_economica + tot_efectivo

    # Efectivo Declarado por el usuario (parámetro POST o GET)
    raw_declarado = request.GET.get('efectivo_declarado')
    efectivo_declarado = None
    descuadre = Decimal('0.00')
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
        'es_cuadrado': (descuadre == Decimal('0.00')) if efectivo_declarado is not None else True,
        'todos_turnos': PosTurno.objects.order_by('-id_turno')[:10]
    }
    return render(request, 'reportes/cierre_caja.html', context)
