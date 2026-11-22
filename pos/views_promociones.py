import logging
from datetime import datetime, time
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from core.tenant import get_oracle_connection
from pos.models import PrAccion, PrCondicion, PrPromocion


logger = logging.getLogger(__name__)

DIAS_SEMANA = [
    ('0', 'Lunes'), ('1', 'Martes'), ('2', 'Miércoles'), ('3', 'Jueves'),
    ('4', 'Viernes'), ('5', 'Sábado'), ('6', 'Domingo'),
]


class PromocionListView(ListView):
    """Listado de promociones configuradas (PR_PROMOCION)."""
    model = PrPromocion
    template_name = 'pos/promociones/lista.html'
    context_object_name = 'promociones'

    def get_queryset(self):
        # Mismo criterio de prioridad que api_promociones (mayor prioridad primero).
        return (
            PrPromocion.objects
            .prefetch_related('condiciones', 'acciones')
            .order_by('-activo', '-prioridad', '-fch_inicio')
        )


# ==========================================
# CRUD (crear / editar / activar / eliminar)
# ==========================================

def _parse_fecha(valor, fin_de_dia=False):
    """
    Acepta 'YYYY-MM-DDTHH:MM[:SS]', 'YYYY-MM-DD HH:MM[:SS]' o 'YYYY-MM-DD'.
    Retorna datetime (naive o aware según USE_TZ) o None si viene vacío.
    Lanza ValueError si el texto no es una fecha válida.
    """
    texto = (valor or '').strip()
    if not texto:
        return None
    d = parse_date(texto) if len(texto) == 10 else None
    if d is not None:
        # Solo fecha: inicio = 00:00:00, fin = 23:59:59 (vigencia inclusiva todo el día).
        dt = datetime.combine(d, time(23, 59, 59) if fin_de_dia else time.min)
    else:
        dt = parse_datetime(texto)
        if dt is None:
            raise ValueError(texto)
    # Coherente con settings.USE_TZ (hoy False => datetimes naive en hora local de Bogotá).
    if getattr(settings, 'USE_TZ', False):
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
    elif timezone.is_aware(dt):
        dt = timezone.make_naive(dt)
    return dt


def _to_decimal(valor, default=None):
    texto = str(valor if valor is not None else '').strip().replace(',', '.')
    if texto == '':
        return default
    try:
        return Decimal(texto)
    except InvalidOperation:
        raise ValueError(texto)


def _fmt_input_dt(dt):
    """Formato compatible con <input type="datetime-local">."""
    return dt.strftime('%Y-%m-%dT%H:%M') if dt else ''


def _choices(choices):
    return [{'value': v, 'label': l} for v, l in choices]


def _filas_desde_post(post):
    """Une las listas paralelas del POST en filas (sin descartar nada; se valida después)."""
    tipos = post.getlist('cond_tipo')
    valores = post.getlist('cond_valor')
    cantidades = post.getlist('cond_cantidad')
    condiciones = []
    for i, tipo in enumerate(tipos):
        condiciones.append({
            'tipo_condicion': (tipo or '').strip(),
            'valor_condicion': (valores[i] if i < len(valores) else '').strip(),
            'cantidad_minima': (cantidades[i] if i < len(cantidades) else '').strip(),
        })

    tipos = post.getlist('acc_tipo')
    valores = post.getlist('acc_valor')
    regalos = post.getlist('acc_regalo')
    acciones = []
    for i, tipo in enumerate(tipos):
        acciones.append({
            'tipo_accion': (tipo or '').strip(),
            'valor_accion': (valores[i] if i < len(valores) else '').strip(),
            'id_articulo_regalo': (regalos[i] if i < len(regalos) else '').strip(),
        })
    return condiciones, acciones


