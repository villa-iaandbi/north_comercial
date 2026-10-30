# -*- coding: utf-8 -*-
import os
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
    """ Retorna el detalle transaccional de un lote """
    nit_empresa = os.environ.get('NIT_EMPRESA', '900123456')
    service = ConciliacionDianService(nit_empresa)
    return JsonResponse(service.get_detalle_lote(id_lote))


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
