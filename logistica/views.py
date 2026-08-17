from django.shortcuts import render
from django.db.models import Q
from django.db import connection
from core.models import CoDocumento, CoTercero, SgMunicipioDian
from .services import crear_entrega_logistica
from django.http import HttpResponseNotAllowed

def entregas_list_view(request):
    """
    Vista principal para cargar la UI de generación de entregas.
    Pre-carga los filtros (Vendedores y Municipios) que tengan documentos pendientes.
    """
    # Cargar municipios
    municipios = SgMunicipioDian.objects.using('default').order_by('nom_municipio_dian')
    
    # Cargar vendedores con sus nombres de sg_personas filtrado por id_vendedor de co_documentos pendientes
    vendedores = CoDocumento.objects.raw('''
        SELECT DISTINCT p.ID_PERSONA as id_documento, p.ID_PERSONA as vendedor_id, p.NOM_PERSONA as nom_persona
        FROM co_documentos d
        JOIN sg_personas p ON d.ID_VENDEDOR = p.ID_PERSONA
        WHERE d.ID_ENTREGA IS NULL 
        AND EXISTS (SELECT 1 FROM ct_ventas v WHERE v.ID_DOCUMENTO = d.ID_DOCUMENTO)
        ORDER BY p.NOM_PERSONA
    ''')

    # Cargar transportadores de sg_usuarios (perfil TRANSP, activos) cruzados con sg_personas
    with connection.cursor() as cursor:
        cursor.execute('''
            SELECT u.ID_USUARIO, p.NOM_PERSONA
            FROM sg_usuarios u
            JOIN sg_personas p ON u.ID_USUARIO = p.ID_PERSONA
            WHERE u.ACTIVO_INACTIVO = 'A' AND u.ID_GRP_USR = 'TRANSP'
            ORDER BY p.NOM_PERSONA
        ''')
        transportadores = [
            {'id_transportador': row[0], 'nom_transportador': row[1]}
            for row in cursor.fetchall()
        ]
    
    context = {
        'vendedores': vendedores,
        'municipios': municipios,
        'transportadores': transportadores
    }
    return render(request, 'logistica/entregas_list.html', context)


def entregas_table_partial(request):
    """
    Vista HTMX para paginar y filtrar las facturas pendientes de asignación de entrega.
    Implementa Anti-Paginación para Oracle 11g.
    """
    search = request.GET.get('search', '').strip()
    id_vendedor = request.GET.get('vendedor', '').strip()
    id_municipio = request.GET.get('municipio', '').strip()
    page_number = request.GET.get('page', 1)
    
    # Construir QuerySet usando ORM
    qs = CoDocumento.objects.using('default').select_related('id_tercero', 'id_tercero__id_municipio_dian', 'id_vendedor').filter(id_entrega__isnull=True, ctventa__isnull=False)
    
    if search:
        qs = qs.filter(Q(num_documento__icontains=search) | Q(id_tercero__nom_tercero__icontains=search))
        
    if id_vendedor:
        qs = qs.filter(id_vendedor=id_vendedor)
        
    if id_municipio:
        qs = qs.filter(id_tercero__id_municipio_dian=id_municipio)
        
    qs = qs.order_by('-fch_documento', '-id_documento')
    
    try:
        page_number = int(page_number)
    except ValueError:
        page_number = 1
        
    limit = 50
    offset = (page_number - 1) * limit
    upper_bound = offset + limit
    
    # Iteración Defensiva Limitada (Regla 11g)
    records = []
    has_next = False
    
    for count, doc in enumerate(qs.iterator()):
        if count < offset:
            continue
            
        if len(records) < limit:
            # Emular los alias calculados para no romper el template
            doc.nom_tercero_calc = doc.id_tercero.nom_tercero if doc.id_tercero else None
            doc.nom_municipio_calc = doc.id_tercero.id_municipio_dian.nom_municipio_dian if doc.id_tercero and getattr(doc.id_tercero, 'id_municipio_dian', None) else None
            records.append(doc)
        elif len(records) == limit:
            has_next = True
            break
    
    context = {
        'facturas': records,
        'has_next': has_next,
        'next_page_number': page_number + 1 if has_next else None,
        'search': search,
        'vendedor': id_vendedor,
        'municipio': id_municipio
    }
    
    return render(request, 'logistica/partials/entregas_table_rows.html', context)


