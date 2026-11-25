import hashlib
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
from core.tenant import get_oracle_connection

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


def _token_empresa_sesion(request):
    """Token de la empresa (tenant) en sesión; aísla los datos locales de SQLite (qcluster_db)."""
    return str(request.session.get('token_empresa') or '').strip()


def _clave_tenant(token, valor):
    """
    Llave primaria por tenant para tablas cuya PK es un código de negocio (PosTicketHeader.ticket_id,
    PosPuntosCliente.id_tercero): evita colisiones entre empresas ('CJ01-1' existe en todas).
    Sin token devuelve el valor tal cual (compatible con los datos anteriores).
    """
    valor = str(valor or '').strip()
    if not token or not valor:
        return valor
    return f"{hashlib.sha1(token.encode('utf-8')).hexdigest()[:8]}~{valor}"


def _id_sistema_sesion(request):
    """ID_SISTEMA de la empresa en sesión (north_configuraciones.id_sistema); '1' por defecto."""
    return str(request.session.get('id_sistema') or '1').strip() or '1'


def _caja_id_sesion(request, fallback='CAJA-01', enviado=None):
    """
    Caja activa del POS. Prioridad: request.session['caja_id'] (la que fijó la apertura de
    turno con TS_CAJAS.COD_CAJA), luego el valor enviado (body JSON o ?caja_id=) y por último
    el fallback.
    """
    caja = (
        request.session.get('caja_id')
        or enviado
        or request.GET.get('caja_id')
        or fallback
    )
    return str(caja).strip() or fallback


def _a_int(valor, default=0):
    """NUMBER de Oracle (int/Decimal/float/None) -> int."""
    try:
        return int(valor) if valor is not None and valor != '' else default
    except (TypeError, ValueError):
        return default


def _obtener_numeracion_caja(caja_id):
    """
    Resolución y consecutivo de la caja desde TS_CAJAS (Oracle del tenant activo).
    caja_id puede ser COD_CAJA, ID_CAJA o variantes ('CAJA-01'); ver _resolver_caja_ts.

    Retorna dict {id_caja, cod_caja, prefijo, num_resolucion, fch_vencimiento ('YYYY-MM-DD'|None),
    rango_inicial, rango_final, consecutivo_actual} o None si la caja no existe o Oracle falla
    (nunca lanza excepciones: el POS debe seguir operando offline).
    """
    caja_ref = str(caja_id or '').strip()
    if not caja_ref:
        return None
    try:
        from impresion.document_renderer import _resolver_caja_ts
        with get_oracle_connection().cursor() as cursor:
            cursor.execute(
                "SELECT ID_CAJA, COD_CAJA, DESCRIPCION, PREFIJO, NUM_RESOLUCION, FCH_VENCIMIENTO, "
                "RANGO_INICIAL, RANGO_FINAL, CONSECUTIVO_ACTUAL FROM TS_CAJAS"
            )
            fila = _resolver_caja_ts(caja_ref, cursor.fetchall())
        if not fila:
            logger.warning(f"POS: la caja '{caja_ref}' no corresponde a ninguna fila de TS_CAJAS.")
            return None
        id_caja, cod_caja, _desc, prefijo, num_res, fch_ven, rango_ini, rango_fin, consec = fila
        return {
            'id_caja': str(id_caja or '').strip(),
            'cod_caja': str(cod_caja or '').strip(),
            'prefijo': str(prefijo or '').strip(),
            'num_resolucion': str(num_res or '').strip(),
            'fch_vencimiento': fch_ven.strftime('%Y-%m-%d') if fch_ven else None,
            'rango_inicial': _a_int(rango_ini, 1),
            'rango_final': _a_int(rango_fin, 0),
            'consecutivo_actual': _a_int(consec, 0),
        }
    except Exception as exc:
        logger.warning(f"POS: no se pudo leer la numeración de TS_CAJAS para la caja '{caja_ref}': {exc}")
        return None


