import json
import logging
from decimal import Decimal
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import connection
from django.utils import timezone
from pos.models import (
    PosTurno, PosTicketHeader, PosTicketDetail,
    PrPromocion, PrCondicion, PrAccion, PosPuntosCliente
)
from pos.pos_consolidator import consolidar_cierre_z

logger = logging.getLogger(__name__)

def pos_home(request):
    """Renderiza la pantalla principal del Punto de Venta (POS)."""
    turno_abierto = PosTurno.objects.filter(estado='ABIERTO').order_by('-id_turno').first()
    context = {
        'turno_abierto': turno_abierto
    }
    return render(request, 'pos/pos.html', context)


def api_catalog(request):
    """
    Descarga el catálogo maestro (IN_ARTICULOS + IN_GRAVAMENES).
    Se ejecuta al iniciar el turno para sincronizar IndexedDB en el navegador.
    """
    query = """
    SELECT 
        a.ID_ARTICULO,
        a.REFERENCIA,
        a.NOM_ARTICULO,
        NVL(a.CODIGO_BARRAS, '') AS CODIGO_BARRAS,
        NVL(a.VLR_REPOSICION, 0) AS PRECIO,
        a.ID_GRAVAMEN,
        NVL(g.PORC_GRAVAMEN, 0) AS PORC_GRAVAMEN
    FROM IN_ARTICULOS a
    LEFT JOIN IN_GRAVAMENES g ON a.ID_GRAVAMEN = g.ID_GRAVAMEN
    WHERE NVL(a.ACTIVO_INACTIVO, 'A') = 'A'
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
            columns = [col[0].lower() for col in cursor.description]
            catalog = [dict(zip(columns, row)) for row in rows]
            
            for item in catalog:
                item['precio'] = float(item['precio'])
                item['porc_gravamen'] = float(item['porc_gravamen'])

        return JsonResponse({'status': 'success', 'catalog': catalog, 'count': len(catalog)})
    except Exception as e:
        logger.error(f"Error al obtener catálogo maestro POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


def api_terceros(request):
    """
    Buscador rápido de clientes en CO_TERCEROS para cambiar el NIT por defecto ('222222222222').
    Utiliza ROWNUM <= 30 para estricta compatibilidad con Oracle 11g.
    """
    q = request.GET.get('q', '').strip().upper()
    if not q:
        query = """
        SELECT * FROM (
            SELECT ID_TERCERO, NOM_TERCERO FROM CO_TERCEROS ORDER BY NOM_TERCERO
        ) WHERE ROWNUM <= 30
        """
        params = []
    else:
        query = """
        SELECT * FROM (
            SELECT ID_TERCERO, NOM_TERCERO FROM CO_TERCEROS 
            WHERE UPPER(ID_TERCERO) LIKE %s OR UPPER(NOM_TERCERO) LIKE %s
            ORDER BY NOM_TERCERO
        ) WHERE ROWNUM <= 30
        """
        wildcard = f"%{q}%"
        params = [wildcard, wildcard]

    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            terceros = [{'id_tercero': row[0], 'nom_tercero': row[1]} for row in rows]

        return JsonResponse({'status': 'success', 'terceros': terceros})
    except Exception as e:
        logger.error(f"Error en buscador de terceros POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
def api_shift_open(request):
    """Apertura de Caja: Exige ingresar la Base Económica inicial antes de facturar."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        caja_id = data.get('caja_id', 'CAJA-01')
        usuario = data.get('usuario', request.user.username if request.user.is_authenticated else 'cajero')
        base_economica = Decimal(str(data.get('base_economica', 0)))

        turno_existente = PosTurno.objects.filter(caja_id=caja_id, estado='ABIERTO').first()
        if turno_existente:
            return JsonResponse({
                'status': 'error',
                'message': f'La caja {caja_id} ya tiene un turno abierto (#{turno_existente.id_turno}).'
            }, status=400)

        turno = PosTurno.objects.create(
            caja_id=caja_id,
            usuario=usuario,
            base_economica=base_economica,
            estado='ABIERTO'
        )

        return JsonResponse({
            'status': 'success',
            'message': f'Turno de caja #{turno.id_turno} abierto exitosamente.',
            'turno': {
                'id_turno': turno.id_turno,
                'caja_id': turno.caja_id,
                'usuario': turno.usuario,
                'base_economica': float(turno.base_economica),
                'fch_apertura': turno.fch_apertura.strftime('%Y-%m-%d %H:%M:%S'),
                'estado': turno.estado
            }
        })
    except Exception as e:
        logger.error(f"Error en Apertura de Caja POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


def api_shift_status(request):
    """Consulta el estado del turno actual de caja."""
    caja_id = request.GET.get('caja_id', 'CAJA-01')
    turno = PosTurno.objects.filter(caja_id=caja_id, estado='ABIERTO').order_by('-id_turno').first()
    if not turno:
        return JsonResponse({'status': 'success', 'turno_abierto': False})

    tickets_count = turno.tickets.count()
    return JsonResponse({
        'status': 'success',
        'turno_abierto': True,
        'turno': {
            'id_turno': turno.id_turno,
            'caja_id': turno.caja_id,
            'usuario': turno.usuario,
            'base_economica': float(turno.base_economica),
            'fch_apertura': turno.fch_apertura.strftime('%Y-%m-%d %H:%M:%S'),
            'estado': turno.estado,
            'tickets_count': tickets_count
        }
    })


@csrf_exempt
def api_sync_tickets(request):
    """
    Endpoint para recibir y almacenar los tickets sincronizados desde IndexedDB.
    Acredita puntos acumulados al saldo del cliente en PosPuntosCliente.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        payload = json.loads(request.body)
        tickets_data = payload.get('tickets', [])
        caja_id = payload.get('caja_id', 'CAJA-01')

        turno = PosTurno.objects.filter(caja_id=caja_id, estado='ABIERTO').order_by('-id_turno').first()
        if not turno:
            return JsonResponse({
                'status': 'error',
                'message': 'No hay un turno abierto para asociar los tickets sincronizados.'
            }, status=400)

        synced_ids = []
        for t_data in tickets_data:
            ticket_id = t_data.get('ticket_id')
            if not ticket_id:
                continue

            if PosTicketHeader.objects.filter(ticket_id=ticket_id).exists():
                synced_ids.append(ticket_id)
                continue

            id_tercero = t_data.get('id_tercero', '222222222222')
            puntos_ganados = int(t_data.get('puntos_ganados', 0))
            puntos_redimidos = int(t_data.get('puntos_redimidos_ticket', 0))

            header = PosTicketHeader.objects.create(
                ticket_id=ticket_id,
                turno=turno,
                id_tercero=id_tercero,
                nom_tercero=t_data.get('nom_tercero', 'VENTAS MASIVAS (CONSUMIDOR FINAL)'),
                fch_ticket=t_data.get('fch_ticket', timezone.now()),
                tot_mercancia=Decimal(str(t_data.get('tot_mercancia', 0))),
                tot_iva=Decimal(str(t_data.get('tot_iva', 0))),
                tot_ticket=Decimal(str(t_data.get('tot_ticket', 0))),
                descuento_promocion=Decimal(str(t_data.get('descuento_promocion', 0))),
                pago_efectivo=Decimal(str(t_data.get('pago_efectivo', 0))),
                pago_tarjeta=Decimal(str(t_data.get('pago_tarjeta', 0))),
                pago_transferencia=Decimal(str(t_data.get('pago_transferencia', 0))),
                pago_puntos=Decimal(str(t_data.get('pago_puntos', 0))),
                puntos_ganados=puntos_ganados,
                puntos_redimidos_ticket=puntos_redimidos,
                cambio=Decimal(str(t_data.get('cambio', 0))),
                sync_status=True,
                consolidado_cierre=False
            )

            for item_data in t_data.get('items', []):
                PosTicketDetail.objects.create(
                    ticket=header,
                    id_articulo=item_data.get('id_articulo'),
                    referencia=item_data.get('referencia', ''),
                    nom_articulo=item_data.get('nom_articulo', ''),
                    cantidad=Decimal(str(item_data.get('cantidad', 1))),
                    vlr_unitario=Decimal(str(item_data.get('vlr_unitario', 0))),
                    porc_descuento=Decimal(str(item_data.get('porc_descuento', 0))),
                    porc_iva=Decimal(str(item_data.get('porc_iva', 0))),
                    vlr_iva=Decimal(str(item_data.get('vlr_iva', 0))),
                    tot_linea=Decimal(str(item_data.get('tot_linea', 0)))
                )

            # Actualizar Fidelización Puntos Cliente (Earn)
            if id_tercero != '222222222222' and puntos_ganados > 0:
                puntos_obj, _ = PosPuntosCliente.objects.get_or_create(id_tercero=id_tercero)
                puntos_obj.puntos_saldo += puntos_ganados
                puntos_obj.puntos_acumulados += puntos_ganados
                puntos_obj.save()

            synced_ids.append(ticket_id)

        return JsonResponse({
            'status': 'success',
            'synced_ids': synced_ids,
            'message': f'{len(synced_ids)} tickets sincronizados correctamente.'
        })
    except Exception as e:
        logger.error(f"Error al sincronizar tickets POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
def api_cierre_z(request):
    """Ejecuta el Cierre Z de la caja: Consolidación Kardex + Asiento Contable Maestro en Oracle."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        caja_id = data.get('caja_id', 'CAJA-01')

        turno = PosTurno.objects.filter(caja_id=caja_id, estado='ABIERTO').order_by('-id_turno').first()
        if not turno:
            return JsonResponse({'status': 'error', 'message': 'No hay un turno abierto para cerrar.'}, status=400)

        res = consolidar_cierre_z(turno.id_turno)
        if res.get('status') == 'success':
            return JsonResponse(res)
        else:
            return JsonResponse(res, status=400)

    except Exception as e:
        logger.error(f"Error en Cierre Z POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


# ==========================================
# ENDPOINTS PROMOCIONES Y FIDELIZACIÓN (CAPÍTULO 7)
# ==========================================

def api_promociones(request):
    """
    Retorna el catálogo JSON de promociones activas y sus reglas (Condiciones y Acciones)
    para ser descargadas e inspeccionadas en tiempo real por IndexedDB en el POS.
    """
    try:
        now = timezone.now()
        # Filtro de vigencia: fch_inicio <= now <= fch_fin
        promos_qs = PrPromocion.objects.filter(activo=True).order_by('-prioridad')
        
        promociones_data = []
        for p in promos_qs:
            condiciones = list(p.condiciones.values('id_condicion', 'tipo_condicion', 'valor_condicion', 'cantidad_minima'))
            acciones = list(p.acciones.values('id_accion', 'tipo_accion', 'valor_accion', 'id_articulo_regalo'))
            
            # Convertir Decimals a float para JSON
            for c in condiciones:
                c['cantidad_minima'] = float(c['cantidad_minima'])
            for a in acciones:
                a['valor_accion'] = float(a['valor_accion'])

            promociones_data.append({
                'id_promocion': p.id_promocion,
                'nom_promocion': p.nom_promocion,
                'prioridad': p.prioridad,
                'condiciones': condiciones,
                'acciones': acciones
            })

        return JsonResponse({'status': 'success', 'promociones': promociones_data, 'count': len(promociones_data)})
    except Exception as e:
        logger.error(f"Error al servir API de promociones POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


def api_puntos_saldo(request):
    """
    Consulta en tiempo real el saldo de puntos de fidelización de un cliente (Burn).
    ESTRICTAMENTE ONLINE: Requiere conexión con el servidor.
    """
    id_tercero = request.GET.get('id_tercero', '').strip()
    if not id_tercero or id_tercero == '222222222222':
        return JsonResponse({'status': 'success', 'id_tercero': id_tercero, 'puntos_saldo': 0, 'valor_cop': 0})

    try:
        puntos_obj, _ = PosPuntosCliente.objects.get_or_create(id_tercero=id_tercero)
        saldo = puntos_obj.puntos_saldo
        valor_cop = saldo * 10 # Regla: 1 punto = $10 COP

        return JsonResponse({
            'status': 'success',
            'id_tercero': id_tercero,
            'puntos_saldo': saldo,
            'valor_cop': valor_cop
        })
    except Exception as e:
        logger.error(f"Error en API puntos saldo POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
def api_puntos_redimir(request):
    """
    Redime/descuenta puntos del saldo del cliente al pagar (Burn).
    ESTRICTAMENTE ONLINE: Valida saldo actual y procesa el descuento de puntos.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        id_tercero = data.get('id_tercero')
        puntos_a_redimir = int(data.get('puntos_redimir', 0))

        if not id_tercero or id_tercero == '222222222222' or puntos_a_redimir <= 0:
            return JsonResponse({'status': 'error', 'message': 'Parámetros de redención inválidos.'}, status=400)

        puntos_obj = PosPuntosCliente.objects.filter(id_tercero=id_tercero).first()
        if not puntos_obj or puntos_obj.puntos_saldo < puntos_a_redimir:
            saldo_actual = puntos_obj.puntos_saldo if puntos_obj else 0
            return JsonResponse({
                'status': 'error',
                'message': f'Saldo insuficiente. Puntos disponibles: {saldo_actual}, solicitados: {puntos_a_redimir}.'
            }, status=400)

        # Descontar puntos
        puntos_obj.puntos_saldo -= puntos_a_redimir
        puntos_obj.puntos_redimidos += puntos_a_redimir
        puntos_obj.save()

        valor_descuento_cop = puntos_a_redimir * 10

        return JsonResponse({
            'status': 'success',
            'message': f'{puntos_a_redimir} puntos redimidos exitosamente ($ {valor_descuento_cop:,.2f} COP).',
            'puntos_redimidos': puntos_a_redimir,
            'valor_cop': valor_descuento_cop,
            'nuevo_saldo_puntos': puntos_obj.puntos_saldo
        })
    except Exception as e:
        logger.error(f"Error al redimir puntos POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
