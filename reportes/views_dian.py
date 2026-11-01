# -*- coding: utf-8 -*-
import os
import re
import json
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .services_conciliacion_dian import ConciliacionDianService


def gestor_lotes_dian(request):
    """ Renderiza la vista principal (Gestor de Lotes) """
    return render(request, 'comercial/dian_gestor_lotes.html')


@csrf_exempt
def upload_excel_dian(request):
    """ Recibe el Excel desde el frontend mediante FormData """
    if request.method == 'POST':
        archivo = request.FILES.get('archivo_dian')
        nom_lote = request.POST.get('nom_lote', 'Lote Sin Nombre')
        
        if not archivo:
            return JsonResponse({"status": "error", "mensaje": "No se proporcionó ningún archivo."})

        # Capturar el NIT desde la variable de entorno del SO (o .env a través de config)
        # Se deja un fallback básico en caso de que la variable no esté exportada en el entorno
        nit_empresa = os.environ.get('NIT_EMPRESA') or os.environ.get('NIT', '900123456')
        
        usuario = request.user.username if request.user.is_authenticated else 'SISTEMA'
        
        service = ConciliacionDianService(nit_empresa=nit_empresa)
        resultado = service.procesar_excel_dian(archivo, nom_lote, usuario)
        
        # ADR-020: Normalización de payload JSON
        return JsonResponse(resultado)
        
    return JsonResponse({"status": "error", "mensaje": "Método no permitido."}, status=405)


def api_dian_get_lotes(request):
    """ Retorna el historial de lotes """
    nit_empresa = os.environ.get('NIT_EMPRESA', '900123456')
    service = ConciliacionDianService(nit_empresa)
    return JsonResponse(service.get_lotes())


def api_dian_get_detalle(request, id_lote):
    """ Retorna el detalle transaccional de un lote (Paginado, con filtros) y sus KPIs """
    nit_empresa = os.environ.get('NIT_EMPRESA', '900123456')

    try:
        page = max(1, int(request.GET.get('page', 1)))
        limit = min(500, max(1, int(request.GET.get('limit', 100))))  # Tope de seguridad
    except ValueError:
        page = 1
        limit = 100

    # Estados válidos de ESTADO_CRUCE (calculados por P_CONCILIAR_LOTE_DIAN):
    # 1=EXACTO, 2=DESCUADRE, 3=FALTANTE, 4=FANTASMA. Se filtra estrictamente por ese valor.
    estado = request.GET.get('estado')
    if estado and str(estado).isdigit() and int(estado) in (1, 2, 3, 4):
        estado = int(estado)
    else:
        estado = None

    grupo = request.GET.get('grupo')
    tipo_doc = request.GET.get('tipo_doc')

    service = ConciliacionDianService(nit_empresa)
    resultado = service.get_detalle_lote_paginado(id_lote, page=page, limit=limit, estado=estado, grupo=grupo, tipo_doc=tipo_doc)
    return JsonResponse(resultado)


def api_dian_estado_recalculo(request, id_lote):
    """ Polling: estado de la tarea asíncrona (Django-Q2) de recálculo de un lote """
    if request.method != 'GET':
        return JsonResponse({"status": "error", "mensaje": "Método no permitido."}, status=405)

    task_id = request.GET.get('task_id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', task_id):
        return JsonResponse({"status": "error", "mensaje": "task_id inválido."}, status=400)

    nit_empresa = os.environ.get('NIT_EMPRESA', '900123456')
    service = ConciliacionDianService(nit_empresa)
    return JsonResponse(service.consultar_estado_recalculo(id_lote, task_id))


@csrf_exempt
def api_dian_accion_lote(request, id_lote):
    """ Ejecuta el recálculo PL/SQL o cierra la auditoría """
    if request.method == 'POST':
        try:
            body = json.loads(request.body)
            accion = body.get('accion')
            
            nit_empresa = os.environ.get('NIT_EMPRESA', '900123456')
            service = ConciliacionDianService(nit_empresa)
            
            if accion == 'recalcular':
                resultado = service.recalcular_lote(id_lote)
            elif accion == 'cerrar':
                resultado = service.cerrar_lote(id_lote)
            else:
                return JsonResponse({"status": "error", "mensaje": "Acción inválida."})
                
            return JsonResponse(resultado)
        except Exception as e:
            return JsonResponse({"status": "error", "mensaje": str(e)})
            
    return JsonResponse({"status": "error", "mensaje": "Método no permitido."}, status=405)
