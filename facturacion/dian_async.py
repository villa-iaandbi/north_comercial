import logging
from django_q.tasks import async_task
from services.invoice_orchestrator import InvoiceOrchestrator

logger = logging.getLogger(__name__)

def transmitir_factura_binapps(id_documento: str, dry_run: bool = False) -> dict:
    """
    Transmite una factura electrónica a la DIAN a través de Binapps.
    1. Realiza el flujo OAuth2 (client_credentials).
    2. Construye el payload JSON con diccionarios nativos (dict) y json.dumps().
    3. Formatea las fechas en estricto ISO 8601 (YYYY-MM-DDTHH:mm:ss) bajo la zona horaria America/Bogota.
    4. Procesa la respuesta de Binapps y, si eDocumentStatus / State es '30', actualiza/inserta en CT_VENTAS_FEL.
    """
    logger.info(f"[DIAN Async] Iniciando transmisión para ID_DOCUMENTO: {id_documento}")
    orchestrator = InvoiceOrchestrator()
    result = orchestrator.process_electronic_invoice(id_documento, dry_run=dry_run)
    logger.info(f"[DIAN Async] Resultado transmisión {id_documento}: {result}")
    return result

def encolar_transmision_dian(id_documento: str) -> str:
    """
    Encola la transmisión de una factura en segundo plano utilizando Django-Q
    para asegurar que la UI principal nunca espere la respuesta HTTP de Binapps.
    """
    task_id = async_task('facturacion.dian_async.transmitir_factura_binapps', id_documento)
    logger.info(f"[DIAN Async] Factura {id_documento} encolada en Django-Q con Task ID: {task_id}")
    return task_id

def encolar_lote_transmision_dian(document_ids: list) -> list:
    """Encola un lote de facturas para transmisión asíncrona."""
    task_ids = []
    for doc_id in document_ids:
        t_id = encolar_transmision_dian(doc_id)
        task_ids.append(t_id)
    return task_ids
