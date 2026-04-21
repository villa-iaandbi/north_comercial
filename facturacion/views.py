from django.shortcuts import render
from django.db.models import Q
from core.models import MvPedidosNorth

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
