import json
import logging
import os
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import connection
from django.db.models import Q
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from pos.models import (
    PosTurno, PosTicketHeader, PosTicketDetail,
    PrPromocion, PrCondicion, PrAccion, PosPuntosCliente
)
from pos.pos_consolidator import consolidar_cierre_z
from pos.views_auth import pos_role_required

logger = logging.getLogger(__name__)

ROLES_APERTURA = ('CAJERO', 'DBA', 'SUPERVISOR')


# ==========================================
# TIQUETE POS: CUFE LOCAL + JSON DIAN + PDF TÉRMICO
# ==========================================

try:
    from zoneinfo import ZoneInfo
    BOGOTA_TZ = ZoneInfo('America/Bogota')
except Exception:  # pragma: no cover
    import pytz
    BOGOTA_TZ = pytz.timezone('America/Bogota')

CONSUMIDOR_FINAL = '222222222222'


def _parsear_fecha_ticket(valor):
    """
    Normaliza la fecha de un ticket a datetime naive (hora de Colombia). Soporta:
      - datetime / ISO 8601 ('YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DDTHH:MM:SS', con o sin 'Z'/zona)
      - Localizado es-CO de Chrome: '19/11/2026, 5:32:15 p. m.' (a. m./p. m./AM/PM, con \xa0)
      - 24h: 'DD/MM/YYYY HH:MM[:SS]' o 'DD/MM/YYYY, HH:MM[:SS]'
    Si viene vacío o no se puede interpretar, retorna la hora actual de Colombia.
    """
    ahora = datetime.now(BOGOTA_TZ).replace(tzinfo=None)
    if isinstance(valor, datetime):
        dt = valor
    else:
        texto = str(valor or '')
        # Espacios no separables / estrechos (\xa0, \u202f) y espacios múltiples
        texto = re.sub(r'[\s\u00a0\u202f]+', ' ', texto).strip()
        if not texto:
            return ahora
        dt = None
        try:
            dt = parse_datetime(texto)
        except ValueError:
            dt = None
        if dt is None:
            m_loc = re.match(
                r'^(\d{1,2})/(\d{1,2})/(\d{4})[,\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(?:([ap])\.?\s*m\.?)?$',
                texto, re.IGNORECASE,
            )
            if not m_loc:
                logger.warning(f"POS: fecha de ticket no reconocida: {valor!r}")
                return ahora
            dia, mes, anio, hora, minuto, segundo, meridiano = m_loc.groups()
            hora = int(hora)
            if meridiano:
                if not 1 <= hora <= 12:
                    return ahora
                hora = hora % 12 + (12 if meridiano.lower() == 'p' else 0)
            try:
                dt = datetime(int(anio), int(mes), int(dia), hora, int(minuto), int(segundo or 0))
            except ValueError:
                logger.warning(f"POS: fecha de ticket inválida: {valor!r}")
                return ahora
    if timezone.is_aware(dt):
        dt = dt.astimezone(BOGOTA_TZ).replace(tzinfo=None)
    return dt


def _fecha_bogota(valor):
    """Normaliza fch_ticket (datetime o str ISO) a datetime naive en hora de Colombia."""
    if isinstance(valor, str):
        valor = parse_datetime(valor)
    if not isinstance(valor, datetime):
        return datetime.now(BOGOTA_TZ).replace(tzinfo=None)
    if timezone.is_aware(valor):
        valor = valor.astimezone(BOGOTA_TZ).replace(tzinfo=None)
    return valor