def generar_entrega_action(request):
    """
    Vista POST (HTMX) para recibir los IDs de factura seleccionados y crear la entrega.
    """
    if request.method == 'POST':
        selected_ids = request.POST.getlist('selected_docs')
        transportador = request.POST.get('transportador', '').strip()
        placa = request.POST.get('placa', '').strip()
        observaciones = request.POST.get('observaciones', '').strip()
        
        if not selected_ids:
            return render(request, 'logistica/partials/entregas_bulk_alert.html', {
                'message': 'No se seleccionaron facturas para agrupar.',
                'alert_type': 'warning'
            })
            
        if not transportador or not placa:
            return render(request, 'logistica/partials/entregas_bulk_alert.html', {
                'message': 'El transportador y la placa son obligatorios.',
                'alert_type': 'warning'
            })
            
        try:
            # TODO: El ID del sistema se asume 1, y la ruta se omite por indicación del usuario.
            entrega = crear_entrega_logistica(
                id_sistema='1',
                id_transportador=transportador,
                lista_ids_documentos=selected_ids,
                placa_vehiculo=placa,
                cod_ruta=None,
                observaciones=observaciones
            )
            
            response = render(request, 'logistica/partials/entregas_bulk_alert.html', {
                'message': f'¡Éxito! Se ha generado la planilla de entrega #{entrega.num_entrega} con {len(selected_ids)} facturas.',
                'alert_type': 'success'
            })
            # Refrescar la tabla automáticamente desde HTMX
            response['HX-Trigger'] = 'reloadTable'
            return response
            
        except Exception as e:
            return render(request, 'logistica/partials/entregas_bulk_alert.html', {
                'message': f'Error al generar entrega: {str(e)}',
                'alert_type': 'error'
            })
    
    return HttpResponseNotAllowed(['POST'])


from django.http import JsonResponse
from core.models import NorthCliente
import math

def haversine_km(lat1, lon1, lat2, lon2):
    try:
        r = 6371.0  # km
        phi1 = math.radians(float(lat1))
        phi2 = math.radians(float(lat2))
        delta_phi = math.radians(float(lat2) - float(lat1))
        delta_lambda = math.radians(float(lon2) - float(lon1))

        a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return r * c
    except (TypeError, ValueError):
        return 0.0

def api_ruta_ideal(request):
    """
    Endpoint de ruteo inteligente (TSP / Vecino más cercano).
    Calcula la secuencia óptima de entrega para una lista de documentos seleccionados.
    """
    if request.method == 'POST':
        import json
        try:
            body = json.loads(request.body)
            doc_ids = body.get('selected_docs', [])
        except Exception:
            doc_ids = request.POST.getlist('selected_docs[]') or request.POST.getlist('selected_docs')

        if not doc_ids:
            return JsonResponse({'error': 'No se enviaron documentos para el ruteo.'}, status=400)

        docs = CoDocumento.objects.filter(id_documento__in=doc_ids).select_related('id_tercero')
        terceros_ids = [d.id_tercero_id for d in docs if d.id_tercero_id]
        
        dict_clients = {c.id_tercero: c for c in NorthCliente.objects.filter(id_tercero__in=terceros_ids)}

        puntos = []
        for d in docs:
            cli = dict_clients.get(d.id_tercero_id)
            lat = float(cli.latitud) if cli and cli.latitud else None
            lng = float(cli.longitud) if cli and cli.longitud else None

            puntos.append({
                'id_documento': d.id_documento,
                'num_documento': d.num_documento,
                'nom_tercero': d.id_tercero.nom_tercero if d.id_tercero else d.id_tercero_id,
                'lat': lat,
                'lng': lng,
                'tot_documento': float(d.tot_documento or 0)
            })

        # Algoritmo de Vecino Más Cercano (Nearest Neighbor TSP)
        con_gps = [p for p in puntos if p['lat'] is not None and p['lng'] is not None]
        sin_gps = [p for p in puntos if p['lat'] is None or p['lng'] is None]

        ruta_ordenada = []
        distancia_total = 0.0

        if con_gps:
            no_visitados = con_gps[:]
            actual = no_visitados.pop(0)  # Iniciar en el primer punto registrado
            actual['secuencia'] = 1
            actual['distancia_tramo_km'] = 0.0
            ruta_ordenada.append(actual)

            while no_visitados:
                siguiente = min(
                    no_visitados,
                    key=lambda p: haversine_km(actual['lat'], actual['lng'], p['lat'], p['lng'])
                )
                dist_tramo = haversine_km(actual['lat'], actual['lng'], siguiente['lat'], siguiente['lng'])
                distancia_total += dist_tramo
                siguiente['secuencia'] = len(ruta_ordenada) + 1
                siguiente['distancia_tramo_km'] = round(dist_tramo, 2)
                ruta_ordenada.append(siguiente)
                no_visitados.remove(siguiente)
                actual = siguiente

        # Añadir al final los que no tengan coordenadas GPS
        for item in sin_gps:
            item['secuencia'] = len(ruta_ordenada) + 1
            item['distancia_tramo_km'] = 0.0
            ruta_ordenada.append(item)

        return JsonResponse({
            'status': 'success',
            'ruta': ruta_ordenada,
            'distancia_total_km': round(distancia_total, 2),
            'total_paradas': len(ruta_ordenada),
            'paradas_con_gps': len(con_gps)
        })

    return JsonResponse({'error': 'Método no permitido'}, status=405)
