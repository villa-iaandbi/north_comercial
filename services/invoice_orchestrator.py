import logging
from django.db import transaction
from django.utils import timezone
from core.models import CtVentasFel
from services.binapps_client import BinappsClient
from services.payload_builder import InvoicePayloadBuilder

# Inyección externa - Se asume que el DTO de recuperación funciona contractualmente
from services.legacy_repository import get_doors_invoice_data

logger = logging.getLogger(__name__)

# Diccionario universal de fallback para los Nom_Estado en caso de carencias en el JSON proveedor
STATUS_MAP = {
    '30': 'Procesado Exitosamente',
    '31': 'Procesado con Alertas (Revisar)',
    '80': 'Rechazado Estructuralmente por DIAN',
    '99': 'Fallo Crítico Interconexión / API'
}

class InvoiceOrchestrator:
    """
    Capa de servicio que orquesta todo el flujo end-to-end de facturación electrónica.
    Extrae, transfiere a un DTO, despacha por red, y persiste trazas y acuses en Oracle CT_VENTAS_FEL.
    """

    @staticmethod
    def process_electronic_invoice(id_documento: str, dry_run: bool = False) -> dict:
        try:
            # 1. Extracción de datos del ERP Legacy
            invoice_data = get_doors_invoice_data(id_documento)
            if not invoice_data:
                return {
                    "success": False,
                    "error": f"Extracción rechazada: Documento ID_DOCUMENTO '{id_documento}' inexistente en la base de datos origen."
                }
                
            header_data, items_data, taxes_data = invoice_data
            
            # 2. Generación y Auditoría del DTO (La guarda a disco ocurre internamente)
            payload = InvoicePayloadBuilder.build_invoice_payload(header_data, items_data, taxes_data)
            
            # 3. Disparo a los servidores de la Proveeduría Tecnológica
            client = BinappsClient(dry_run=dry_run)
            response = client.transmit_document(payload, is_credit_note=False)
            
            # 4. Homologación Semántica de la Respuesta (Defensivo por las llaves inestables)
            # Adaptamos las variaciones léxicas en caso de que BINAPPS responda difuminado
            est_pt = str(response.get('State', response.get('eDocumentStatus', '99')))
            cufe = response.get('cufe', response.get('Cufe', ''))
            error_msg = response.get('statusMessage', response.get('Message', ''))[:3950]
            
            if est_pt == '30':
                nom_est = 'Procesado/Aprobado'
            elif est_pt == '2':
                nom_est = 'Rechazado/Preexistente'
            else:
                nom_est = STATUS_MAP.get(est_pt, f'Estado {est_pt}')
                
            is_success = est_pt in ['30', '31']
            
            # Obtener tiempo de la zona horaria local limpio de timezone info para Oracle
            import pytz
            from datetime import datetime
            bogota_tz = pytz.timezone('America/Bogota')
            local_time_naive = datetime.now(bogota_tz).replace(tzinfo=None)

            defaults_data = {
                'cufe': cufe[:100],
                'error': error_msg if not is_success else None,
                'est_pt': est_pt,
                'nom_est_pt': nom_est[:200],
                'est_dian': est_pt, # Asumimos espejo por ahora
                'nom_est_dian': nom_est[:200],
                'est_cliente': est_pt,
                'nom_est_cliente': nom_est[:200],
                'fch_envio': local_time_naive,
                'fch_respuesta': local_time_naive.strftime('%Y-%m-%dT%H:%M:%S')
            }

            # 5. Atomic Commitment con Upsert (update_or_create)
            with transaction.atomic():
                CtVentasFel.objects.update_or_create(
                    id_documento=id_documento,
                    defaults=defaults_data
                )
                    
            # 6. Salida Estándar para Workers Asíncronos
            return {
                "success": is_success,
                "api_state": est_pt,
                "message": error_msg if not is_success else "OK",
                "cufe": cufe
            }

        except Exception as e:
            msg_critico = f"Colapso Python Crítico en InvoiceOrchestrator: {str(e)}"
            logger.error(msg_critico)
            return {
                "success": False,
                "api_state": "99",
                "error": msg_critico
            }
