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
