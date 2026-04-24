import os
import json
import logging
import requests
from django.conf import settings
from django.core.cache import cache
from services.legacy_repository import get_system_parameter

logger = logging.getLogger(__name__)

class BinappsClient:
    """
    Cliente API para integración de Facturación Electrónica con Binapps.
    Maneja autenticación OAuth2 con caché, control de timeouts y dry_run.
    """
    
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        
        # Preferir settings de Django, si no, buscar en variables de entorno OS
        self.auth_url = getattr(settings, 'BINAPPS_AUTH_URL', os.getenv('BINAPPS_AUTH_URL'))
        self.client_id = getattr(settings, 'BINAPPS_CLIENT_ID', os.getenv('BINAPPS_CLIENT_ID'))
        self.client_secret = getattr(settings, 'BINAPPS_CLIENT_SECRET', os.getenv('BINAPPS_CLIENT_SECRET'))
        self.invoice_url = getattr(settings, 'BINAPPS_INVOICE_URL', os.getenv('BINAPPS_INVOICE_URL'))
        self.credit_note_url = getattr(settings, 'BINAPPS_CREDIT_NOTE_URL', os.getenv('BINAPPS_CREDIT_NOTE_URL'))
        
        self.scope = 'EDocumentsWebApi.write'

    def _get_tenant_id(self):
        """
        Retorna el Tenant ID configurado para la API de Binapps desde Oracle.
        """
        tenant_bd = get_system_parameter('BAPP_TEN', '')
        if tenant_bd:
            return tenant_bd
        return os.getenv('BINAPPS_TENANT_ID', '1')

    def _get_access_token(self):
        """
        Obtiene el token Bearer desde caché de Django o solicitando uno fresco a Binapps.
        """
        cache_key = "binapps_access_token"
        token = cache.get(cache_key)
        
        if token:
            return token

        payload = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'client_credentials',
            'scope': self.scope
        }

        try:
            # Petición de autenticación
            response = requests.post(
                self.auth_url,
                data=payload,
                timeout=(5, 15)
            )
            response.raise_for_status()
            
            data = response.json()
            token = data.get('access_token')
            expires_in = data.get('expires_in', 3600)
            
            # Guardamos en caché restándole 60 segundos por precaución
            cache_timeout = max(0, int(expires_in) - 60)
            cache.set(cache_key, token, timeout=cache_timeout)
            
            return token
            
        except requests.RequestException as e:
            logger.error(f"Fallo de red al solicitar token Binapps: {str(e)}")
            raise Exception(f"Binapps Authentication Request Failed: {str(e)}")
        except Exception as e:
            logger.error(f"Error inesperado solicitando token: {str(e)}")
            raise Exception(f"Binapps Authentication Process Failed: {str(e)}")

    def transmit_document(self, payload_dict: dict, is_credit_note=False) -> dict:
        """
        Envía el diccionario JSON a los servidores de la DIAN a través de Binapps.
        Soporta modo 'dry_run' para emitir logs en vez de llamadas web.
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Transmisión FEL simulada a Binapps. Payload: {json.dumps(payload_dict, ensure_ascii=False)}")
            return {
                'State': '30',
                'Status': 'EXITO_SIMULADO',
                'Message': 'Documento procesado correctamente (Modo DRY RUN).',
                'Cufe': 'SIMULATED_CUFE_' + str(payload_dict.get('Number', 'UNKNOWN'))
            }

        url = self.credit_note_url if is_credit_note else self.invoice_url
        
        try:
            token = self._get_access_token()
            
            headers = {
                'Content-Type': 'application/json; charset=UTF-8',
                'Authorization': f'Bearer {token}',
                'TenantId': self._get_tenant_id()
            }
            
            # Se serializan los datos con el wrapper 'json=' de requests, que implícitamente utiliza json.dumps
            response = requests.post(
                url,
                json=payload_dict,
                headers=headers,
                timeout=(5, 25)
            )
            response.raise_for_status()
            
            # Retornar el payload de respuesta entregado por Binapps
            return response.json()
            
        except requests.RequestException as e:
            error_body = e.response.text if e.response is not None else "Sin cuerpo HTTP"
            logger.error(f"Fallo HTTP con Binapps en la URL ({url}): {str(e)} | Body: {error_body}")
            return {
                'State': '99',
                'Status': 'ERROR_API_RED',
                'Message': f'Fallo HTTP - Binapps inalcanzable o en error severo: {str(e)} | Detalles: {error_body}',
                'Cufe': ''
            }
        except Exception as e:
            logger.error(f"Fallo estructural interno en la transmisión de documento FEL: {str(e)}")
            return {
                'State': '99',
                'Status': 'ERROR_INTERNO_SERVIDOR',
                'Message': f'Excepción de software atrapada localmente: {str(e)}',
                'Cufe': ''
            }
