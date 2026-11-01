import pandas as pd
from django.db import connection
from datetime import datetime
import numpy as np
import os
from django_q.tasks import async_task
from django_q.models import Task


def ejecutar_plsql_async_task(id_lote):
    """Ejecuta el cruce de la DIAN en segundo plano usando Django-Q2.

    Retorna un dict serializable que Django-Q persiste como `Task.result`,
    de modo que el endpoint de estado pueda informar éxito o error al frontend.
    """
    try:
        with connection.cursor() as cursor:
            # Idempotencia: purgar fantasmas previos del lote ANTES del cruce. Si quedaran vivos,
            # el MERGE los re-cruzaría como EXACTO/DESCUADRE y se insertarían fantasmas nuevos
            # (los contadores crecerían en cada recálculo).
            cursor.execute(
                "DELETE FROM AUDIT_DIAN_DOCS WHERE ID_LOTE = %s "
                "AND (ESTADO_CRUCE = 4 OR TIPO_DOCUMENTO = 'DOCUMENTO_ERP_NO_DIAN')",
                [id_lote])
            cursor.callproc('P_CONCILIAR_LOTE_DIAN', [id_lote])
            connection.commit()
        print(f"Lote {id_lote} conciliado exitosamente en segundo plano.")
        return {"ok": True}
    except Exception as e:
        print(f"Error conciliando lote {id_lote} en segundo plano: {str(e)}")
        return {"ok": False, "error": str(e)}


def parsear_fechas_dian(serie):
    """Convierte fechas de la DIAN (dd-mm-yyyy o dd-mm-yyyy HH:MM:SS, o datetimes
    nativos de Excel) a datetime64, interpretando siempre el día primero."""
    resultado = pd.to_datetime(
        serie, format='%d-%m-%Y %H:%M:%S', errors='coerce')
    for formato in ('%d-%m-%Y', '%d/%m/%Y %H:%M:%S', '%d/%m/%Y'):
        pendientes = resultado.isna() & serie.notna()
        if not pendientes.any():
            return resultado
        resultado[pendientes] = pd.to_datetime(
            serie[pendientes], format=formato, errors='coerce')
    pendientes = resultado.isna() & serie.notna()
    if pendientes.any():
        resultado[pendientes] = pd.to_datetime(
            serie[pendientes], dayfirst=True, errors='coerce')
    return resultado


