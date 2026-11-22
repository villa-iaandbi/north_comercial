import hashlib
import logging
import os
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
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


# ---------------------------------------------------------------------------
# Soporte POS: CUFE local y transmisión no bloqueante de tiquetes
# ---------------------------------------------------------------------------

def _dian_dec(value) -> str:
    """Formato numérico DIAN para el CUFE: 2 decimales, sin separador de miles."""
    try:
        d = Decimal(str(value if value not in (None, '') else 0))
    except Exception:
        d = Decimal('0')
    return str(d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def get_dian_cufe_config() -> dict:
    """Clave técnica y ambiente DIAN desde settings o variables de entorno."""
    return {
        'clave_tecnica': str(getattr(settings, 'DIAN_CLAVE_TECNICA', os.getenv('DIAN_CLAVE_TECNICA', '')) or ''),
        'tipo_ambiente': str(getattr(settings, 'DIAN_TIPO_AMBIENTE', os.getenv('DIAN_TIPO_AMBIENTE', '2')) or '2'),
    }


def calcular_cufe_local(num_fac, fecha, hora, val_fac, val_iva, val_inc, val_ica,
                        val_tot, nit_ofe, num_adq, clave_tecnica='', tipo_ambiente='2') -> str:
    """
    CUFE estándar DIAN (SHA-384) calculado localmente:

        NumFac + FecFac + HorFac + ValFac + CodImp1(01) + ValImp1 + CodImp2(04) + ValImp2
        + CodImp3(03) + ValImp3 + ValTot + NitOFE + NumAdq + ClTec + TipoAmbiente

    fecha: 'YYYY-MM-DD'; hora: 'HH:MM:SS-05:00'. Los valores monetarios se normalizan a 2 decimales.
    Si no hay clave técnica configurada el resultado es un CUFE local provisional (no
    coincidirá con el que calcule la DIAN), pero es determinístico y permite generar el QR.
    """
    cadena = (
        f"{num_fac}{fecha}{hora}{_dian_dec(val_fac)}"
        f"01{_dian_dec(val_iva)}04{_dian_dec(val_inc)}03{_dian_dec(val_ica)}"
        f"{_dian_dec(val_tot)}{nit_ofe}{num_adq}{clave_tecnica}{tipo_ambiente}"
    )
    return hashlib.sha384(cadena.encode('utf-8')).hexdigest()


def transmitir_payload_pos_binapps(ticket_id: str, payload: dict) -> dict:
    """
    Tarea Django-Q: transmite a Binapps el payload ya construido de un tiquete POS.
    Nunca lanza excepciones (BinappsClient devuelve State '99' ante fallos de red).
    """
    from services.binapps_client import BinappsClient
    logger.info(f"[DIAN Async][POS] Transmitiendo tiquete {ticket_id}")
    try:
        response = BinappsClient(dry_run=False).transmit_document(payload, is_credit_note=False)
    except Exception as exc:
        logger.error(f"[DIAN Async][POS] Error transmitiendo tiquete {ticket_id}: {exc}")
        return {'ticket_id': ticket_id, 'success': False, 'api_state': '99', 'error': str(exc)}
    state = str(response.get('State', response.get('eDocumentStatus', '99')))
    logger.info(f"[DIAN Async][POS] Tiquete {ticket_id} -> estado {state}")
    return {
        'ticket_id': ticket_id,
        'success': state in ('30', '31'),
        'api_state': state,
        'cufe': response.get('cufe', response.get('Cufe', '')),
        'message': response.get('statusMessage', response.get('Message', '')),
    }


def encolar_transmision_pos(ticket_id: str, payload: dict) -> str:
    """Encola (Django-Q) la transmisión de un tiquete POS; la caja no espera a Binapps."""
    task_id = async_task('facturacion.dian_async.transmitir_payload_pos_binapps', ticket_id, payload)
    logger.info(f"[DIAN Async][POS] Tiquete {ticket_id} encolado con Task ID: {task_id}")
    return task_id