def _validar_motor_v2(post, errores):
    """Valida los campos del motor v2 de PrPromocion. Agrega mensajes a `errores`; retorna los valores normalizados."""
    # Días de la semana (0=Lunes ... 6=Domingo). Vacío = todos los días.
    dias = []
    for d in post.getlist('dias_semana'):
        for parte in str(d).split(','):
            parte = parte.strip()
            if not parte:
                continue
            if parte not in {v for v, _ in DIAS_SEMANA}:
                errores.append(f'Día de la semana no válido: {parte}.')
            elif parte not in dias:
                dias.append(parte)
    dias.sort(key=int)

    try:
        monto_minimo = _to_decimal(post.get('monto_minimo'), default=Decimal('0'))
    except ValueError:
        monto_minimo = Decimal('0')
        errores.append('El monto mínimo no es un número válido.')
    if monto_minimo < 0:
        errores.append('El monto mínimo no puede ser negativo.')

    alcance = (post.get('alcance') or 'TODA_TIENDA').strip()
    if alcance not in {v for v, _ in PrPromocion.ALCANCE_CHOICES}:
        errores.append('El alcance no es válido.')
        alcance = 'TODA_TIENDA'
    valores = []
    for v in post.getlist('alcance_valores'):
        for parte in str(v).split(','):
            parte = parte.strip()
            if parte and parte not in valores:
                valores.append(parte)
    if alcance == 'TODA_TIENDA':
        valores = []
    else:
        if not valores:
            errores.append('Debe seleccionar al menos un valor para el alcance elegido.')
        if alcance == 'PRODUCTO' and len(valores) > 1:
            errores.append('El alcance «Un producto» admite un solo artículo.')
    if len(','.join(valores)) > 20000:
        errores.append('La selección de alcance es demasiado extensa.')

    tipo = (post.get('tipo_beneficio') or '').strip()
    if tipo not in {v for v, _ in PrPromocion.TIPO_BENEFICIO_CHOICES}:
        errores.append('El tipo de beneficio no es válido.')
        tipo = 'DESCUENTO_PORC'
    try:
        valor = _to_decimal(post.get('valor_beneficio'), default=Decimal('0'))
    except ValueError:
        valor = Decimal('0')
        errores.append('El valor del beneficio no es un número válido.')
    if valor <= 0:
        errores.append('El valor del beneficio debe ser mayor que cero.')
    elif tipo in ('DESCUENTO_PORC', 'N_ESIMA_UNIDAD') and valor > 100:
        errores.append('El porcentaje del beneficio no puede superar 100.')

    try:
        cada_n = int((post.get('cada_n_unidades') or '2').strip() or 2)
    except ValueError:
        cada_n = 2
        errores.append('«Cada N unidades» debe ser un número entero.')
    if tipo == 'N_ESIMA_UNIDAD' and cada_n < 2:
        errores.append('«Cada N unidades» debe ser 2 o más.')
    if cada_n < 0 or cada_n > 999:
        errores.append('«Cada N unidades» debe estar entre 2 y 999.')
        cada_n = 2

    return {
        'dias_semana': ','.join(dias),
        'monto_minimo': monto_minimo,
        'alcance': alcance,
        'alcance_valores': ','.join(valores),
        'tipo_beneficio': tipo,
        'valor_beneficio': valor,
        'cada_n_unidades': cada_n,
    }


