import traceback
from django.core.management.base import BaseCommand
from core.oracle_client import OracleClient

class Command(BaseCommand):
    help = 'Prueba la conexión a la base de datos Oracle 11g configurada'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("Iniciando prueba de conexión a Oracle 11g..."))
        
        try:
            client = OracleClient()
            self.stdout.write(f"-> Conectando a DSN: {client.dsn} con usuario: {client.user}")
            
            # Intentar ejecutar el query simple
            result = client.execute_query("SELECT SYSDATE FROM DUAL")
            
            if result:
                server_time = list(result[0].values())[0] if result[0] else None
                self.stdout.write(self.style.SUCCESS(f"¡Conexión exitosa! SYSDATE devuelto por Oracle: {server_time}"))
            else:
                self.stdout.write(self.style.ERROR("La consulta no devolvió resultados (raro para DUAL)."))
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error durante la prueba de conexión a Oracle:"))
            self.stdout.write(self.style.ERROR(str(e)))
            self.stdout.write("\nDetalle técnico del error:")
            self.stdout.write(traceback.format_exc())