def _consecutivo_num_ticket(t_data, prefijo=''):
    """
    Entero de consecutivo del ticket: 'consecutivo_num' del payload (lo envía el frontend) o, como
    compatibilidad, el entero que sigue al prefijo de la caja en ticket_id (p. ej. 'CJ01-45' -> 45).
    Los ticket_id antiguos ('POS-<ts>-<n>') no coinciden y devuelven 0.
    """
    crudo = t_data.get('consecutivo_num')
    if crudo is not None and str(crudo).strip() != '':
        try:
            return max(0, int(crudo))
        except (TypeError, ValueError):
            logger.warning(f"POS: consecutivo_num inválido en el ticket {t_data.get('ticket_id')}: {crudo!r}")
            return 0
    ticket_id = str(t_data.get('ticket_id') or '').strip()
    prefijo = (prefijo or '').strip()
    if prefijo and ticket_id.upper().startswith(prefijo.upper()):
        resto = ticket_id[len(prefijo):]
        if resto.isdigit():
            return int(resto)
    return 0


def _actualizar_consecutivo_caja(id_caja, consecutivo_num):
    """
    TS_CAJAS.CONSECUTIVO_ACTUAL = GREATEST(actual, consecutivo_num) (nunca retrocede). Operación
    idempotente; si Oracle no responde solo registra un warning (el ticket local ya está guardado).
    Retorna True si actualizó una fila.
    """
    if not id_caja or not consecutivo_num or consecutivo_num <= 0:
        return False
    try:
        conn = get_oracle_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE TS_CAJAS SET CONSECUTIVO_ACTUAL = GREATEST(NVL(CONSECUTIVO_ACTUAL, 0), %s) "
                "WHERE TRIM(ID_CAJA) = %s",
                [int(consecutivo_num), str(id_caja).strip()],
            )
            actualizadas = cursor.rowcount
        if not conn.get_autocommit() and not conn.in_atomic_block:
            conn.commit()
        return bool(actualizadas)
    except Exception as exc:
        logger.warning(
            f"POS: no se pudo actualizar TS_CAJAS.CONSECUTIVO_ACTUAL (caja {id_caja}, "
            f"consecutivo {consecutivo_num}): {exc}"
        )
        return False


def _datos_empresa(request):
    """
    Datos de la empresa para el encabezado/CUFE. Nunca lanza excepciones.
    Usa _fetch_empresa_data (TRIM(ID_SISTEMA) con fallback a '1' y a la primera fila de SG_SISTEMAS),
    de modo que NIT, dirección y responsabilidades tributarias no se pierdan por un id de sesión
    que no coincide.
    """
    id_sistema = _id_sistema_sesion(request)
    datos = {
        'nom_sistema': request.session.get('nom_empresa') or '',
        'nit_emp': '', 'dir_emp': '', 'municipio_emp': '', 'tels_emp': '', 'contribuyente': '',
    }
    try:
        from impresion.document_renderer import _fetch_empresa_data
        with get_oracle_connection().cursor() as cursor:
            emp = _fetch_empresa_data(cursor, id_sistema)
        datos['nom_sistema'] = emp.get('nom_empresa') or datos['nom_sistema']
        datos['nit_emp'] = str(emp.get('nit') or '')
        datos['dir_emp'] = emp.get('dir') or ''
        datos['contribuyente'] = emp.get('obser1') or ''
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