def _validar_y_normalizar(post):
    """
    Valida el POST. Retorna (cabecera, condiciones, acciones, errores).
    - cabecera: dict con nom_promocion, fch_inicio, fch_fin, prioridad, activo.
    - condiciones / acciones: listas de dicts ya tipadas (Decimal) y listas para guardar.
    - errores: lista de textos (vacía si todo es válido).
    """
    errores = []
    tipos_cond = {v for v, _ in PrCondicion.TIPO_CONDICION_CHOICES}
    tipos_acc = {v for v, _ in PrAccion.TIPO_ACCION_CHOICES}

    nombre = (post.get('nombre') or '').strip()
    if not nombre:
        errores.append('El nombre de la promoción es obligatorio.')
    elif len(nombre) > 150:
        errores.append('El nombre no puede superar 150 caracteres.')

    fch_inicio = fch_fin = None
    try:
        fch_inicio = _parse_fecha(post.get('fch_inicio'))
    except ValueError:
        errores.append('La fecha de inicio no es válida.')
    try:
        fch_fin = _parse_fecha(post.get('fch_fin'), fin_de_dia=True)
    except ValueError:
        errores.append('La fecha de fin no es válida.')
    if fch_inicio and fch_fin and fch_inicio > fch_fin:
        errores.append('La fecha de inicio no puede ser posterior a la fecha de fin.')
    # PR_PROMOCION.fch_inicio / fch_fin son NOT NULL en el modelo.
    if not fch_inicio and not any('inicio' in e for e in errores):
        errores.append('La fecha de inicio es obligatoria.')
    if not fch_fin and not any('fin' in e for e in errores):
        errores.append('La fecha de fin es obligatoria.')

    try:
        prioridad = int((post.get('prioridad') or '1').strip() or 1)
    except ValueError:
        prioridad = 1
        errores.append('La prioridad debe ser un número entero.')

    cabecera = {
        'nom_promocion': nombre,
        'fch_inicio': fch_inicio,
        'fch_fin': fch_fin,
        'prioridad': prioridad,
        'activo': post.get('activo') in ('on', 'true', 'True', '1'),
    }

    # Motor v2: solo se valida/guarda si el formulario envía 'tipo_beneficio'
    # (el formulario anterior, sin estos campos, sigue funcionando con los defaults del modelo).
    motor_v2 = 'tipo_beneficio' in post
    if motor_v2:
        cabecera.update(_validar_motor_v2(post, errores))

    filas_cond, filas_acc = _filas_desde_post(post)

    condiciones = []
    for n, f in enumerate(filas_cond, start=1):
        # Fila completamente vacía: se ignora.
        if not f['tipo_condicion'] and not f['valor_condicion']:
            continue
        if f['tipo_condicion'] not in tipos_cond:
            errores.append(f'Condición {n}: tipo no válido.')
            continue
        if not f['valor_condicion']:
            errores.append(f'Condición {n}: el valor es obligatorio.')
            continue
        try:
            cantidad = _to_decimal(f['cantidad_minima'], default=Decimal('1'))
        except ValueError:
            errores.append(f'Condición {n}: la cantidad mínima no es un número válido.')
            continue
        if cantidad <= 0:
            errores.append(f'Condición {n}: la cantidad mínima debe ser mayor que cero.')
            continue
        condiciones.append({
            'tipo_condicion': f['tipo_condicion'],
            'valor_condicion': f['valor_condicion'][:100],
            'cantidad_minima': cantidad,
        })

    acciones = []
    for n, f in enumerate(filas_acc, start=1):
        if not f['tipo_accion'] and not f['valor_accion'] and not f['id_articulo_regalo']:
            continue
        if f['tipo_accion'] not in tipos_acc:
            errores.append(f'Acción {n}: tipo no válido.')
            continue
        try:
            valor = _to_decimal(f['valor_accion'], default=Decimal('0'))
        except ValueError:
            errores.append(f'Acción {n}: el valor no es un número válido.')
            continue
        if f['tipo_accion'] == 'REGALO':
            if not f['id_articulo_regalo']:
                errores.append(f'Acción {n}: el artículo de regalo es obligatorio.')
                continue
        else:
            if valor <= 0:
                errores.append(f'Acción {n}: el valor debe ser mayor que cero.')
                continue
            if f['tipo_accion'] == 'DESCUENTO_PORC' and valor > 100:
                errores.append(f'Acción {n}: el porcentaje no puede superar 100.')
                continue
        acciones.append({
            'tipo_accion': f['tipo_accion'],
            'valor_accion': valor,
            'id_articulo_regalo': (f['id_articulo_regalo'][:20] or None)
            if f['tipo_accion'] == 'REGALO' else None,
        })

    # En el motor v2 el beneficio vive en la propia promoción; las acciones heredadas son opcionales.
    if not motor_v2 and not acciones and not any(e.startswith('Acción') for e in errores):
        errores.append('Debe definir al menos una acción válida.')

    return cabecera, condiciones, acciones, errores


def _form_base():
    return {
        'nombre': '', 'fch_inicio': '', 'fch_fin': '', 'prioridad': 1, 'activo': True,
        'dias_semana': [], 'monto_minimo': '0', 'alcance': 'TODA_TIENDA', 'alcance_valores': [],
        'tipo_beneficio': 'DESCUENTO_PORC', 'valor_beneficio': '', 'cada_n_unidades': 2,
    }


def _split_csv(texto):
    return [v.strip() for v in str(texto or '').split(',') if v.strip()]


def _num_str(valor):
    """Decimal -> texto sin ceros sobrantes ('50000.00' -> '50000') para repoblar inputs."""
    if valor is None or valor == '':
        return ''
    d = Decimal(str(valor))
    return format(d.normalize(), 'f') if d == d.to_integral() else format(d, 'f').rstrip('0').rstrip('.')