class ConciliacionDianService:
    def __init__(self, nit_empresa=None):
        if not nit_empresa:
            nit_empresa = os.environ.get('NIT_EMPRESA', '900123456')
        self.nit_empresa = str(nit_empresa).strip()

    def procesar_excel_dian(self, file_obj, nom_lote, usuario):
        try:
            df = pd.read_excel(file_obj, engine='openpyxl')
            # Limpieza extrema de nombres de columnas: sin espacios extra y en mayúsculas
            df.columns = [str(c).strip().upper().replace('  ', ' ')
                          for c in df.columns]
            df = df.replace({np.nan: None})

            # 1. Mapeo Dinámico de Columnas (Tolerancia a variaciones del Excel DIAN)
            def encontrar_columna(opciones):
                for op in opciones:
                    if op in df.columns:
                        return op
                return None

            col_fecha = encontrar_columna([
                'FECHA EMISIÓN', 'FECHA EMISION', 
                'FECHA DE EMISIÓN', 'FECHA DE EMISION', 
                'FECHA DE EXPEDICIÓN', 'FECHA DE EXPEDICION', 
                'FECHA'
            ])
            col_nit_emisor = encontrar_columna(
                ['NIT DEL EMISOR', 'NIT EMISOR'])
            col_nit_receptor = encontrar_columna(
                ['NIT DEL RECEPTOR', 'NIT RECEPTOR'])
            col_cufe = encontrar_columna(['CUFE', 'CUFE/CUDE', 'CUFE_CUDE'])
            col_tipo = encontrar_columna(
                ['TIPO DE DOCUMENTO', 'TIPO DOCUMENTO'])
            col_nombre_emisor = encontrar_columna(
                ['NOMBRE DEL EMISOR', 'NOMBRE EMISOR'])
            col_nombre_receptor = encontrar_columna(
                ['NOMBRE DEL RECEPTOR', 'NOMBRE RECEPTOR'])

            # Excluir documentos que no se concilian (Punto 3)
            if col_tipo:
                # Exclusión case-insensitive y tolerante a tildes
                tipo_norm = (df[col_tipo].astype(str)
                             .str.normalize('NFKD')
                             .str.encode('ascii', errors='ignore')
                             .str.decode('ascii'))
                df = df[~tipo_norm.str.contains(
                    r'NOMINA|APPLICATION\s*RESPONSE',
                    case=False, na=False, regex=True)]

            # 2. Extracción y Limpieza Vectorizada
            if col_fecha:
                df['FECHA_DOC_CLEAN'] = parsear_fechas_dian(df[col_fecha])
                # Extraer min/max ignorando los NaT
                fechas_validas = df['FECHA_DOC_CLEAN'].dropna()
                fecha_inicio = fechas_validas.min().to_pydatetime(
                ) if not fechas_validas.empty else datetime.now()
                fecha_fin = fechas_validas.max().to_pydatetime(
                ) if not fechas_validas.empty else datetime.now()
                # Convertir la columna para Oracle (None en lugar de NaT)
                df['FECHA_DOC_CLEAN'] = df['FECHA_DOC_CLEAN'].apply(
                    lambda x: x.to_pydatetime() if pd.notna(x) else None)
            else:
                fecha_inicio = datetime.now()
                fecha_fin = datetime.now()
                df['FECHA_DOC_CLEAN'] = None

            # NITs y Grupos
            df['NIT_EMISOR_CLEAN'] = df[col_nit_emisor].astype(str).str.replace(
                r'\.0$', '', regex=True) if col_nit_emisor else ''
            df['NIT_RECEPTOR_CLEAN'] = df[col_nit_receptor].astype(str).str.replace(
                r'\.0$', '', regex=True) if col_nit_receptor else ''
            df['GRUPO_FISCAL'] = np.where(
                df['NIT_EMISOR_CLEAN'] == self.nit_empresa, 'EMITIDO', 'RECIBIDO')

            total_emitidos = int((df['GRUPO_FISCAL'] == 'EMITIDO').sum())
            total_recibidos = int((df['GRUPO_FISCAL'] == 'RECIBIDO').sum())

            # Totales
            df['TOTAL_CLEAN'] = pd.to_numeric(
                df.get('TOTAL', 0), errors='coerce').fillna(0)
            df['IVA_CLEAN'] = pd.to_numeric(
                df.get('IVA', 0), errors='coerce').fillna(0)

            with connection.cursor() as cursor:
                cursor.execute("SELECT SEQ_AUDIT_LOTES.NEXTVAL FROM DUAL")
                id_lote = cursor.fetchone()[0]

                cursor.execute("""
                    INSERT INTO AUDIT_DIAN_LOTES (
                        ID_LOTE, NOM_LOTE, FECHA_INICIO_ARCHIVO, FECHA_FIN_ARCHIVO, 
                        TOTAL_EMITIDOS, TOTAL_RECIBIDOS, USUARIO_CARGA, ESTADO_LOTE
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, 'ABIERTO'
                    )
                """, [id_lote, nom_lote, fecha_inicio, fecha_fin, total_emitidos, total_recibidos, usuario])

                # Creación masiva y rápida de tuplas usando mapeo seguro
                datos_insercion = [
                    (
                        id_lote,
                        str(row.get(col_tipo) or '')[:100] if col_tipo else '',
                        str(row.get(col_cufe) or '')[:150] if col_cufe else '',
                        str(row.get('PREFIJO') or '')[:10],
                        str(row.get('FOLIO') or '')[:50],
                        row.get('FECHA_DOC_CLEAN'),
                        str(row.get('NIT_EMISOR_CLEAN') or '')[:30],
                        str(row.get(col_nombre_emisor) or '')[
                            :250] if col_nombre_emisor else '',
                        str(row.get('NIT_RECEPTOR_CLEAN') or '')[:30],
                        str(row.get(col_nombre_receptor) or '')[
                            :250] if col_nombre_receptor else '',
                        float(row.get('IVA_CLEAN', 0)),
                        float(row.get('TOTAL_CLEAN', 0)),
                        row.get('GRUPO_FISCAL')
                    )
                    for row in df.to_dict('records')
                ]

                cursor.executemany("""
                    INSERT INTO AUDIT_DIAN_DOCS (
                        ID_LOTE, TIPO_DOCUMENTO, CUFE_CUDE, PREFIJO, FOLIO, FECHA_EMISION,
                        NIT_EMISOR, NOMBRE_EMISOR, NIT_RECEPTOR, NOMBRE_RECEPTOR,
                        IVA, BASE, TOTAL, GRUPO_FISCAL
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s
                    )
                """, datos_insercion)
                connection.commit()

            # Delegar el cruce pesado al worker asíncrono usando la función global
            async_task(ejecutar_plsql_async_task, id_lote)

            return {"status": "success", "id_lote": id_lote, "mensaje": f"Archivo cargado. El cruce fiscal del lote {id_lote} se está procesando en segundo plano."}

        except Exception as e:
            return {"status": "error", "mensaje": str(e)}

    def get_lotes(self):
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT ID_LOTE, NOM_LOTE, 
                           TO_CHAR(FECHA_INICIO_ARCHIVO, 'YYYY-MM-DD') AS FECHA_INICIO,
                           TO_CHAR(FECHA_FIN_ARCHIVO, 'YYYY-MM-DD') AS FECHA_FIN,
                           TOTAL_EMITIDOS, TOTAL_RECIBIDOS, ESTADO_LOTE
                    FROM AUDIT_DIAN_LOTES
                    ORDER BY ID_LOTE DESC
                """)
                columnas = [col[0].lower() for col in cursor.description]
                lotes = [dict(zip(columnas, fila))
                         for fila in cursor.fetchall()]
            return {"status": "success", "lotes": lotes}
        except Exception as e:
            return {"status": "error", "mensaje": str(e)}

    def get_detalle_lote_paginado(self, id_lote, page=1, limit=100, estado=None, grupo=None, tipo_doc=None):
        try:
            offset = (page - 1) * limit
            limite_superior = offset + limit

            kpis = {
                'exactos': {'cantidad': 0, 'valor_dian': 0, 'valor_erp': 0},
                'descuadres': {'cantidad': 0, 'valor_dian': 0, 'valor_erp': 0},
                'faltantes': {'cantidad': 0, 'valor_dian': 0, 'valor_erp': 0},
                'fantasmas': {'cantidad': 0, 'valor_dian': 0, 'valor_erp': 0},
                # Resumen fiscal DIAN (excluye fantasmas: no existen en la DIAN)
                'resumen_fiscal': {
                    'ventas': {'base': 0, 'iva': 0},
                    'devol_ventas': {'base': 0, 'iva': 0},
                    'compras': {'base': 0, 'iva': 0},
                    'devol_compras': {'base': 0, 'iva': 0},
                },
            }

            with connection.cursor() as cursor:
                # 1. Consulta atómica de KPIs (Tarjetas Superiores) con JOIN a ERP
                cursor.execute("""
                    SELECT D.ESTADO_CRUCE, 
                           COUNT(*), 
                           SUM(CASE WHEN D.ESTADO_CRUCE = 4 THEN 0 ELSE NVL(D.TOTAL, 0) END),
                           SUM(CASE WHEN D.ESTADO_CRUCE = 4 THEN NVL(D.TOTAL, 0) ELSE NVL(C.TOT_DOCUMENTO, 0) END)
                    FROM AUDIT_DIAN_DOCS D
                    LEFT JOIN CO_DOCUMENTOS C ON D.ID_DOC_ERP = C.ID_DOCUMENTO
                    WHERE D.ID_LOTE = %s
                    GROUP BY D.ESTADO_CRUCE
                """, [id_lote])

                for row in cursor.fetchall():
                    estado_kpi = row[0]  # no reutilizar 'estado': es el filtro de la consulta paginada
                    cant = row[1]
                    val_dian = row[2]
                    val_erp = row[3]

                    if estado_kpi == 1:
                        kpis['exactos'] = {'cantidad': cant, 'valor_dian': val_dian, 'valor_erp': val_erp}
                    elif estado_kpi == 2:
                        kpis['descuadres'] = {'cantidad': cant, 'valor_dian': val_dian, 'valor_erp': val_erp}
                    elif estado_kpi == 3:
                        kpis['faltantes'] = {'cantidad': cant, 'valor_dian': val_dian, 'valor_erp': val_erp}
                    elif estado_kpi == 4:
                        kpis['fantasmas'] = {'cantidad': cant, 'valor_dian': val_dian, 'valor_erp': val_erp}

                # 1b. Resumen fiscal DIAN del lote (estado_cruce != 4) en 4 cuadrantes.
                # BASE se calcula como TOTAL - IVA (la columna BASE se carga en 0 al importar el Excel).
                # Nota Crédito: tipo de documento con 'NOTA ... CREDITO' / 'NOTA CR' o la sigla 'NC'.
                cursor.execute("""
                    SELECT GRUPO_FISCAL, ES_NC,
                           NVL(SUM(NVL(TOTAL, 0) - NVL(IVA, 0)), 0),
                           NVL(SUM(NVL(IVA, 0)), 0)
                    FROM (
                        SELECT GRUPO_FISCAL, TOTAL, IVA,
                               CASE WHEN REGEXP_LIKE(UPPER(TIPO_DOCUMENTO),
                                         'NOTA.*CR.DITO|NOTA CR|(^|[^A-Z])NC($|[^A-Z])')
                                    THEN 1 ELSE 0 END AS ES_NC
                        FROM AUDIT_DIAN_DOCS
                        WHERE ID_LOTE = %s AND NVL(ESTADO_CRUCE, 0) <> 4
                    )
                    GROUP BY GRUPO_FISCAL, ES_NC
                """, [id_lote])

                cuadrantes = {
                    ('EMITIDO', 0): 'ventas',
                    ('EMITIDO', 1): 'devol_ventas',
                    ('RECIBIDO', 0): 'compras',
                    ('RECIBIDO', 1): 'devol_compras',
                }
                for grupo_f, es_nc, base_f, iva_f in cursor.fetchall():
                    clave = cuadrantes.get((grupo_f, es_nc))
                    if clave:
                        kpis['resumen_fiscal'][clave] = {'base': base_f, 'iva': iva_f}

                # 2. Consulta Paginada compatible con Oracle 11g
                query_base = """
                    SELECT D.ID_DOC, D.TIPO_DOCUMENTO, D.PREFIJO, D.FOLIO, 
                           TO_CHAR(D.FECHA_EMISION, 'YYYY-MM-DD') AS FECHA_EMISION,
                           D.NIT_EMISOR, D.NOMBRE_EMISOR, D.NIT_RECEPTOR, D.NOMBRE_RECEPTOR,
                           (CASE WHEN D.ESTADO_CRUCE = 4 THEN 0 ELSE NVL(D.TOTAL, 0) END) AS TOTAL, 
                           (CASE WHEN D.ESTADO_CRUCE = 4 THEN NVL(D.TOTAL, 0) ELSE NVL(C.TOT_DOCUMENTO, 0) END) AS TOTAL_ERP, 
                           ((CASE WHEN D.ESTADO_CRUCE = 4 THEN 0 ELSE NVL(D.TOTAL, 0) END) - (CASE WHEN D.ESTADO_CRUCE = 4 THEN NVL(D.TOTAL, 0) ELSE NVL(C.TOT_DOCUMENTO, 0) END)) AS DIFERENCIA,
                           (CASE WHEN D.ESTADO_CRUCE = 4 THEN 0 ELSE NVL(D.IVA, 0) END) AS IVA,
                           (CASE WHEN D.ESTADO_CRUCE = 4 THEN NVL(D.IVA, 0)
                                 ELSE NVL((SELECT MAX(B.IVA_ERP) FROM V_ERP_DIAN_BRIDGE B
                                            WHERE B.ID_DOCUMENTO = D.ID_DOC_ERP), 0) END) AS IVA_ERP,
                           D.GRUPO_FISCAL, D.ESTADO_CRUCE
                    FROM AUDIT_DIAN_DOCS D
                    LEFT JOIN CO_DOCUMENTOS C ON D.ID_DOC_ERP = C.ID_DOCUMENTO
                    WHERE D.ID_LOTE = %s
                """
                params_base = [id_lote]

                if estado is not None:
                    # Filtro estricto por el estado que calculó Oracle; sin comparar montos en Python/SQL
                    query_base += " AND D.ESTADO_CRUCE = %s"
                    params_base.append(estado)
                if grupo and grupo != 'TODOS':
                    query_base += " AND D.GRUPO_FISCAL = %s"
                    params_base.append(grupo)
                if tipo_doc and tipo_doc != 'TODOS':
                    query_base += " AND UPPER(D.TIPO_DOCUMENTO) LIKE UPPER(%s)"
                    params_base.append(f"%{tipo_doc}%")

                query_base += " ORDER BY D.ESTADO_CRUCE ASC, D.ID_DOC ASC"

                query_paginada = f"""
                    SELECT * FROM (
                        SELECT a.*, ROWNUM rnum FROM (
                            {query_base}
                        ) a WHERE ROWNUM <= %s
                    ) WHERE rnum > %s
                """
                params_paginada = params_base + [limite_superior, offset]
                cursor.execute(query_paginada, params_paginada)

                columnas = [col[0].lower() for col in cursor.description]
                # Omitir la columna virtual 'rnum' generada por la paginación de Oracle
                documentos = [
                    {k: v for k, v in zip(columnas, fila) if k != 'rnum'}
                    for fila in cursor.fetchall()
                ]

            return {
                "status": "success",
                "kpis": kpis,
                "documentos": documentos,
                "has_more": len(documentos) == limit,
                "page": page
            }
        except Exception as e:
            return {"status": "error", "mensaje": str(e)}

    def get_detalle_lote(self, id_lote):
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT ID_DOC, TIPO_DOCUMENTO, PREFIJO, FOLIO, 
                           TO_CHAR(FECHA_EMISION, 'YYYY-MM-DD') AS FECHA_EMISION,
                           NIT_EMISOR, NOMBRE_EMISOR, NIT_RECEPTOR, NOMBRE_RECEPTOR,
                           NVL(TOTAL, 0) AS TOTAL, NVL(TOTAL_ERP, 0) AS TOTAL_ERP, 
                           (NVL(TOTAL, 0) - NVL(TOTAL_ERP, 0)) AS DIFERENCIA,
                           GRUPO_FISCAL, ESTADO_CRUCE
                    FROM AUDIT_DIAN_DOCS
                    WHERE ID_LOTE = %s
                    ORDER BY ID_DOC ASC
                """, [id_lote])

                columnas = [col[0].lower() for col in cursor.description]
                documentos = [dict(zip(columnas, fila))
                              for fila in cursor.fetchall()]

            return {"status": "success", "documentos": documentos}
        except Exception as e:
            return {"status": "error", "mensaje": str(e)}

    def recalcular_lote(self, id_lote):
        try:
            # El recálculo manual se hace en segundo plano (Django-Q2).
            # Se retorna el task_id para que el frontend consulte el avance (polling).
            task_id = async_task(ejecutar_plsql_async_task, id_lote)
            return {
                "status": "processing",
                "task_id": task_id,
                "mensaje": "Recálculo iniciado en segundo plano."
            }
        except Exception as e:
            return {"status": "error", "mensaje": str(e)}

    def consultar_estado_recalculo(self, id_lote, task_id):
        """Consulta en Django-Q2 si la tarea de recálculo ya terminó.

        Retorna status: 'processing' (aún en cola/ejecución), 'finished' o 'error'.
        Django-Q2 solo persiste la Task cuando finaliza, por lo que su ausencia
        equivale a 'processing'.
        """
        try:
            tarea = Task.get_task(task_id)
            if tarea is None:
                return {"status": "processing", "task_id": task_id}

            # La tarea debe corresponder al lote consultado
            if tuple(tarea.args or ()) != (id_lote,):
                return {"status": "error", "task_id": task_id,
                        "mensaje": "La tarea no corresponde al lote indicado."}

            resultado = tarea.result
            if tarea.success and isinstance(resultado, dict) and resultado.get("ok") is False:
                return {"status": "error", "task_id": task_id,
                        "mensaje": resultado.get("error") or "Error en el recálculo."}
            if not tarea.success:
                return {"status": "error", "task_id": task_id,
                        "mensaje": str(resultado)}
            return {"status": "finished", "task_id": task_id}
        except Exception as e:
            return {"status": "error", "task_id": task_id, "mensaje": str(e)}

    def cerrar_lote(self, id_lote):
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE AUDIT_DIAN_LOTES SET ESTADO_LOTE = 'CERRADO' WHERE ID_LOTE = %s", [id_lote])
                connection.commit()
            return {"status": "success", "mensaje": "Lote cerrado irrevocablemente."}
        except Exception as e:
            return {"status": "error", "mensaje": str(e)}
