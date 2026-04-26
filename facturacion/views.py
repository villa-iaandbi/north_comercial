from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import Q
from core.models import MvPedidosNorth
from .services import procesar_lote_pedidos

def facturacion_masiva_view(request):
    """
    Vista de facturación masiva.
    Consume la vista (o tabla) de pedidos pendientes y los lista.
    Se aplican las reglas Anti-Paginación para Oracle 11g.
    """
    # 1. Traer QuerySet ordenado del más antiguo al más nuevo
    qs_pedidos = MvPedidosNorth.objects.select_related('id_tercero').filter(
        Q(estado_pedido='APR') & 
        (Q(procesado__isnull=True) | ~Q(procesado='S'))
    ).order_by('fch_pedido')  # Asumiendo que la fecha de pedido se denomina así en el modelo

    # 2. Iteración Defensiva Limitada (Regla 11g): Sin Slicing en SQL (Límite 50)
    pedidos_masivos = []
    for count, pedido in enumerate(qs_pedidos):
        if count >= 50:
            break
        pedidos_masivos.append(pedido)

    context = {
        'pedidos': pedidos_masivos,
        'cantidad_total': len(pedidos_masivos),
    }

    return render(request, 'facturacion/masiva.html', context)

def facturar_lote_api(request):
    """
    Endpoint invocado desde el cliente (con Fetch API) para disparar la facturación.
    """
    if request.method == 'POST':
        import json
        try:
            body = json.loads(request.body)
            lista_pedidos = body.get('pedidos', [])
        except Exception:
            return JsonResponse({'error': 'JSON inválido'}, status=400)
            
        if not lista_pedidos:
            return JsonResponse({'error': 'No se seleccionaron pedidos'}, status=400)

        # Criterio anti-paginación, filtrado por la selección manual
        qs_pedidos = MvPedidosNorth.objects.select_related('id_tercero').filter(
            num_pedido__in=lista_pedidos,
            estado_pedido='APR'
        ).filter(
            Q(procesado__isnull=True) | ~Q(procesado='S')
        ).order_by('fch_pedido')
        
        # Iteración Defensiva Limitada (Regla 11g)
        pedidos_lista = []
        for count, pedido in enumerate(qs_pedidos.iterator()):
            if count >= 50:
                break
            pedidos_lista.append(pedido)
            
        resultados = procesar_lote_pedidos(pedidos_lista)
        return JsonResponse(resultados)
        
    return JsonResponse({'error': 'Método no permitido'}, status=405)


def fel_dashboard(request):
    """
    Vista principal del dashboard para monitorear Facturación Electrónica (FEL).
    """
    from django.utils import timezone
    from core.models import CtVentasFel
    
    local_now = timezone.localtime(timezone.now())
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = local_now.replace(hour=23, minute=59, second=59)
    
    # KPIs diarios para evitar la sobrecarga de consultas históricas profundas
    base_qs = CtVentasFel.objects.filter(fch_envio__range=(today_start, today_end))
    
    total_hoy = base_qs.count()
    error_count = base_qs.filter(est_pt__in=['2', '99', '80']).count() # Consideramos rechazos y fallos
    success_count = total_hoy - error_count
    
    pct_exito = round((success_count / total_hoy * 100) if total_hoy > 0 else 0, 1)

    context = {
        'total_hoy': total_hoy,
        'error_count': error_count,
        'pct_exito': pct_exito,
    }
    return render(request, 'facturacion/fel_dashboard.html', context)