def _form_desde_promocion(promocion):
    return {
        'nombre': promocion.nom_promocion,
        'fch_inicio': _fmt_input_dt(promocion.fch_inicio),
        'fch_fin': _fmt_input_dt(promocion.fch_fin),
        'prioridad': promocion.prioridad,
        'activo': promocion.activo,
        'dias_semana': _split_csv(promocion.dias_semana),
        'monto_minimo': _num_str(promocion.monto_minimo) or '0',
        'alcance': promocion.alcance,
        'alcance_valores': _split_csv(promocion.alcance_valores),
        'tipo_beneficio': promocion.tipo_beneficio,
        'valor_beneficio': _num_str(promocion.valor_beneficio) if promocion.valor_beneficio else '',
        'cada_n_unidades': promocion.cada_n_unidades,
    }


def _form_desde_post(post):
    """Valores crudos del POST para repoblar el formulario tras un error (nada se pierde ni se normaliza)."""
    f = _form_base()
    f.update({
        'nombre': post.get('nombre', ''),
        'fch_inicio': post.get('fch_inicio', ''),
        'fch_fin': post.get('fch_fin', ''),
        'prioridad': post.get('prioridad', ''),
        'activo': post.get('activo') in ('on', 'true', 'True', '1'),
    })
    if 'tipo_beneficio' in post:
        f.update({
            'dias_semana': [p for d in post.getlist('dias_semana') for p in _split_csv(d)],
            'monto_minimo': post.get('monto_minimo', ''),
            'alcance': post.get('alcance') or 'TODA_TIENDA',
            'alcance_valores': [p for v in post.getlist('alcance_valores') for p in _split_csv(v)],
            'tipo_beneficio': post.get('tipo_beneficio') or 'DESCUENTO_PORC',
            'valor_beneficio': post.get('valor_beneficio', ''),
            'cada_n_unidades': post.get('cada_n_unidades', '') or 2,
        })
    return f


def _resolver_nombres_alcance(alcance, ids):
    """
    [{'id', 'codigo', 'nombre'}] para los IDs del alcance (para mostrarlos como chips en el formulario).
    Si Oracle no responde, se devuelve el ID como nombre; nunca lanza excepciones.
    """
    ids = [str(i) for i in ids][:999]
    items = {i: {'id': i, 'codigo': '', 'nombre': i} for i in ids}
    consulta = {
        'GRUPO': "SELECT ID_GRUPO, COD_GRUPO, NOM_GRUPO FROM IN_GRUPOS WHERE ID_GRUPO IN ({})",
        'LINEA': "SELECT ID_LINEA, NULL, NOM_LINEA FROM IN_LINEAS WHERE ID_LINEA IN ({})",
        'PRODUCTO': "SELECT ID_ARTICULO, REFERENCIA, NOM_ARTICULO FROM IN_ARTICULOS WHERE ID_ARTICULO IN ({})",
        'SELECCION_PRODUCTOS': "SELECT ID_ARTICULO, REFERENCIA, NOM_ARTICULO FROM IN_ARTICULOS WHERE ID_ARTICULO IN ({})",
    }.get(alcance)
    if ids and consulta:
        try:
            with get_oracle_connection().cursor() as cursor:
                cursor.execute(consulta.format(','.join(['%s'] * len(ids))), ids)
                for id_, cod, nom in cursor.fetchall():
                    items[str(id_)] = {'id': str(id_), 'codigo': str(cod or ''), 'nombre': str(nom or id_)}
        except Exception:
            logger.exception("No se pudieron resolver los nombres del alcance %s", alcance)
    return [items[i] for i in ids]