def _datos_empresa(request):
    """Datos de la empresa para el encabezado/CUFE. Nunca lanza excepciones."""
    datos = {
        'nom_sistema': request.session.get('nom_empresa') or '',
        'nit_emp': '', 'dir_emp': '', 'municipio_emp': '', 'tels_emp': '',
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT NOM_SISTEMA, NIT, DIRECCION FROM SG_SISTEMAS WHERE ID_SISTEMA = '1'")
            row = cursor.fetchone()
        if row:
            datos['nom_sistema'] = row[0] or datos['nom_sistema']
            datos['nit_emp'] = str(row[1] or '')
            datos['dir_emp'] = row[2] or ''
    except Exception as exc:
        logger.warning(f"POS: no se pudieron leer los datos de empresa en Oracle: {exc}")
    return datos


def _nit_sin_dv(nit):
    """Solo dígitos del NIT sin dígito de verificación (formato 'NNNNNNNNN-D')."""
    return re.sub(r'\D', '', str(nit or '').split('-')[0])


CENT = Decimal('0.01')
CERO = Decimal('0')


def _r2(valor):
    """Redondeo half-up a 2 decimales (idéntico a r2() de templates/pos/pos.html)."""
    return Decimal(valor).quantize(CENT, rounding=ROUND_HALF_UP)


def calcular_ticket_en_reversa(lineas, descuento_global=0):
    """
    Cálculo en reversa de un ticket POS cuyos precios YA INCLUYEN IVA e INC.
    Misma fórmula que recalcTaxes() de templates/pos/pos.html, de modo que
    Caja (JS) == PDF == CUFE == JSON Binapps.

    lineas: iterable de dicts con cantidad, vlr_unitario (con impuestos), porc_descuento,
            porc_iva, porc_inc y vlr_inc_fijo_unit (los dos últimos opcionales).
    Retorna {'lineas': [...], 'tot_bruto', 'tot_descuento', 'tot_base', 'tot_iva', 'tot_inc',
             'tot_pagar'} con Decimals a 2 decimales. Por línea: bruto, descuento, total_pagar,
    base_neta, iva, inc, vlr_unit_neto; siempre base_neta + iva + inc == total_pagar.
    """
    lineas = list(lineas)
    pre = []
    for ln in lineas:
        bruto = _r2(Decimal(ln['cantidad']) * Decimal(ln['vlr_unitario']))
        propio = _r2(bruto * Decimal(ln.get('porc_descuento') or 0) / Decimal('100'))
        pre.append((bruto, propio, max(CERO, _r2(bruto - propio))))

    suma_neto = _r2(sum((p[2] for p in pre), CERO))
    global_dto = min(max(_r2(descuento_global or 0), CERO), suma_neto)
    ultima_pos = max((k for k, p in enumerate(pre) if p[2] > 0), default=-1)

    resultado = []
    asignado = CERO
    for k, (ln, (bruto, propio, neto)) in enumerate(zip(lineas, pre)):
        cuota = CERO
        if suma_neto > 0 and neto > 0:
            cuota = _r2(global_dto - asignado) if k == ultima_pos else _r2(global_dto * neto / suma_neto)
            asignado = _r2(asignado + cuota)
        pagar = max(CERO, _r2(neto - cuota))

        cantidad = Decimal(ln['cantidad'])
        inc_fijo = min(pagar, _r2(cantidad * Decimal(ln.get('vlr_inc_fijo_unit') or 0)))
        saldo = _r2(pagar - inc_fijo)
        p_iva = Decimal(ln.get('porc_iva') or 0) / Decimal('100')
        p_inc = Decimal(ln.get('porc_inc') or 0) / Decimal('100')
        base = _r2(saldo / (Decimal('1') + p_iva + p_inc))
        iva = _r2(base * p_iva)
        inc_pct = _r2(base * p_inc)
        base = _r2(pagar - iva - inc_pct - inc_fijo)  # el residuo de redondeo (<= 0.01) va a la base
        resultado.append({
            'bruto': bruto, 'descuento': _r2(propio + cuota), 'total_pagar': pagar,
            'base_neta': base, 'iva': iva, 'inc': _r2(inc_fijo + inc_pct),
            'vlr_unit_neto': (base / cantidad) if cantidad else CERO,
        })

    def total(clave):
        return _r2(sum((r[clave] for r in resultado), CERO))

    return {
        'lineas': resultado,
        'tot_bruto': total('bruto'), 'tot_descuento': total('descuento'),
        'tot_base': total('base_neta'), 'tot_iva': total('iva'), 'tot_inc': total('inc'),
        'tot_pagar': total('total_pagar'),
    }


def _cargar_inc_fijo_articulos(ids_articulos):
    """
    {id_articulo: INC fijo por unidad} desde IN_ARTICULOS.IMPOCONSUMO (Oracle). El detalle POS
    guardado en SQLite no conserva el INC, así que se consulta con la misma fuente del catálogo.
    Si Oracle no responde retorna {} (se calcula sin INC fijo) y registra una advertencia.
    """
    ids = sorted({str(i) for i in ids_articulos if i})
    if not ids:
        return {}
    try:
        marcadores = ','.join(['%s'] * len(ids))
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT ID_ARTICULO, NVL(IMPOCONSUMO, 0) FROM IN_ARTICULOS WHERE ID_ARTICULO IN ({marcadores})",
                ids,
            )
            return {str(r[0]): Decimal(str(r[1] or 0)) for r in cursor.fetchall()}
    except Exception as exc:
        logger.warning(f"POS: no se pudo leer IMPOCONSUMO de IN_ARTICULOS: {exc}")
        return {}


