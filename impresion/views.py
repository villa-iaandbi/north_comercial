import json
import os
from datetime import datetime
from django.shortcuts import render
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection

# Simulando la librería si falla la instalación o no está lista en Windows inmediatamente
try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError, Exception) as e:
    print(f"Advertencia: WeasyPrint no pudo inicializarse ({e}). Se usará renderizado Dummy.")
    WEASYPRINT_AVAILABLE = False


def dictfetchall(cursor):
    "Return all rows from a cursor as a dict"
    columns = [col[0].lower() for col in cursor.description]
    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]

def portal_impresion(request):
    """Renderiza el layout base del portal de impresión."""
    return render(request, 'impresion/impresion_dashboard.html')

def impresion_table_results(request):
    """
    Controlador HTMX para devolver las filas de la tabla con filtros y orden.
    Trae los documentos y hace JOIN con co_terceros para el nombre del cliente.
    """
    search_query = request.GET.get('q', '').strip()
    estado_filter = request.GET.get('estado', 'todos').strip().lower()
    
    # Construir cláusula WHERE dinámicamente
    where_clauses = ["1=1"]
    params = []
    
    if search_query:
        where_clauses.append("(LOWER(doc.NUM_DOCUMENTO) LIKE LOWER(%s) OR LOWER(ter.NOM_TERCERO) LIKE LOWER(%s))")
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    if estado_filter == 'pendiente':
        where_clauses.append("NVL(doc.SIONO_IMPRESO, 'N') = 'N'")
    elif estado_filter == 'impreso':
        where_clauses.append("NVL(doc.SIONO_IMPRESO, 'N') = 'S'")

    where_sql = "WHERE " + " AND ".join(where_clauses)
    
    if estado_filter == 'todos':
        order_sql = "ORDER BY NVL(doc.SIONO_IMPRESO, 'N') ASC, doc.FCH_DOCUMENTO DESC"
    else:
        order_sql = "ORDER BY doc.FCH_DOCUMENTO DESC"
        
    try:
        page_number = int(request.GET.get('page', 1))
    except ValueError:
        page_number = 1
        
    limit = 50
    offset = (page_number - 1) * limit
    upper_bound = offset + limit + 1
        
    # Query nativo a Oracle
    sql = f"""
    SELECT * FROM (
        SELECT a.*, ROWNUM rnum FROM (
            SELECT 
                doc.ID_DOCUMENTO,
                doc.NUM_DOCUMENTO,
                doc.FCH_DOCUMENTO,
                doc.TOT_DOCUMENTO,
                ven.COD_VENDEDOR as ID_VENDEDOR,
                NVL(doc.SIONO_IMPRESO, 'N') as SIONO_IMPRESO,
                ter.NOM_TERCERO as CLIENTE_NOMBRE,
                fel.CUFE
            FROM CO_DOCUMENTOS doc
            INNER JOIN CT_VENTAS_FEL fel ON doc.ID_DOCUMENTO = fel.ID_DOCUMENTO
            LEFT JOIN CO_TERCEROS ter ON doc.ID_TERCERO = ter.ID_TERCERO
            LEFT JOIN CT_VENDEDORES ven ON doc.ID_VENDEDOR = ven.ID_VENDEDOR
            {where_sql}
            {order_sql}
        ) a WHERE ROWNUM <= %s
    ) WHERE rnum > %s
    """
    
    with connection.cursor() as cursor:
        cursor.execute(sql, params + [upper_bound, offset])
        results = cursor.fetchall()
        
        resultados = []
        for r in results:
            resultados.append({
                'id_documento': r[0],
                'num_documento': r[1],
                'fch_documento': r[2],
                'tot_documento': r[3],
                'id_vendedor': r[4], 
                'siono_impreso': r[5],
                'cliente_nombre': r[6],
                'cufe': r[7],
            })
            
    has_next = len(resultados) > limit
    if has_next:
        resultados = resultados[:limit]
        
    return render(request, 'impresion/partials/impresion_table_rows.html', {
        'resultados': resultados,
        'has_next': has_next,
        'next_page_number': page_number + 1 if has_next else None,
        'search': search_query,
        'estado': estado_filter
    })