def _contexto_form(promocion, form=None, condiciones=None, acciones=None):
    """
    Contexto común del formulario (Paso 2: templates / Alpine.js).

    promocion          -> instancia PrPromocion o None (crear)
    es_edicion         -> bool
    form               -> dict para repoblar el formulario:
                          nombre, fch_inicio, fch_fin, prioridad, activo,
                          dias_semana (lista de '0'..'6'), monto_minimo, alcance,
                          alcance_valores (lista de IDs), tipo_beneficio, valor_beneficio,
                          cada_n_unidades
    tipos_alcance      -> [{'value','label'}] de PrPromocion.ALCANCE_CHOICES
    tipos_beneficio    -> [{'value','label'}] de PrPromocion.TIPO_BENEFICIO_CHOICES
    dias_semana_opciones -> [{'value','label'}] 0=Lunes ... 6=Domingo
    alcance_items_json -> [{'id','codigo','nombre'}] de los IDs seleccionados (lista para |json_script)
    url_catalogos      -> URL del endpoint JSON de Grupos/Líneas/Productos
    tipos_condicion / tipos_accion / condiciones / acciones / condiciones_json / acciones_json
                       -> motor anterior (PrCondicion / PrAccion), se conservan sin cambios
    """
    if form is None:
        form = _form_desde_promocion(promocion) if promocion is not None else _form_base()

    if condiciones is None:
        condiciones = list(promocion.condiciones.order_by('id_condicion')) if promocion else []
        condiciones_json = [
            {'tipo_condicion': c.tipo_condicion, 'valor_condicion': c.valor_condicion,
             'cantidad_minima': float(c.cantidad_minima)}
            for c in condiciones
        ]
    else:
        condiciones_json = [
            {'tipo_condicion': c['tipo_condicion'], 'valor_condicion': c['valor_condicion'],
             'cantidad_minima': str(c['cantidad_minima'])}
            for c in condiciones
        ]

    if acciones is None:
        acciones = list(promocion.acciones.order_by('id_accion')) if promocion else []
        acciones_json = [
            {'tipo_accion': a.tipo_accion, 'valor_accion': float(a.valor_accion),
             'id_articulo_regalo': a.id_articulo_regalo or ''}
            for a in acciones
        ]
    else:
        acciones_json = [
            {'tipo_accion': a['tipo_accion'], 'valor_accion': str(a['valor_accion']),
             'id_articulo_regalo': a.get('id_articulo_regalo') or ''}
            for a in acciones
        ]

    return {
        'promocion': promocion,
        'es_edicion': promocion is not None,
        'form': form,
        'tipos_alcance': _choices(PrPromocion.ALCANCE_CHOICES),
        'tipos_beneficio': _choices(PrPromocion.TIPO_BENEFICIO_CHOICES),
        'dias_semana_opciones': _choices(DIAS_SEMANA),
        'alcance_items_json': _resolver_nombres_alcance(form['alcance'], form['alcance_valores']),
        'url_catalogos': reverse('pos:api_promociones_catalogos'),
        'tipos_condicion': _choices(PrCondicion.TIPO_CONDICION_CHOICES),
        'tipos_accion': _choices(PrAccion.TIPO_ACCION_CHOICES),
        'condiciones': condiciones,
        'acciones': acciones,
        'condiciones_json': condiciones_json,
        'acciones_json': acciones_json,
    }


def _guardar(request, promocion):
    """
    Procesa el POST de crear/editar. Retorna (redirect_response, None) si guardó, o
    (None, contexto) si hay errores y se debe re-renderizar el formulario.
    """
    cabecera, condiciones, acciones, errores = _validar_y_normalizar(request.POST)
    if errores:
        for e in errores:
            messages.error(request, e)
        # Se re-renderiza con lo que escribió el usuario (valores crudos, no se pierde nada).
        filas_cond, filas_acc = _filas_desde_post(request.POST)
        ctx = _contexto_form(promocion, form=_form_desde_post(request.POST), condiciones=[], acciones=[])
        ctx['condiciones_json'] = filas_cond
        ctx['acciones_json'] = filas_acc
        ctx['condiciones'] = filas_cond
        ctx['acciones'] = filas_acc
        return None, ctx

    with transaction.atomic():
        if promocion is None:
            promocion = PrPromocion.objects.create(**cabecera)
        else:
            for campo, valor in cabecera.items():
                setattr(promocion, campo, valor)
            promocion.save()
            promocion.condiciones.all().delete()
            promocion.acciones.all().delete()
        PrCondicion.objects.bulk_create([PrCondicion(promocion=promocion, **c) for c in condiciones])
        PrAccion.objects.bulk_create([PrAccion(promocion=promocion, **a) for a in acciones])
    return promocion, None


