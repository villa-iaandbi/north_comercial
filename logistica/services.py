import traceback
from datetime import datetime
from django.db import transaction, connections
from django.db.models import Sum
from .models import CoEntregas, CoDocumentos
from core.models import CoTipoDocumento

def log_marca(mensaje):
    """
    Invoca el procedimiento MARCA de Oracle para dejar traza de errores 
    en la tabla de logs de la base de datos (legacy DOORS).
    """
    try:
        with connections['default'].cursor() as cursor:
            # Se restringe la longitud del mensaje para no desbordar variables VARCHAR2 típicas
            cursor.callproc("MARCA", [str(mensaje)[:4000]])
    except Exception as e:
        # Falla silenciosa para no interrumpir el flujo principal si el log falla
        print(f"Error al invocar MARCA: {e}")

@transaction.atomic(using='default')
def crear_entrega_logistica(id_sistema, id_transportador, lista_ids_documentos, placa_vehiculo=None, cod_ruta=None, observaciones=None):
    try:
        anio_actual = datetime.now().year
        
        # 1. Obtener consecutivo del documento 'ENTREGAS' a través del SP
        from facturacion.services import obtener_consecutivo
        
        tipo_doc_qs = CoTipoDocumento.objects.using('default').filter(
            nom_tipo_documento='ENTREGAS',
            id_ano=anio_actual
        )
        
        tipo_doc = None
        for td in tipo_doc_qs.iterator():
            tipo_doc = td
            break
            
        if not tipo_doc:
            raise Exception("No se encontró tipo de documento para ENTREGAS en el año actual.")
            
        # El SP se encarga del bloqueo y concurrencia. Devuelve el número visible.
        nuevo_num_entrega, _ = obtener_consecutivo(tipo_doc.id_tipo_documento)
        
        if not nuevo_num_entrega:
            raise Exception("Fallo Crítico: El Procedimiento de Consecutivos retornó NULL para ENTREGAS.")

        # 2. Obtener ID de secuencia Oracle para la Primary Key de CO_ENTREGAS
        with connections['default'].cursor() as cursor:
            cursor.execute("SELECT SEC_ENTREGAS.NEXTVAL FROM DUAL")
            nuevo_id_entrega = cursor.fetchone()[0]

            # 3. Calcular peso invocando PESO_DOC
            total_kg = 0
            for doc_id in lista_ids_documentos:
                cursor.execute("SELECT PESO_DOC(%s) FROM DUAL", [doc_id])
                peso = cursor.fetchone()[0]
                if peso:
                    total_kg += peso

        # 3.5 Calcular totales de la carga (vlr_total) usando SQL crudo para evitar incompatibilidades ORM
        format_strings = ','.join(['%s'] * len(lista_ids_documentos))
        with connections['default'].cursor() as cursor:
            cursor.execute(f"SELECT SUM(TOT_DOCUMENTO) FROM CO_DOCUMENTOS WHERE ID_DOCUMENTO IN ({format_strings})", lista_ids_documentos)
            sum_result = cursor.fetchone()[0]
            total_vlr = sum_result if sum_result else 0

        # 4. Crear cabecera CO_ENTREGAS
        entrega = CoEntregas.objects.using('default').create(
            id_entrega=nuevo_id_entrega,
            id_sistema=id_sistema,
            num_entrega=nuevo_num_entrega,
            id_transportador=id_transportador,
            placa_vehiculo=placa_vehiculo,
            vlr_total_carga=total_vlr,
            total_peso=total_kg,
            cod_ruta=cod_ruta,
            observaciones=observaciones,
            estado=1
        )

        # 5. Vinculación masiva de facturas (asignamos el ID_ENTREGA a los documentos seleccionados) mediante SQL
        with connections['default'].cursor() as cursor:
            cursor.execute(f"UPDATE CO_DOCUMENTOS SET ID_ENTREGA = %s WHERE ID_DOCUMENTO IN ({format_strings})", [nuevo_id_entrega] + list(lista_ids_documentos))

        return entrega

    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            # Atrapar cualquier excepción de DB, volcar el traceback e invocar a MARCA
            error_msg = f"Error crear_entrega_logistica: {str(e)}"
            # log_marca(error_msg) # Temporalmente deshabilitado para no causar TransactionManagementError
        except Exception:
            pass
        raise e
