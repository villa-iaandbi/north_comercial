# -*- coding: utf-8 -*-
import pandas as pd
from django.db import connection
from datetime import datetime
import numpy as np


class ConciliacionDianService:
    def __init__(self, nit_empresa=''):
        # NIT propio de la compañía para determinar GRUPO_FISCAL (Emitido vs Recibido)
        self.nit_empresa = str(nit_empresa).strip()

    def procesar_excel_dian(self, file_obj, nom_lote, usuario):
        try:
            df = pd.read_excel(file_obj, engine='openpyxl')
            df.columns = [str(c).strip().upper() for c in df.columns]
            df = df.replace({np.nan: None})

            col_fecha = 'FECHA DE EMISIÓN' if 'FECHA DE EMISIÓN' in df.columns else 'FECHA EMISION'
            if col_fecha in df.columns:
                df[col_fecha] = pd.to_datetime(df[col_fecha], errors='coerce')
                fecha_inicio = df[col_fecha].min()
                fecha_fin = df[col_fecha].max()
                
                # Prevenir error de inserción de NaT en Oracle
                fecha_inicio = fecha_inicio.to_pydatetime() if pd.notna(fecha_inicio) else None
                fecha_fin = fecha_fin.to_pydatetime() if pd.notna(fecha_fin) else None
            else:
                fecha_inicio = datetime.now()
                fecha_fin = datetime.now()

            col_nit_emisor = 'NIT DEL EMISOR' if 'NIT DEL EMISOR' in df.columns else 'NIT EMISOR'
            df['NIT_EMISOR_CLEAN'] = df.get(col_nit_emisor, pd.Series()).astype(str).str.replace(r'\.0$', '', regex=True)
            df['GRUPO_FISCAL'] = np.where(df['NIT_EMISOR_CLEAN'] == self.nit_empresa, 'EMITIDO', 'RECIBIDO')
            
            total_emitidos = int((df['GRUPO_FISCAL'] == 'EMITIDO').sum())
            total_recibidos = int((df['GRUPO_FISCAL'] == 'RECIBIDO').sum())

            with connection.cursor() as cursor:
                cursor.execute("SELECT SEQ_AUDIT_LOTES.NEXTVAL FROM DUAL")
                id_lote = cursor.fetchone()[0]

                # CORRECCIÓN: Uso de %s en lugar de :1, :2 para compatibilidad con el wrapper de Django
                cursor.execute("""
                    INSERT INTO AUDIT_DIAN_LOTES (
                        ID_LOTE, NOM_LOTE, FECHA_INICIO_ARCHIVO, FECHA_FIN_ARCHIVO, 
                        TOTAL_EMITIDOS, TOTAL_RECIBIDOS, USUARIO_CARGA, ESTADO_LOTE
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, 'ABIERTO'
                    )
                """, [id_lote, nom_lote, fecha_inicio, fecha_fin, total_emitidos, total_recibidos, usuario])

                # OPTIMIZACIÓN: Crear la lista de inserción sin usar iterrows()
                # Extraemos los datos a diccionarios o listas para mayor velocidad
                
                # Pre-procesar columnas conflictivas para que no generen error en la tupla
                df['NIT_RECEPTOR_CLEAN'] = df.get('NIT DEL RECEPTOR', df.get('NIT RECEPTOR', pd.Series('', index=df.index))).astype(str).str.replace(r'\.0$', '', regex=True)
                if col_fecha in df.columns:
                    df['FECHA_DOC_CLEAN'] = pd.to_datetime(df[col_fecha], errors='coerce').apply(lambda x: x.to_pydatetime() if pd.notna(x) else None)
                else:
                    df['FECHA_DOC_CLEAN'] = None
                
                # Llenar nulos numéricos con 0
                df['TOTAL_CLEAN'] = pd.to_numeric(df.get('TOTAL', pd.Series(0, index=df.index)), errors='coerce').fillna(0)
                df['IVA_CLEAN'] = pd.to_numeric(df.get('IVA', pd.Series(0, index=df.index)), errors='coerce').fillna(0)

                # Construir la lista de tuplas masivamente
                datos_insercion = [
                    (
                        id_lote,
                        str(row.get('TIPO DE DOCUMENTO', ''))[:100],
                        str(row.get('CUFE', ''))[:150],
                        str(row.get('PREFIJO', ''))[:10],
                        str(row.get('FOLIO', ''))[:50],
                        row.get('FECHA_DOC_CLEAN'),
                        str(row.get('NIT_EMISOR_CLEAN', ''))[:30],
                        str(row.get('NOMBRE DEL EMISOR', ''))[:250],
                        str(row.get('NIT_RECEPTOR_CLEAN', ''))[:30],
                        str(row.get('NOMBRE DEL RECEPTOR', ''))[:250],
                        float(row.get('IVA_CLEAN')),
                        float(row.get('TOTAL_CLEAN')),
                        row.get('GRUPO_FISCAL')
                    )
                    for row in df.to_dict('records') # to_dict('records') es mucho más rápido que iterrows()
                ]

                # CORRECCIÓN: Uso de %s para executemany en Django
                cursor.executemany("""
                    INSERT INTO AUDIT_DIAN_DOCS (
                        ID_LOTE, TIPO_DOCUMENTO, CUFE_CUDE, PREFIJO, FOLIO, FECHA_EMISION,
                        NIT_EMISOR, NOMBRE_EMISOR, NIT_RECEPTOR, NOMBRE_RECEPTOR,
                        IVA, BASE, TOTAL, GRUPO_FISCAL
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s
                    )
                """, datos_insercion)

                cursor.callproc('P_CONCILIAR_LOTE_DIAN', [id_lote])
                connection.commit()

            return {"status": "success", "id_lote": id_lote, "mensaje": f"Lote {id_lote} procesado con éxito."}

        except Exception as e:
            return {"status": "error", "mensaje": str(e)}

    def get_lotes(self):
        try:
            with connection.cursor() as cursor:
                # Se evitan alias de una letra (ADR Oracle)
                cursor.execute("""
                    SELECT ID_LOTE, NOM_LOTE, 
                           TO_CHAR(FECHA_INICIO_ARCHIVO, 'YYYY-MM-DD') AS FECHA_INICIO,
                           TO_CHAR(FECHA_FIN_ARCHIVO, 'YYYY-MM-DD') AS FECHA_FIN,
                           TOTAL_EMITIDOS, TOTAL_RECIBIDOS, ESTADO_LOTE
                    FROM AUDIT_DIAN_LOTES
                    ORDER BY ID_LOTE DESC
                """)
                columnas = [col[0].lower() for col in cursor.description]
                lotes = [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
            return {"status": "success", "lotes": lotes}
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
                documentos = [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
                
            return {"status": "success", "documentos": documentos}
        except Exception as e:
            return {"status": "error", "mensaje": str(e)}

    def recalcular_lote(self, id_lote):
        try:
            with connection.cursor() as cursor:
                cursor.callproc('P_CONCILIAR_LOTE_DIAN', [id_lote])
                connection.commit()
            return {"status": "success", "mensaje": "Lote recalculado exitosamente."}
        except Exception as e:
            return {"status": "error", "mensaje": str(e)}

    def cerrar_lote(self, id_lote):
        try:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE AUDIT_DIAN_LOTES SET ESTADO_LOTE = 'CERRADO' WHERE ID_LOTE = %s", [id_lote])
                connection.commit()
            return {"status": "success", "mensaje": "Lote cerrado irrevocablemente."}
        except Exception as e:
            return {"status": "error", "mensaje": str(e)}