def fel_table_results(request):
    """
    Vista diseñada para HTMX con Paginación.
    Retorna únicamente fragmentos HTML (<tr>).
    """
    from core.models import CtVentasFel
    from django.core.paginator import Paginator
    from django.db.models import Q
    
    search = request.GET.get('search', '').strip()
    status = request.GET.get('status', 'todos').strip()
    page_number = request.GET.get('page', 1)
    
    where_clauses = []
    params = []
    
    if search:
        where_clauses.append("(FEL.ID_DOCUMENTO LIKE %s OR FEL.CUFE LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
        
    if status == 'errores':
        where_clauses.append("FEL.EST_PT IN ('2', '99', '80')")
    elif status == 'aprobados':
        where_clauses.append("FEL.EST_PT NOT IN ('2', '99', '80')")
        
    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)
        
    try:
        page_number = int(page_number)
    except ValueError:
        page_number = 1
        
    limit = 50
    offset = (page_number - 1) * limit
    upper_bound = offset + limit + 1
    
    # Motor de Paginación Nativo para Oracle 11g (Reemplaza a FETCH FIRST / OFFSET no soportados en 11g)
    # y evita el OOM/Congelamiento del loop iterador en Python
    sql = f'''
    SELECT * FROM (
        SELECT a.*, ROWNUM rnum FROM (
            SELECT FEL.*, DOC.NUM_DOCUMENTO AS DOC_NUM, DOC.FCH_DOCUMENTO AS DOC_FCH
            FROM CT_VENTAS_FEL FEL
            LEFT JOIN CO_DOCUMENTOS DOC ON FEL.ID_DOCUMENTO = DOC.ID_DOCUMENTO
            {where_sql}
            ORDER BY DOC.FCH_DOCUMENTO DESC NULLS LAST, FEL.ID_DOCUMENTO DESC
        ) a WHERE ROWNUM <= %s
    ) WHERE rnum > %s
    '''
    
    params.extend([upper_bound, offset])
    
    records_plus_one = list(CtVentasFel.objects.raw(sql, params))
    
    has_next = len(records_plus_one) > limit
    records = records_plus_one[:limit] if has_next else records_plus_one
        
    try:
        from django_q.models import OrmQ
        has_active_tasks = OrmQ.objects.using('qcluster_db').exists()
    except Exception:
        has_active_tasks = False

    context = {
        'fel_records': records,
        'has_next': has_next,
        'next_page_number': page_number + 1 if has_next else None,
        'search': search,
        'status': status,
        'has_active_tasks': has_active_tasks
    }
    return render(request, 'facturacion/partials/fel_table_rows.html', context)

def fel_document_detail(request, id_documento):
    """
    Vista diseñada para HTMX. Retorna el template HTML fragmentado del Modal
    con los detalles del documento y, opcionalmente, lee el JSON del payload local.
    """
    from core.models import CtVentasFel
    import os
    from django.conf import settings
    from django.shortcuts import get_object_or_404
    
    documento = get_object_or_404(CtVentasFel, id_documento=id_documento)
    
    payload_json = None
    # Intentar leer el log JSON si existe
    try:
        log_dir = os.path.join(settings.BASE_DIR, 'logs', 'fel_payloads')
        filename = f"payload_{id_documento}.json"
        filepath = os.path.join(log_dir, filename)
        
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                payload_json = f.read()
    except Exception as e:
        pass # Fallback silencioso si no puede leer el JSON local
        
    context = {
        'documento': documento,
        'payload_json': payload_json
    }
    
    return render(request, 'facturacion/partials/fel_modal.html', context)

def fel_bulk_retry(request):
    """
    Vista receptora para encolar facturas seleccionadas para reintento.
    """
    from django_q.tasks import async_task
    
    if request.method == 'POST':
        selected_ids = request.POST.getlist('selected_docs')
        if not selected_ids:
            # Retorna un HTML simple indicando que no se seleccionó nada
            return render(request, 'facturacion/partials/fel_bulk_alert.html', {
                'message': 'No se seleccionaron facturas para reintentar.',
                'alert_type': 'warning'
            })
            
        # Despachar tarea en background a django-q2
        async_task('facturacion.tasks.process_bulk_invoices_task', selected_ids)
        
        # Retornar alerta de éxito
        response = render(request, 'facturacion/partials/fel_bulk_alert.html', {
            'message': f'Se han encolado {len(selected_ids)} facturas para su reintento en segundo plano. La tabla se actualizará automáticamente.',
            'alert_type': 'success'
        })
        response['HX-Trigger'] = 'reloadTable'
        return response
    else:
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
