from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import Q
from core.models import MvReciboNorth
from .services import procesar_lote_recibos

def lote_recibos_view(request):
    """
    Vista de consolidación masiva de Recibos de Caja.
    Se aplican las mismas reglas Anti-Paginación para Oracle 11g.
    """
    # 1. Traer QuerySet ordenado del más antiguo al más nuevo
    qs_recibos = MvReciboNorth.objects.select_related('id_tercero').filter(
        Q(estado_recibo='APR') & 
        (Q(procesado__isnull=True) | ~Q(procesado='S'))
    ).order_by('fch_recibo')

    # 2. Iteración Defensiva Limitada (Regla 11g): Sin Slicing en SQL (Límite 50)
    recibos_masivos = []
    for count, recibo in enumerate(qs_recibos):
        if count >= 50:
            break
        recibos_masivos.append(recibo)

    context = {
        'recibos': recibos_masivos,
        'cantidad_total': len(recibos_masivos),
    }

    return render(request, 'recibos/masiva.html', context)

def procesar_recibos_api(request):
    """
    Endpoint invocado desde el cliente (con Fetch API) para disparar la consolidación.
    """
    if request.method == 'POST':
        import json
        try:
            body = json.loads(request.body)
            lista_recibos = body.get('recibos', [])
        except Exception as e:
            return JsonResponse({'error': f'JSON inválido: {str(e)}'}, status=400)
            
        if not lista_recibos:
            return JsonResponse({'error': 'No se seleccionaron recibos'}, status=400)

        # Criterio anti-paginación, filtrado por la selección manual
        qs_recibos = MvReciboNorth.objects.filter(
            num_recibo__in=lista_recibos,
            estado_recibo='APR'
        ).filter(
            Q(procesado__isnull=True) | ~Q(procesado='S')
        )
        
        valid_ids = list(qs_recibos.values_list('num_recibo', flat=True))
        if not valid_ids:
            return JsonResponse({'error': f'Ninguno de los recibos ({lista_recibos}) es válido o ya procesado (Q query returned 0).'}, status=400)

        resultados = procesar_lote_recibos(valid_ids)
        return JsonResponse(resultados)

    return JsonResponse({'error': 'Método no permitido'}, status=405)