def _calcular_ticket(header, items):
    """Aplica calcular_ticket_en_reversa a un ticket grabado (header + detalle SQLite)."""
    inc_fijo = _cargar_inc_fijo_articulos(it.id_articulo for it in items)
    lineas = [{
        'cantidad': it.cantidad, 'vlr_unitario': it.vlr_unitario, 'porc_descuento': it.porc_descuento,
        'porc_iva': it.porc_iva, 'porc_inc': CERO,
        'vlr_inc_fijo_unit': inc_fijo.get(str(it.id_articulo), CERO),
    } for it in items]
    calculo = calcular_ticket_en_reversa(lineas, header.descuento_promocion)
    if abs(calculo['tot_pagar'] - header.tot_ticket) > CENT:
        logger.warning(
            f"POS {header.ticket_id}: total recalculado {calculo['tot_pagar']} != tot_ticket grabado {header.tot_ticket}"
        )
    return calculo


def _armar_datos_dian(header, items, prefijo, empresa, calculo, tercero=None):
    """
    Construye (header_data, items_data, taxes_data) con las llaves de InvoicePayloadBuilder.
    VLR_UNITARIO es el valor unitario NETO sin impuestos (base_neta / cantidad), por lo que
    CANTIDAD * VLR_UNITARIO = base_neta y TaxExclusive + IVA + INC = PayableAmount.
    `tercero` (obtener_tercero_pos) aporta los datos reales de CO_TERCEROS para CustomerInformation.
    """
    tercero = tercero or {}
    nit_adq = tercero.get('nit') or header.id_tercero or CONSUMIDOR_FINAL
    razon_social = tercero.get('razon_social') or header.nom_tercero
    fecha = _fecha_bogota(header.fch_ticket)
    id_safe = re.sub(r'[^A-Za-z0-9_-]', '_', header.ticket_id)
    header_data = {
        'ID_DOCUMENTO': id_safe,
        'NUM_DOCUMENTO': header.ticket_id,
        'FCH_DOCUMENTO': fecha.strftime('%Y-%m-%dT%H:%M:%S'),
        'TOT_DOCUMENTO': float(calculo['tot_pagar']),
        'TOT_MERCANCIA': float(calculo['tot_base']),
        'PLAZO_PAGO': 0,
        'REGIMEN': tercero.get('regimen') or '2',
        'NUM_RESOLUCION': '',
        'NIT_TERCERO': nit_adq,
        'DIGITO_VERIFICACION': tercero.get('digito_verificacion', ''),
        'RAZON_SOCIAL': razon_social,
        'NOMBRE_COMERCIAL': tercero.get('nombre_comercial') or razon_social,
        'EMAIL': tercero.get('email', ''),
        'DIRECCION': tercero.get('direccion', ''),
        'TELEFONO': tercero.get('telefono', ''),
        'ID_MUNICIPIO_DIAN': tercero.get('id_municipio_dian', ''),
        'DEPARTAMENTO_NOMBRE': tercero.get('departamento_nombre', ''),
        'MUNICIPIO_NOMBRE': tercero.get('municipio_nombre', ''),
        'CREADO_POR': empresa.get('nom_sistema', ''),
        'VENDEDOR_NOMBRE_LARGO': '',
    }
    items_data = []
    for it, c in zip(items, calculo['lineas']):
        items_data.append({
            'ID_ARTICULO': it.id_articulo,
            'NOM_ARTICULO': it.nom_articulo,
            'CANTIDAD': float(it.cantidad),
            'VLR_UNITARIO': float(c['vlr_unit_neto']),
            'VLR_IVA_LINEA': float(c['iva']),
            'PORC_IVA': float(it.porc_iva),
            'VLR_IMPOCONSUMO_LINEA': float(c['inc']),
            'PORC_IMPOCONSUMO': 0.0,
            'BASE_LINEA': float(c['base_neta']),
        })
    return header_data, items_data, {}