def _r0(valor):
    """Redondeo half-up al peso entero (idéntico a Math.round de templates/pos/pos.html)."""
    return Decimal(valor).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def calcular_ticket_en_reversa(lineas, descuento_global=0):
    """
    Cálculo en reversa de un ticket POS cuyos precios YA INCLUYEN IVA e INC.
    Misma fórmula que recalcTaxes() de templates/pos/pos.html, de modo que
    Caja (JS) == PDF == CUFE == JSON Binapps.

    lineas: iterable de dicts con cantidad, vlr_unitario (con impuestos), porc_descuento,
            descuento_valor (COP exacto de la línea; opcional), porc_iva, porc_inc y
            vlr_inc_fijo_unit (los tres últimos opcionales).
    Descuentos:
      - Si alguna línea trae descuento_valor > 0, cada línea recibe EXACTAMENTE el suyo (topado a su bruto)
        y se calcula su propio IVA/INC; el descuento_global se ignora (no se prorratea a líneas con 0).
      - Solo si NINGUNA línea trae descuento_valor se prorratea descuento_global entre las líneas (respaldo).
    Retorna {'lineas': [...], 'tot_bruto', 'tot_descuento', 'tot_base', 'tot_iva', 'tot_inc',
             'tot_pagar'} con Decimals a 2 decimales. Por línea: bruto, descuento, total_pagar,
    base_neta, iva, inc, vlr_unit_neto; siempre base_neta + iva + inc == total_pagar.
    """
    lineas = list(lineas)
    hay_desc_item = any(_r0(ln.get('descuento_valor') or 0) > 0 for ln in lineas)
    pre = []
    for ln in lineas:
        # Bruto y descuento de línea al peso entero: coincide peso a peso con el POS (cantidades fraccionadas)
        bruto = _r0(Decimal(ln['cantidad']) * Decimal(ln['vlr_unitario']))
        propio = _r0(bruto * Decimal(ln.get('porc_descuento') or 0) / Decimal('100'))
        propio = min(bruto, _r0(propio + max(CERO, _r0(ln.get('descuento_valor') or 0))))
        pre.append((bruto, propio, max(CERO, _r2(bruto - propio))))

    suma_neto = _r2(sum((p[2] for p in pre), CERO))
    global_dto = CERO if hay_desc_item else min(max(_r2(descuento_global or 0), CERO), suma_neto)
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
        with get_oracle_connection().cursor() as cursor:
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
        'descuento_valor': it.descuento_valor,
        'porc_iva': it.porc_iva, 'porc_inc': CERO,
        'vlr_inc_fijo_unit': inc_fijo.get(str(it.id_articulo), CERO),
    } for it in items]
    calculo = calcular_ticket_en_reversa(lineas, header.descuento_promocion)
    if abs(calculo['tot_pagar'] - header.tot_ticket) > CENT:
        logger.warning(
            f"POS {header.numero_ticket}: total recalculado {calculo['tot_pagar']} != tot_ticket grabado {header.tot_ticket}"
        )
    return calculo