def descargar_factura_pdf(request, id_documento):
    """
    Genera el PDF usando render_invoice_to_pdf, lo guarda en la carpeta por fecha de factura y lo retorna.
    Además marca la factura como impresa en la DB.
    """
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
        
    from .document_renderer import render_invoice_to_pdf

    try:
        file_path = render_invoice_to_pdf(id_documento)
        pdf_filename = os.path.basename(file_path)

        # Actualizar estado a impreso 'S'
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE CO_DOCUMENTOS SET SIONO_IMPRESO = 'S' WHERE ID_DOCUMENTO = %s
            """, [id_documento])

        with open(file_path, 'rb') as pdf:
            response = HttpResponse(pdf.read(), content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="{pdf_filename}"'
            return response
    except Exception as e:
        return HttpResponse(f"Error generando PDF para {id_documento}: {str(e)}", status=500)
        
from django.db import connection
from django_q.tasks import async_task

def procesar_lote_impresion(ids_seleccionados):
    """Tarea de fondo para generar PDFs masivamente."""
    from .document_renderer import render_invoice_to_pdf
    
    exitosos = 0
    fallidos = 0
    
    for doc_id in ids_seleccionados:
        try:
            render_invoice_to_pdf(doc_id)
            
            # Actualizar el documento a 'Impreso' (siono_impreso='S')
            with connection.cursor() as cursor:
                cursor.execute("""
                    UPDATE CO_DOCUMENTOS SET SIONO_IMPRESO = 'S' WHERE ID_DOCUMENTO = %s
                """, [doc_id])
                
            exitosos += 1
        except Exception as e:
            print(f"Error generando PDF para {doc_id}: {e}")
            fallidos += 1
            
    return exitosos, fallidos

def generar_pdfs_seleccionados(request):
    """
    Controlador HTMX POST que recibe una lista de IDs seleccionados,
    y lanza una tarea de Django-Q2 en segundo plano para procesarlos.
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
        
    ids_seleccionados = request.POST.getlist('selected_docs')
    
    if not ids_seleccionados:
        return HttpResponse("""
        <div x-data="{ show: true }" x-init="setTimeout(() => show = false, 5000)" x-show="show" x-transition.opacity id="toast-message" class="fixed bottom-5 right-5 bg-red-600 text-white px-6 py-4 rounded shadow-lg text-sm z-50 flex items-center space-x-2">
            <svg class="w-5 h-5 text-red-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            <span>Debe seleccionar al menos un documento para generar PDFs.</span>
        </div>
        """, status=200)

    # Si son 20 o menos facturas, procesamos directamente para respuesta inmediata sin requerir qcluster
    if len(ids_seleccionados) <= 20:
        exitosos, fallidos = procesar_lote_impresion(ids_seleccionados)
        msg = f"Éxito: Se generaron {exitosos} documentos PDF ({fallidos} fallos)."
    else:
        async_task(procesar_lote_impresion, ids_seleccionados)
        msg = f"Se ha enviado a la cola de impresión el lote de {len(ids_seleccionados)} facturas. Procesando en segundo plano..."

    html_response = f"""
    <div x-data="{{ show: true }}" x-init="setTimeout(() => show = false, 6000)" x-show="show" x-transition.opacity
         id="toast-message" class="fixed bottom-5 right-5 bg-indigo-600 text-white px-6 py-4 rounded shadow-lg text-sm z-50 flex items-center space-x-3">
        <svg class="h-5 w-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
        </svg>
        <span>{msg}</span>
    </div>
    <style>
      #gen-btn-spinner {{ display: none; }}
      #gen-btn-icon {{ display: block; }}
    </style>
    """
    
    response = HttpResponse(html_response)
    response['HX-Trigger'] = 'refreshTable'
    return response


def descargar_factura_docx(request, id_documento):
    """
    Genera y entrega la factura en formato Microsoft Word (.docx) utilizando docxtpl y QR dinámico.
    """
    from django.http import FileResponse, HttpResponseNotFound
    from impresion.docx_engine import renderizar_factura_docx
    import logging
    logger = logging.getLogger(__name__)

    try:
        docx_stream = renderizar_factura_docx(str(id_documento))
        filename = f"Factura_{id_documento}.docx"
        response = FileResponse(
            docx_stream,
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        logger.error(f"Error al generar factura .docx para ID {id_documento}: {e}")
        return HttpResponseNotFound(f"No fue posible generar la factura .docx para ID '{id_documento}': {e}")