def _armar_ticket_data(header, items, cufe, empresa, estado_dian, calculo, atendido_por='', tercero=None):
    """
    Diccionario para templates/plantilla_tiquete_pos_weasyprint.html.

    V.UNIT y TOTAL se imprimen con impuestos incluidos (tal como se vende); base, IVA e INC
    salen de calcular_ticket_en_reversa (la misma fuente del CUFE y del JSON DIAN).
    Los valores se envían numéricos; el renderizador los redondea a cero decimales.
    Caja = TS_CAJAS.COD_CAJA (empresa['cod_caja']); tercero = CO_TERCEROS (solo NIT y NOM_TERCERO).
    """
    tercero = tercero or {}
    fecha = _fecha_bogota(header.fch_ticket)
    turno = header.turno if header.turno_id else None

    lineas = []
    grupos = {}
    for it, c in zip(items, calculo['lineas']):
        lineas.append({
            'cantidad': it.cantidad,
            'referencia': it.referencia or it.id_articulo,
            'nom_articulo': it.nom_articulo,
            'vlr_unitario': it.vlr_unitario,
            'vlr_total': c['bruto'],
            'descuento': c['descuento'],
            'porc_iva': it.porc_iva or CERO,
        })
        g = grupos.setdefault(it.porc_iva or CERO, {'base': CERO, 'iva': CERO, 'inc': CERO})
        g['base'] += c['base_neta']
        g['iva'] += c['iva']
        g['inc'] += c['inc']

    # Una fila por tarifa de IVA; el INC (si existe) se agrega como fila propia para que
    # BASE + VLR IMPUESTO del TOTAL cuadre con el TOTAL A PAGAR.
    impuestos = []
    for porc, g in sorted(grupos.items()):
        impuestos.append({'porc': porc, 'base': g['base'], 'valor': g['iva']})
    tot_inc = calculo['tot_inc']
    if tot_inc:
        impuestos.append({'porc': CERO, 'tipo': 'INC', 'base': CERO, 'valor': tot_inc})

    pagos = [
        {'metodo': nombre, 'valor': valor}
        for nombre, valor in (
            ('Efectivo', header.pago_efectivo), ('Tarjeta', header.pago_tarjeta),
            ('Transferencia', header.pago_transferencia), ('Puntos', header.pago_puntos),
        ) if valor
    ]
    return {
        **empresa,
        'num_documento': header.ticket_id,
        'fch_documento': fecha.strftime('%Y-%m-%d %H:%M:%S'),
        'caja': empresa.get('cod_caja') or '',  # solo TS_CAJAS.COD_CAJA (nunca nombre/alias de la caja)
        'atendido_por': atendido_por or (turno.usuario if turno else ''),
        'nom_tercero': tercero.get('nom_tercero') or header.nom_tercero,
        'nit_tercero': tercero.get('nit') or header.id_tercero or CONSUMIDOR_FINAL,
        'items': lineas,
        'tot_items': len(lineas),
        'tot_mercancia': calculo['tot_bruto'],
        'tot_descuento': calculo['tot_descuento'],
        'total_descuentos': calculo['tot_descuento'],
        'tot_documento': calculo['tot_pagar'],
        'impuestos': impuestos,
        'pagos': pagos,
        'cambio': header.cambio if header.pago_efectivo else CERO,
        'cufe': cufe,
        'estado_dian': estado_dian,
        'fecha_documento_raw': fecha,
    }


