import logging
from services.invoice_orchestrator import InvoiceOrchestrator

logger = logging.getLogger(__name__)

def process_bulk_invoices_task(document_ids: list):
    """
    Tarea asíncrona para reintentar el envío de múltiples facturas a la DIAN.
    """
    logger.info(f"Iniciando reintento masivo para {len(document_ids)} facturas: {document_ids}")
    
    exitos = 0
    fallos = 0
    
    for doc_id in document_ids:
        logger.info(f"Procesando factura ID: {doc_id} en segundo plano...")
        try:
            # Instanciamos el orquestador por cada documento para limpiar cualquier estado
            orchestrator = InvoiceOrchestrator()
            
            # Procesamos la factura a través del orquestador principal
            # Se usa dry_run=False asumiendo que debe ejecutarse la recarga real
            result = orchestrator.process_electronic_invoice(doc_id, dry_run=False)
            logger.info(f"Factura {doc_id} procesada exitosamente. Resultado: {result}")
            exitos += 1
        except Exception as e:
            logger.error(f"Error procesando factura {doc_id} en reintento masivo: {str(e)}")
            fallos += 1
            # Permitimos que continue con el siguiente documento
            continue
            
    logger.info(f"Reintento masivo finalizado. Éxitos: {exitos}, Fallos: {fallos}")
    return {'exitos': exitos, 'fallos': fallos, 'total': len(document_ids)}
