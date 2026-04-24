from django.core.management.base import BaseCommand
import traceback
from services.invoice_orchestrator import InvoiceOrchestrator

class Command(BaseCommand):
    help = 'E2E Test: Genera el DTO y Ejecuta el transductor de Facturación Electrónica vía Binapps.'

    def add_arguments(self, parser):
        parser.add_argument(
            'id_documento', 
            type=str, 
            help='ID (String) interno del documento de Oracle a emitir (ej. FAC-1234 o RECA-9092).'
        )
        parser.add_argument(
            '--live',
            action='store_true',
            help='Ejecuta la transmisión HTTP hacia el servidor real de DIAN/Binapps desactivando el DRY_RUN protector.',
        )

    def handle(self, *args, **options):
        id_doc = options['id_documento']
        is_live = options['live']
        
        # El dry run is True a menos que nos pasen el flag --live intencionalmente
        dry_run_state = not is_live
        
        self.stdout.write(self.style.WARNING("==========================================="))
        self.stdout.write(self.style.WARNING("--- INICIANDO FLUJO FEL E2E (ORQUESTADOR) ---"))
        self.stdout.write(self.style.WARNING("==========================================="))
        self.stdout.write(f"\n> Identificador Objetivo : {id_doc}")
        self.stdout.write(f"> Entorno Petición APi   : {'LIVE API (Peligro, Afectación Fiscal Real)' if is_live else 'DRY RUN (Simulación y Auditoría Restringida)'}\n")
        
        self.stdout.write("Iniciando proceso para documento...")
        
        try:
            # 1. Empujar contra el orquestador
            result = InvoiceOrchestrator.process_electronic_invoice(id_doc, dry_run=dry_run_state)
            
            # 2. Análisis del output del diccionario de red
            if result.get("success"):
                self.stdout.write(self.style.SUCCESS(f"\n[OK] Extracción y Payload generado exitosamente."))
                self.stdout.write(self.style.SUCCESS(f"[OK] Respuesta Proveedor Tecnológico recibida: Estado {result.get('api_state')} - '{result.get('message')}'"))
                
                cufe = result.get('cufe')
                if cufe:
                    self.stdout.write(self.style.SUCCESS(f"[OK] CUFE DIAN: {cufe}"))
                     
                self.stdout.write(self.style.SUCCESS(f"[OK] Transacción de acuse y log guardada en base de datos local (CT_VENTAS_FEL)."))
            else:
                error_msg = result.get("error") or result.get("message")
                self.stdout.write(self.style.ERROR(f"\n[X] Fallo Extracción/Procesamiento. Estado de Red: {result.get('api_state', 'N/A')}"))
                self.stdout.write(self.style.ERROR(f"[X] Detalle del Error: {error_msg}"))
                self.stdout.write(self.style.WARNING(f"[!] Se guardó la transacción de fallback de error en la base de datos para monitoreo."))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n[X] CRASH DEL COMANDO INTERNO AL INVOCAR LA CARGA:"))
            self.stdout.write(self.style.ERROR(str(e)))
            self.stdout.write(traceback.format_exc())
            
        self.stdout.write("\nFin de Operación.\n")