def _nombre_atendido_por(request, header):
    """Nombre del cajero tal como se muestra en el menú principal (base.html): sesión usuario_nombre / pos_user.nombre."""
    pos_user = request.session.get('pos_user') or {}
    nombre = request.session.get('usuario_nombre') or pos_user.get('nombre')
    if nombre:
        return str(nombre).strip()
    return header.turno.usuario if header.turno_id else ''


def _ruta_relativa_pdf(pdf_path):
    """Ruta del PDF relativa a MEDIA_ROOT/BASE_DIR (no se expone la ruta absoluta del servidor)."""
    base = str(getattr(settings, 'MEDIA_ROOT', '') or '').strip() or str(settings.BASE_DIR)
    try:
        return Path(pdf_path).resolve().relative_to(Path(base).resolve()).as_posix()
    except Exception:
        return os.path.basename(pdf_path)


def _procesar_tiquete_pos(request, header, empresa, es_nuevo=True):
    """
    Post-grabación de un tiquete POS: CUFE local, JSON DIAN real en disco, encolado DIAN
    y PDF térmico. El tiquete ya está grabado en SQLite; NADA de lo que ocurre aquí
    puede propagar una excepción ni bloquear la caja.

    es_nuevo=False (reintento de sincronización de un ticket ya grabado): solo se
    recalculan CUFE y PDF; no se vuelve a escribir JSON ni a encolar.
    """
    prefijo = request.session.get('prefijo_numeracion')
    resultado = {
        'ticket_id': header.ticket_id, 'cufe': '', 'pdf_nombre': None, 'pdf_ruta': None,
        'estado_dian': 'PENDIENTE_CIERRE_Z', 'payload_guardado': False,
    }
    items = []
    try:
        header.refresh_from_db()
        items = list(header.items.order_by('id'))
        calculo = _calcular_ticket(header, items)
    except Exception as exc:
        logger.error(f"POS {header.ticket_id}: no se pudo leer el tiquete grabado: {exc}")
        resultado['estado_dian'] = 'ERROR_LECTURA'
        return resultado

    # Adquirente real desde CO_TERCEROS (NIT/ID del ticket; respaldo 222222222222). No lanza excepciones.
    from impresion.document_renderer import obtener_tercero_pos
    tercero = obtener_tercero_pos(header.id_tercero or CONSUMIDOR_FINAL)

    # 1. CUFE local (SHA-384). Siempre disponible, sin red.
    try:
        from facturacion.dian_async import calcular_cufe_local, get_dian_cufe_config
        cfg = get_dian_cufe_config()
        fecha = _fecha_bogota(header.fch_ticket)
        resultado['cufe'] = calcular_cufe_local(
            num_fac=header.ticket_id,
            fecha=fecha.strftime('%Y-%m-%d'),
            hora=fecha.strftime('%H:%M:%S-05:00'),
            val_fac=calculo['tot_base'], val_iva=calculo['tot_iva'], val_inc=calculo['tot_inc'], val_ica=0,
            val_tot=calculo['tot_pagar'],
            nit_ofe=_nit_sin_dv(empresa.get('nit_emp')),
            num_adq=tercero.get('nit') or header.id_tercero or CONSUMIDOR_FINAL,
            clave_tecnica=cfg['clave_tecnica'], tipo_ambiente=cfg['tipo_ambiente'],
        )
    except Exception as exc:
        logger.error(f"POS {header.ticket_id}: error calculando CUFE local: {exc}")

    # 2. JSON real (dry_run=False) escrito en logs/fel_payloads + 3. encolado DIAN
    if es_nuevo:
        payload = None
        try:
            from services.payload_builder import InvoicePayloadBuilder
            header_data, items_data, taxes_data = _armar_datos_dian(header, items, prefijo, empresa, calculo, tercero)
            payload = InvoicePayloadBuilder.build_invoice_payload(header_data, items_data, taxes_data)
            resultado['payload_guardado'] = True
        except Exception as exc:
            logger.error(f"POS {header.ticket_id}: no se pudo generar el JSON DIAN: {exc}")
            resultado['estado_dian'] = 'ERROR_PAYLOAD'

        # La transmisión por tiquete es opt-in: el Cierre Z ya consolida y transmite
        # un documento maestro (ver pos_consolidator); transmitir ambos duplicaría la venta.
        if payload is not None and getattr(settings, 'POS_DIAN_TRANSMIT_ON_SYNC',
                                           os.getenv('POS_DIAN_TRANSMIT_ON_SYNC', '').lower() in ('1', 'true', 'yes')):
            try:
                from facturacion.dian_async import encolar_transmision_pos
                resultado['task_id'] = str(encolar_transmision_pos(header.ticket_id, payload))
                resultado['estado_dian'] = 'ENCOLADO'
            except Exception as exc:
                logger.error(f"POS {header.ticket_id}: no se pudo encolar la transmisión DIAN: {exc}")
                resultado['estado_dian'] = 'PENDIENTE_CIERRE_Z'

    # 4. PDF térmico
    try:
        from impresion.document_renderer import render_pos_ticket_to_pdf, obtener_datos_emisor_pos
        caja_id = header.turno.caja_id if header.turno_id else ''
        # Emisor, resolución DIAN y responsabilidades tributarias: misma fuente Oracle que la factura masiva.
        emisor = obtener_datos_emisor_pos(caja_id)
        empresa_pdf = {**empresa, **{k: v for k, v in emisor.items() if v}}
        ticket_data = _armar_ticket_data(
            header, items, resultado['cufe'], empresa_pdf, resultado['estado_dian'], calculo,
            atendido_por=_nombre_atendido_por(request, header), tercero=tercero,
        )
        pdf_path = render_pos_ticket_to_pdf(ticket_data, prefijo_numeracion=prefijo)
        resultado['pdf_nombre'] = os.path.basename(pdf_path)
        resultado['pdf_ruta'] = _ruta_relativa_pdf(pdf_path)
    except Exception as exc:
        logger.error(f"POS {header.ticket_id}: no se pudo generar el PDF térmico: {exc}")

    return resultado