def _armar_datos_dian(header, items, prefijo, empresa, calculo, tercero=None, emisor=None, atendido_por=''):
    """
    Construye (header_data, items_data, taxes_data) con las llaves de InvoicePayloadBuilder.
    VLR_UNITARIO es el valor unitario NETO sin impuestos (base_neta / cantidad), por lo que
    CANTIDAD * VLR_UNITARIO = base_neta y TaxExclusive + IVA + INC = PayableAmount.
    `tercero` (obtener_tercero_pos) aporta los datos reales de CO_TERCEROS para CustomerInformation.
    `emisor` (obtener_datos_emisor_pos) aporta prefijo y resolución DIAN de TS_CAJAS y las
    responsabilidades tributarias (contribuyente -> Note2). `atendido_por` es el cajero
    (VENDEDOR_NOMBRE_LARGO -> SalesPerson / SalespersonName).
    """
    tercero = tercero or {}
    emisor = emisor or {}
    nit_adq = tercero.get('nit') or header.id_tercero or CONSUMIDOR_FINAL
    razon_social = tercero.get('razon_social') or header.nom_tercero
    fecha = _fecha_bogota(header.fch_ticket)
    id_safe = re.sub(r'[^A-Za-z0-9_-]', '_', header.numero_ticket)
    header_data = {
        'ID_DOCUMENTO': id_safe,
        'NUM_DOCUMENTO': header.numero_ticket,
        'FCH_DOCUMENTO': fecha.strftime('%Y-%m-%dT%H:%M:%S'),
        'TOT_DOCUMENTO': float(calculo['tot_pagar']),
        'TOT_MERCANCIA': float(calculo['tot_base']),
        'PLAZO_PAGO': 0,
        'REGIMEN': tercero.get('regimen') or '2',
        'PREFIJO': str(emisor.get('prefijo') or prefijo or '').strip(),
        'NUM_RESOLUCION': str(emisor.get('num_resolucion') or '').strip(),
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
        'VENDEDOR_NOMBRE_LARGO': str(atendido_por or '').strip(),
        'NOTE_2': str(emisor.get('contribuyente') or '').strip(),
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


def _texto_tef(valor, max_len=None):
    """Valor opcional del JSON del ticket -> str ('' si falta). dict/list se serializan como JSON."""
    if valor is None:
        return ''
    if isinstance(valor, (dict, list)):
        valor = json.dumps(valor, ensure_ascii=False)
    texto = str(valor).strip()
    return texto[:max_len] if max_len else texto


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
            # Solo líneas con descuento real imprimen "Descuento (-)"; el resto va en 0 (la plantilla no lo muestra)
            'descuento': c['descuento'] if c['descuento'] > 0 else CERO,
            'porc_iva': it.porc_iva or CERO,
            'grupo_compuesto': (it.grupo_compuesto or '').strip(),
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
        'num_documento': header.numero_ticket,
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


def _binapps_tenant_id():
    """
    Tenant ID de Binapps, resuelto en el hilo HTTP (donde sí hay tenant Oracle activo) para que el
    worker de Django-Q no consulte Oracle. SG_PARAMETROS.BAPP_TEN -> BINAPPS_TENANT_ID -> '1'.
    """
    try:
        from services.legacy_repository import get_system_parameter
        valor = str(get_system_parameter('BAPP_TEN', '') or '').strip()
    except Exception as exc:
        logger.warning(f"POS: no se pudo leer BAPP_TEN de Oracle: {exc}")
        valor = ''
    return valor or os.getenv('BINAPPS_TENANT_ID') or '1'


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
        'ticket_id': header.numero_ticket, 'cufe': '', 'pdf_nombre': None, 'pdf_ruta': None,
        'estado_dian': 'PENDIENTE_CIERRE_Z', 'payload_guardado': False,
    }
    items = []
    try:
        header.refresh_from_db()
        items = list(header.items.order_by('id'))
        calculo = _calcular_ticket(header, items)
    except Exception as exc:
        logger.error(f"POS {header.numero_ticket}: no se pudo leer el tiquete grabado: {exc}")
        resultado['estado_dian'] = 'ERROR_LECTURA'
        return resultado

    # Adquirente real desde CO_TERCEROS (NIT/ID del ticket; respaldo 222222222222). No lanza excepciones.
    from impresion.document_renderer import obtener_tercero_pos
    tercero = obtener_tercero_pos(header.id_tercero or CONSUMIDOR_FINAL)

    # Emisor, resolución DIAN, prefijo y clave técnica de la caja (TS_CAJAS) + SG_SISTEMAS, una sola vez
    # y ANTES del CUFE y del JSON. obtener_datos_emisor_pos nunca lanza excepciones.
    emisor = {}
    try:
        from impresion.document_renderer import obtener_datos_emisor_pos
        caja_id = header.turno.caja_id if header.turno_id else ''
        emisor = obtener_datos_emisor_pos(caja_id, id_sistema=_id_sistema_sesion(request)) or {}
    except Exception as exc:
        logger.error(f"POS {header.numero_ticket}: no se pudieron leer los datos del emisor: {exc}")

    # 1. CUFE local (SHA-384). Siempre disponible, sin red.
    try:
        from facturacion.dian_async import calcular_cufe_local, get_dian_cufe_config
        cfg = get_dian_cufe_config()
        fecha = _fecha_bogota(header.fch_ticket)
        resultado['cufe'] = calcular_cufe_local(
            num_fac=header.numero_ticket,
            fecha=fecha.strftime('%Y-%m-%d'),
            hora=fecha.strftime('%H:%M:%S-05:00'),
            val_fac=calculo['tot_base'], val_iva=calculo['tot_iva'], val_inc=calculo['tot_inc'], val_ica=0,
            val_tot=calculo['tot_pagar'],
            nit_ofe=_nit_sin_dv(empresa.get('nit_emp')),
            num_adq=tercero.get('nit') or header.id_tercero or CONSUMIDOR_FINAL,
            # Clave técnica de la caja (TS_CAJAS.CLAVE_TECNICA); DIAN_CLAVE_TECNICA del .env solo como respaldo.
            clave_tecnica=emisor.get('clave_tecnica') or cfg['clave_tecnica'],
            tipo_ambiente=cfg['tipo_ambiente'],
        )
    except Exception as exc:
        logger.error(f"POS {header.numero_ticket}: error calculando CUFE local: {exc}")

    # 2. JSON real (dry_run=False) escrito en logs/fel_payloads + 3. encolado DIAN
    if es_nuevo:
        payload = None
        try:
            from services.payload_builder import InvoicePayloadBuilder
            header_data, items_data, taxes_data = _armar_datos_dian(
                header, items, prefijo, empresa, calculo, tercero, emisor=emisor,
                atendido_por=_nombre_atendido_por(request, header),
            )
            payload = InvoicePayloadBuilder.build_invoice_payload(header_data, items_data, taxes_data)
            resultado['payload_guardado'] = True
        except Exception as exc:
            logger.error(f"POS {header.numero_ticket}: no se pudo generar el JSON DIAN: {exc}")
            resultado['estado_dian'] = 'ERROR_PAYLOAD'

        # La transmisión por tiquete es opt-in: el Cierre Z ya consolida y transmite
        # un documento maestro (ver pos_consolidator); transmitir ambos duplicaría la venta.
        if payload is not None and getattr(settings, 'POS_DIAN_TRANSMIT_ON_SYNC',
                                           os.getenv('POS_DIAN_TRANSMIT_ON_SYNC', '').lower() in ('1', 'true', 'yes')):
            try:
                from facturacion.dian_async import encolar_transmision_pos
                # El tenant de Binapps se resuelve aquí (hilo HTTP con tenant Oracle activo), no en el worker.
                binapps_tenant_id = _binapps_tenant_id()
                resultado['task_id'] = str(
                    encolar_transmision_pos(header.numero_ticket, payload, tenant_id=binapps_tenant_id)
                )
                resultado['estado_dian'] = 'ENCOLADO'
            except Exception as exc:
                logger.error(f"POS {header.numero_ticket}: no se pudo encolar la transmisión DIAN: {exc}")
                resultado['estado_dian'] = 'PENDIENTE_CIERRE_Z'

    # 4. PDF térmico
    try:
        from impresion.document_renderer import render_pos_ticket_to_pdf
        # Emisor (ya leído arriba): resolución DIAN y responsabilidades tributarias, misma fuente Oracle que la factura masiva.
        empresa_pdf = {**empresa, **{k: v for k, v in emisor.items() if v}}
        ticket_data = _armar_ticket_data(
            header, items, resultado['cufe'], empresa_pdf, resultado['estado_dian'], calculo,
            atendido_por=_nombre_atendido_por(request, header), tercero=tercero,
        )
        pdf_path = render_pos_ticket_to_pdf(ticket_data, prefijo_numeracion=prefijo)
        resultado['pdf_nombre'] = os.path.basename(pdf_path)
        resultado['pdf_ruta'] = _ruta_relativa_pdf(pdf_path)
    except Exception as exc:
        logger.error(f"POS {header.numero_ticket}: no se pudo generar el PDF térmico: {exc}")

    return resultado