def promocion_crear(request):
    """Alta de promoción con condiciones y acciones dinámicas."""
    if request.method == 'POST':
        promocion, ctx = _guardar(request, None)
        if promocion is not None:
            messages.success(request, f'Promoción «{promocion.nom_promocion}» creada correctamente.')
            return redirect('pos:promociones_lista')
        return render(request, 'pos/promociones/form.html', ctx)
    return render(request, 'pos/promociones/form.html', _contexto_form(None))


def promocion_editar(request, pk):
    """Edición de promoción; reemplaza condiciones y acciones previas."""
    promocion = get_object_or_404(PrPromocion, pk=pk)
    if request.method == 'POST':
        guardada, ctx = _guardar(request, promocion)
        if guardada is not None:
            messages.success(request, f'Promoción «{guardada.nom_promocion}» actualizada correctamente.')
            return redirect('pos:promociones_lista')
        return render(request, 'pos/promociones/form.html', ctx)
    return render(request, 'pos/promociones/form.html', _contexto_form(promocion))


@require_POST
def promocion_toggle_activo(request, pk):
    """Activa / desactiva una promoción."""
    promocion = get_object_or_404(PrPromocion, pk=pk)
    promocion.activo = not promocion.activo
    promocion.save(update_fields=['activo'])
    estado = 'activada' if promocion.activo else 'desactivada'
    messages.success(request, f'Promoción «{promocion.nom_promocion}» {estado}.')
    return redirect('pos:promociones_lista')


@require_POST
def promocion_eliminar(request, pk):
    """Elimina la promoción (condiciones y acciones caen por CASCADE)."""
    promocion = get_object_or_404(PrPromocion, pk=pk)
    nombre = promocion.nom_promocion
    promocion.delete()
    messages.success(request, f'Promoción «{nombre}» eliminada.')
    return redirect('pos:promociones_lista')


def api_promociones_catalogos(request):
    """
    Catálogos de apoyo para el formulario de promociones (Oracle del tenant activo).

    GET /pos/api/promociones/catalogos/            -> {'grupos': [...], 'lineas': [...]}
    GET /pos/api/promociones/catalogos/?q=texto    -> además {'productos': [...]} (q >= 2 caracteres, top 30)

    grupos / lineas: [{'id', 'nombre'}]  (IN_GRUPOS.ID_GRUPO/NOM_GRUPO, IN_LINEAS.ID_LINEA/NOM_LINEA)
    productos:       [{'id', 'codigo', 'nombre'}]  (IN_ARTICULOS.ID_ARTICULO/REFERENCIA/NOM_ARTICULO)
    """
    q = (request.GET.get('q') or '').strip().upper()
    respuesta = {'status': 'success', 'grupos': [], 'lineas': []}
    try:
        with get_oracle_connection().cursor() as cursor:
            cursor.execute("SELECT ID_GRUPO, NOM_GRUPO FROM IN_GRUPOS ORDER BY NOM_GRUPO")
            respuesta['grupos'] = [{'id': str(r[0]), 'nombre': str(r[1] or r[0])} for r in cursor.fetchall()]
            cursor.execute("SELECT ID_LINEA, NOM_LINEA FROM IN_LINEAS ORDER BY NOM_LINEA")
            respuesta['lineas'] = [{'id': str(r[0]), 'nombre': str(r[1] or r[0])} for r in cursor.fetchall()]

            if len(q) >= 2:
                comodin = f"%{q}%"
                cursor.execute(
                    "SELECT * FROM ("
                    "  SELECT ID_ARTICULO, REFERENCIA, NOM_ARTICULO FROM IN_ARTICULOS "
                    "  WHERE NVL(ACTIVO_INACTIVO, 'A') = 'A' "
                    "    AND (UPPER(ID_ARTICULO) LIKE %s OR UPPER(REFERENCIA) LIKE %s OR UPPER(NOM_ARTICULO) LIKE %s) "
                    "  ORDER BY NOM_ARTICULO"
                    ") WHERE ROWNUM <= 30",
                    [comodin, comodin, comodin],
                )
                respuesta['productos'] = [
                    {'id': str(r[0]), 'codigo': str(r[1] or ''), 'nombre': str(r[2] or '')}
                    for r in cursor.fetchall()
                ]
        return JsonResponse(respuesta)
    except Exception as exc:
        logger.exception("Error en api_promociones_catalogos")
        return JsonResponse({'status': 'error', 'message': str(exc), 'grupos': [], 'lineas': []}, status=500)