@pos_role_required(*ROLES_APERTURA)
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
        NVL(g.PORC_GRAVAMEN, 0) AS PORC_GRAVAMEN,
        NVL(a.IMPOCONSUMO, 0) AS VLR_INC_FIJO_UNIT,
        a.ID_GRUPO,
        a.ID_LINEA
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
                # IN_ARTICULOS.IMPOCONSUMO es un valor fijo por unidad (COP); no existe INC porcentual.
                item['vlr_inc_fijo_unit'] = float(item['vlr_inc_fijo_unit'])
                item['porc_impoconsumo'] = 0.0
                # Para cruzar promociones por Grupo/Línea en el POS offline (siempre texto; '' si es nulo).
                item['id_grupo'] = str(item.get('id_grupo') or '').strip()
                item['id_linea'] = str(item.get('id_linea') or '').strip()

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
@pos_role_required(*ROLES_APERTURA)
def api_shift_open(request):
    """Apertura de Caja: Exige ingresar la Base Económica inicial antes de facturar."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        caja_id = data.get('caja_id', 'CAJA-01')
        # El usuario sale de la sesión POS; el body nunca define quién abre el turno.
        usuario = (
            (request.session.get('pos_user') or {}).get('email')
            or request.session.get('id_usuario')
            or (request.user.username if request.user.is_authenticated else None)
            or 'cajero'
        )
        usuario = str(usuario)
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
        tickets_resultado = []
        empresa_ctx = _datos_empresa(request) if tickets_data else {}
        for t_data in tickets_data:
            ticket_id = t_data.get('ticket_id')
            if not ticket_id:
                continue

            existente = PosTicketHeader.objects.filter(ticket_id=ticket_id).first()
            if existente:
                synced_ids.append(ticket_id)
                # Reintento de sync: se devuelve de nuevo CUFE/PDF sin duplicar JSON ni transmisión.
                tickets_resultado.append(_procesar_tiquete_pos(request, existente, empresa_ctx, es_nuevo=False))
                continue

            id_tercero = t_data.get('id_tercero', '222222222222')
            puntos_ganados = int(t_data.get('puntos_ganados', 0))
            puntos_redimidos = int(t_data.get('puntos_redimidos_ticket', 0))

            header = PosTicketHeader.objects.create(
                ticket_id=ticket_id,
                turno=turno,
                id_tercero=id_tercero,
                nom_tercero=t_data.get('nom_tercero', 'VENTAS MASIVAS (CONSUMIDOR FINAL)'),
                fch_ticket=_parsear_fecha_ticket(t_data.get('fch_ticket')),
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

            # Normaliza base/IVA con la fórmula en reversa del servidor (la misma del JS, del CUFE y
            # del JSON DIAN); corrige también tickets encolados offline con la fórmula anterior.
            detalle = list(header.items.order_by('id'))
            calculo = _calcular_ticket(header, detalle)
            header.tot_mercancia = calculo['tot_base']
            header.tot_iva = calculo['tot_iva']
            header.save(update_fields=['tot_mercancia', 'tot_iva'])
            for det, c in zip(detalle, calculo['lineas']):
                if det.vlr_iva != c['iva']:
                    det.vlr_iva = c['iva']
                    det.save(update_fields=['vlr_iva'])

            # Actualizar Fidelización Puntos Cliente (Earn)
            if id_tercero != '222222222222' and puntos_ganados > 0:
                puntos_obj, _ = PosPuntosCliente.objects.get_or_create(id_tercero=id_tercero)
                puntos_obj.puntos_saldo += puntos_ganados
                puntos_obj.puntos_acumulados += puntos_ganados
                puntos_obj.save()

            synced_ids.append(ticket_id)
            tickets_resultado.append(_procesar_tiquete_pos(request, header, empresa_ctx, es_nuevo=True))

        return JsonResponse({
            'status': 'success',
            'synced_ids': synced_ids,
            'tickets': tickets_resultado,
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
        # Filtro de vigencia: fch_inicio <= now <= fch_fin (extremos nulos = sin límite)
        promos_qs = PrPromocion.objects.filter(
            (Q(fch_inicio__isnull=True) | Q(fch_inicio__lte=now)) &
            (Q(fch_fin__isnull=True) | Q(fch_fin__gte=now)),
            activo=True,
        ).order_by('-prioridad')
        
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
                # Motor v2
                'dias_semana': p.dias_semana,
                'monto_minimo': float(p.monto_minimo),
                'alcance': p.alcance,
                'alcance_valores': [v.strip() for v in (p.alcance_valores or '').split(',') if v.strip()],
                'tipo_beneficio': p.tipo_beneficio,
                'valor_beneficio': float(p.valor_beneficio),
                'cada_n_unidades': p.cada_n_unidades,
                # Compatibilidad con el motor anterior
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