@pos_role_required(*ROLES_APERTURA)
def pos_home(request):
    """Renderiza la pantalla principal del Punto de Venta (POS)."""
    turno_abierto = PosTurno.objects.filter(
        estado='ABIERTO', token_empresa=_token_empresa_sesion(request)
    ).order_by('-id_turno').first()
    context = {
        'turno_abierto': turno_abierto
    }
    return render(request, 'pos/pos.html', context)


def _cargar_recetas_compuestos():
    """
    {id_articulo_terminado: [{'id_articulo': mat_prima, 'cantidad': float}]} desde
    IN_TRANSF_COMPONENTE_ITEMS. Si la tabla no existe en el esquema retorna {} (el POS vende el padre).
    """
    recetas = {}
    try:
        with get_oracle_connection().cursor() as cursor:
            cursor.execute(
                "SELECT TRIM(ID_ARTICULO_TERMINADO), TRIM(ID_ARTICULO_MAT_PRIMA), CANTIDAD "
                "FROM IN_TRANSF_COMPONENTE_ITEMS"
            )
            for terminado, mat_prima, cantidad in cursor.fetchall():
                if not terminado or not mat_prima:
                    continue
                recetas.setdefault(str(terminado).strip(), []).append(
                    {'id_articulo': str(mat_prima).strip(), 'cantidad': float(cantidad or 0)}
                )
    except Exception as exc:
        logger.warning(f"POS: no se pudieron leer recetas de IN_TRANSF_COMPONENTE_ITEMS: {exc}")
        return {}
    return recetas


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
        a.ID_LINEA,
        NVL(TRIM(a.SIONO_SERIE), 'N') AS SIONO_SERIE,
        NVL(TRIM(a.SIONO_TRANSFORMACION), 'N') AS SIONO_TRANSFORMACION
    FROM IN_ARTICULOS a
    LEFT JOIN IN_GRAVAMENES g ON a.ID_GRAVAMEN = g.ID_GRAVAMEN
    WHERE NVL(a.ACTIVO_INACTIVO, 'A') = 'A'
    """
    try:
        with get_oracle_connection().cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
            columns = [col[0].lower() for col in cursor.description]
            catalog = [dict(zip(columns, row)) for row in rows]

        recetas = _cargar_recetas_compuestos()
        for item in catalog:
            es_comp = (str(item.pop('siono_transformacion', 'N') or 'N').strip().upper() == 'S'
                       or str(item.pop('siono_serie', 'N') or 'N').strip().upper() == 'S')
            item['es_compuesto'] = bool(es_comp)
            if es_comp:
                item['componentes'] = recetas.get(str(item['id_articulo']).strip(), [])

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
    Buscador rápido de clientes en CO_TERCEROS (solo por NIT/identificación o por nombre).
    COD_TERCERO es el NIT/cédula; ID_TERCERO es el código interno y NO participa en la búsqueda
    (se devuelve únicamente porque el tiquete se graba con él).
    Utiliza ROWNUM <= 30 para estricta compatibilidad con Oracle 11g.
    """
    q = request.GET.get('q', '').strip().upper()
    if not q:
        query = """
        SELECT * FROM (
            SELECT ID_TERCERO, NOM_TERCERO, COD_TERCERO FROM CO_TERCEROS ORDER BY NOM_TERCERO
        ) WHERE ROWNUM <= 30
        """
        params = []
    else:
        query = """
        SELECT * FROM (
            SELECT ID_TERCERO, NOM_TERCERO, COD_TERCERO FROM CO_TERCEROS
            WHERE UPPER(COD_TERCERO) LIKE %s OR UPPER(NOM_TERCERO) LIKE %s
            ORDER BY NOM_TERCERO
        ) WHERE ROWNUM <= 30
        """
        wildcard = f"%{q}%"
        params = [wildcard, wildcard]

    try:
        with get_oracle_connection().cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            terceros = [
                {
                    'id_tercero': row[0],
                    'nom_tercero': row[1],
                    'cod_tercero': (str(row[2]).strip() if row[2] is not None else '') or str(row[0]),
                }
                for row in rows
            ]

        return JsonResponse({'status': 'success', 'terceros': terceros})
    except Exception as e:
        logger.error(f"Error en buscador de terceros POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


def _pdf_tiquete_response(request):
    """
    GET ?pdf=1&ticket_id=<id>: devuelve el PDF térmico de un tiquete ya sincronizado de la empresa en
    sesión (reimpresión). Regenera CUFE/PDF con _procesar_tiquete_pos(es_nuevo=False): no duplica JSON ni DIAN.
    """
    from django.http import FileResponse
    ticket_id = str(request.GET.get('ticket_id', '')).strip()
    if not ticket_id:
        return JsonResponse({'status': 'error', 'message': 'Falta ticket_id.'}, status=400)
    header = PosTicketHeader.objects.filter(
        ticket_id=_clave_tenant(_token_empresa_sesion(request), ticket_id),
        token_empresa=_token_empresa_sesion(request),
    ).first()
    if not header:
        return JsonResponse({
            'status': 'error',
            'message': 'El tiquete aún no está sincronizado con el servidor; intente de nuevo en unos segundos.'
        }, status=404)

    resultado = _procesar_tiquete_pos(request, header, _datos_empresa(request), es_nuevo=False)
    ruta = resultado.get('pdf_ruta')
    if not ruta:
        return JsonResponse({'status': 'error', 'message': 'No se pudo generar el PDF del tiquete.'}, status=500)

    from impresion.document_renderer import _get_facturas_base_dir
    base_media = Path(str(getattr(settings, 'MEDIA_ROOT', '') or '').strip() or str(settings.BASE_DIR)).resolve()
    pdf_path = (base_media / ruta).resolve()
    try:
        pdf_path.relative_to(Path(_get_facturas_base_dir()).resolve())   # solo archivos bajo la carpeta de facturas
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'Ruta de PDF no permitida.'}, status=403)
    if not pdf_path.is_file():
        return JsonResponse({'status': 'error', 'message': 'El PDF del tiquete no se encuentra en el servidor.'}, status=404)
    response = FileResponse(open(pdf_path, 'rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{pdf_path.name}"'
    return response


@csrf_exempt
@pos_role_required(*ROLES_APERTURA)
def api_shift_open(request):
    """Apertura de Caja: Exige ingresar la Base Económica inicial antes de facturar."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        caja_id = _caja_id_sesion(request, enviado=data.get('caja_id'))
        # El usuario sale de la sesión POS; el body nunca define quién abre el turno.
        usuario = (
            (request.session.get('pos_user') or {}).get('email')
            or request.session.get('id_usuario')
            or (request.user.username if request.user.is_authenticated else None)
            or 'cajero'
        )
        usuario = str(usuario)
        base_economica = Decimal(str(data.get('base_economica', 0)))

        token = _token_empresa_sesion(request)
        turno_existente = PosTurno.objects.filter(
            caja_id=caja_id, estado='ABIERTO', token_empresa=token
        ).first()
        if turno_existente:
            return JsonResponse({
                'status': 'error',
                'message': f'La caja {caja_id} ya tiene un turno abierto (#{turno_existente.id_turno}).'
            }, status=400)

        turno = PosTurno.objects.create(
            token_empresa=token,
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
            },
            'caja_id': turno.caja_id,
            'numeracion': _obtener_numeracion_caja(turno.caja_id),
        })
    except Exception as e:
        logger.error(f"Error en Apertura de Caja POS: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


def api_shift_status(request):
    """Consulta el estado del turno actual de caja."""
    caja_id = _caja_id_sesion(request)
    turno = PosTurno.objects.filter(
        caja_id=caja_id, estado='ABIERTO', token_empresa=_token_empresa_sesion(request)
    ).order_by('-id_turno').first()
    if not turno:
        return JsonResponse({
            'status': 'success',
            'turno_abierto': False,
            'caja_id': caja_id,
            'numeracion': _obtener_numeracion_caja(caja_id),
        })

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
        },
        'caja_id': turno.caja_id,
        'numeracion': _obtener_numeracion_caja(turno.caja_id),
    })


@csrf_exempt
def api_sync_tickets(request):
    """
    Endpoint para recibir y almacenar los tickets sincronizados desde IndexedDB.
    Acredita puntos acumulados al saldo del cliente en PosPuntosCliente.
    GET con ?pdf=1&ticket_id=... sirve el PDF del tiquete para reimprimirlo.
    """
    if request.method == 'GET' and request.GET.get('pdf'):
        try:
            return _pdf_tiquete_response(request)
        except Exception as e:
            logger.error(f"Error al servir PDF de tiquete POS: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        payload = json.loads(request.body)
        tickets_data = payload.get('tickets', [])
        caja_id = _caja_id_sesion(request, enviado=payload.get('caja_id'))
        token = _token_empresa_sesion(request)

        turno = PosTurno.objects.filter(
            caja_id=caja_id, estado='ABIERTO', token_empresa=token
        ).order_by('-id_turno').first()
        if not turno:
            return JsonResponse({
                'status': 'error',
                'message': 'No hay un turno abierto para asociar los tickets sincronizados.'
            }, status=400)

        synced_ids = []
        tickets_resultado = []
        errores_tickets = []
        empresa_ctx = _datos_empresa(request) if tickets_data else {}
        # Numeración de la caja del turno (una sola lectura de TS_CAJAS por sincronización).
        numeracion = _obtener_numeracion_caja(turno.caja_id) if tickets_data else None
        id_caja_ts = (numeracion or {}).get('id_caja', '')
        prefijo_caja = (numeracion or {}).get('prefijo', '')
        for t_data in tickets_data:
            ticket_id = t_data.get('ticket_id')
            if not ticket_id:
                continue
            ticket_id = str(ticket_id).strip()
            # Llave interna única por empresa: dos tenants pueden emitir el mismo 'CJ01-1'.
            ticket_pk = _clave_tenant(token, ticket_id)

            existente = PosTicketHeader.objects.filter(ticket_id=ticket_pk).first()
            if existente and existente.token_empresa != token:
                # Defensa: la llave ya pertenece a OTRA empresa; nunca se trata como reintento idempotente.
                logger.error(
                    f"POS: ticket {ticket_id} rechazado: la llave ya existe bajo otro token_empresa "
                    f"(token actual '{token[:6]}...')."
                )
                errores_tickets.append({
                    'ticket_id': ticket_id,
                    'error': 'COLISION_TICKET_ID',
                    'message': f'El ticket {ticket_id} ya existe para otra empresa.',
                })
                continue
            if existente:
                synced_ids.append(ticket_id)
                # Idempotente: reafirma el consecutivo por si el UPDATE de un intento previo falló.
                _actualizar_consecutivo_caja(id_caja_ts, _consecutivo_num_ticket(t_data, prefijo_caja))
                # Reintento de sync: se devuelve de nuevo CUFE/PDF sin duplicar JSON ni transmisión.
                tickets_resultado.append(_procesar_tiquete_pos(request, existente, empresa_ctx, es_nuevo=False))
                continue

            id_tercero = t_data.get('id_tercero', '222222222222')
            puntos_ganados = int(t_data.get('puntos_ganados', 0))
            puntos_redimidos = int(t_data.get('puntos_redimidos_ticket', 0))

            header = PosTicketHeader.objects.create(
                ticket_id=ticket_pk,
                token_empresa=token,
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
                tef_aprobacion=_texto_tef(t_data.get('tef_aprobacion'), 50),
                tef_franquicia=_texto_tef(t_data.get('tef_franquicia'), 50),
                tef_ultimos4=_texto_tef(t_data.get('tef_ultimos4'), 10),
                tef_recibo=_texto_tef(t_data.get('tef_recibo'), 50),
                tef_respuesta_raw=_texto_tef(t_data.get('tef_respuesta_raw')),
                sync_status=True,
                consolidado_cierre=False
            )

            for item_data in t_data.get('items', []):
                PosTicketDetail.objects.create(
                    ticket=header,
                    token_empresa=token,
                    id_articulo=item_data.get('id_articulo'),
                    referencia=item_data.get('referencia', ''),
                    nom_articulo=item_data.get('nom_articulo', ''),
                    cantidad=Decimal(str(item_data.get('cantidad', 1))),
                    vlr_unitario=Decimal(str(item_data.get('vlr_unitario', 0))),
                    porc_descuento=Decimal(str(item_data.get('porc_descuento', 0))),
                    descuento_valor=Decimal(str(item_data.get('descuento_valor') or 0)),
                    porc_iva=Decimal(str(item_data.get('porc_iva', 0))),
                    vlr_iva=Decimal(str(item_data.get('vlr_iva', 0))),
                    tot_linea=Decimal(str(item_data.get('tot_linea', 0))),
                    grupo_compuesto=_texto_tef(item_data.get('grupo_compuesto'), 120),
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
                puntos_obj, _ = PosPuntosCliente.objects.get_or_create(
                    id_tercero=_clave_tenant(token, id_tercero), token_empresa=token
                )
                puntos_obj.puntos_saldo += puntos_ganados
                puntos_obj.puntos_acumulados += puntos_ganados
                puntos_obj.save()

            # Consecutivo por caja: TS_CAJAS.CONSECUTIVO_ACTUAL nunca retrocede (GREATEST).
            # Si Oracle falla solo se registra warning; el ticket local ya quedó guardado.
            _actualizar_consecutivo_caja(id_caja_ts, _consecutivo_num_ticket(t_data, prefijo_caja))

            synced_ids.append(ticket_id)
            tickets_resultado.append(_procesar_tiquete_pos(request, header, empresa_ctx, es_nuevo=True))

        return JsonResponse({
            'status': 'success',
            'synced_ids': synced_ids,
            'tickets': tickets_resultado,
            'errores': errores_tickets,
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
        caja_id = _caja_id_sesion(request, enviado=data.get('caja_id'))

        turno = PosTurno.objects.filter(
            caja_id=caja_id, estado='ABIERTO', token_empresa=_token_empresa_sesion(request)
        ).order_by('-id_turno').first()
        if not turno:
            return JsonResponse({'status': 'error', 'message': 'No hay un turno abierto para cerrar.'}, status=400)

        res = consolidar_cierre_z(turno.id_turno, id_sistema=_id_sistema_sesion(request))
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
            Q(token_empresa=_token_empresa_sesion(request)) &
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

    token = _token_empresa_sesion(request)
    try:
        puntos_obj, _ = PosPuntosCliente.objects.get_or_create(
            id_tercero=_clave_tenant(token, id_tercero), token_empresa=token
        )
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

        token = _token_empresa_sesion(request)
        puntos_obj = PosPuntosCliente.objects.filter(
            id_tercero=_clave_tenant(token, id_tercero), token_empresa=token
        ).first()
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
