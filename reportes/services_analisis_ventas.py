# -*- coding: utf-8 -*-
from decimal import Decimal
import logging
from django.db import connection
import pandas as pd

logger = logging.getLogger(__name__)


class VentasClientesAnalyticsService:

    def __init__(self, filtros=None):
        self.filtros = filtros if filtros else {}
        self.where_clauses = []
        self.params = {}

        if self.filtros:
            if self.filtros.get('anio'):
                self.where_clauses.append("VND.ANIO = :anio")
                self.params['anio'] = str(self.filtros['anio'])
            if self.filtros.get('mes'):
                mes_val = str(self.filtros['mes']).zfill(2)
                self.where_clauses.append("VND.MES_NUM = :mes")
                self.params['mes'] = mes_val
            if self.filtros.get('zona_vendedor'):
                self.where_clauses.append("VND.ZONA_VENDEDOR = :zona_vendedor")
                self.params['zona_vendedor'] = str(
                    self.filtros['zona_vendedor'])
            if self.filtros.get('proveedor'):
                self.where_clauses.append("VND.PROVEEDOR = :proveedor")
                self.params['proveedor'] = str(self.filtros['proveedor'])
            if self.filtros.get('linea'):
                self.where_clauses.append("VND.LINEA = :linea")
                self.params['linea'] = str(self.filtros['linea'])
            if self.filtros.get('canal'):
                self.where_clauses.append("VND.CANAL = :canal")
                self.params['canal'] = str(self.filtros['canal'])

            # Incorporación del ADR-014: Filtros Geográficos Normalizados
            if self.filtros.get('departamento'):
                self.where_clauses.append(
                    "TRIM(UPPER(VND.DEPARTAMENTO)) = TRIM(UPPER(:departamento))")
                self.params['departamento'] = str(self.filtros['departamento'])
            if self.filtros.get('municipio'):
                self.where_clauses.append(
                    "TRIM(UPPER(VND.MUNICIPIO)) = TRIM(UPPER(:municipio))")
                self.params['municipio'] = str(self.filtros['municipio'])
            if self.filtros.get('barrio'):
                self.where_clauses.append(
                    "TRIM(UPPER(VND.BARRIO)) = TRIM(UPPER(:barrio))")
                self.params['barrio'] = str(self.filtros['barrio'])

    def _get_where_sql(self):
        if not self.where_clauses:
            return ""
        return "WHERE " + " AND ".join(self.where_clauses)

    def _ejecutar_consulta(self, cursor, query, params=None):
        consulta_params = params if params is not None else self.params
        if consulta_params:
            cursor.execute(query, consulta_params)
        else:
            cursor.execute(query)

    def get_filtros_disponibles(self):
        filtros = {
            'anios': [],
            'vendedores': [],
            'proveedores': [],
            'lineas': [],
            'canales': []
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT ANIO FROM BI_VENTAS_CLIENTE_MENSUAL WHERE ANIO IS NOT NULL ORDER BY ANIO DESC")
                filtros['anios'] = [row[0] for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT ZONA_VENDEDOR FROM BI_VENTAS_CLIENTE_MENSUAL WHERE ZONA_VENDEDOR IS NOT NULL ORDER BY ZONA_VENDEDOR")
                filtros['vendedores'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT PROVEEDOR FROM BI_VENTAS_CLIENTE_MENSUAL WHERE PROVEEDOR IS NOT NULL ORDER BY PROVEEDOR")
                filtros['proveedores'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT LINEA FROM BI_VENTAS_CLIENTE_MENSUAL WHERE LINEA IS NOT NULL ORDER BY LINEA")
                filtros['lineas'] = [{'id': row[0], 'nombre': row[0]}
                                     for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT CANAL FROM BI_VENTAS_CLIENTE_MENSUAL WHERE CANAL IS NOT NULL ORDER BY CANAL")
                filtros['canales'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Error al obtener filtros: %s", e)
        return filtros

    def get_datos_tablero(self):
        where_sql = self._get_where_sql()
        datos = {
            'canal': [],
            'treemap': [],
            'deptos': [],
            'municipios': [],
            'barrios': [],
        }

        try:
            with connection.cursor() as cursor:
                # 1. Canal
                query_canal = f"""
                    SELECT VND.CANAL, SUM(VND.VENTA_NETA) AS VENTA, COUNT(DISTINCT VND.ID_TERCERO) AS QCLIENTES
                    FROM BI_VENTAS_CLIENTE_MENSUAL VND {where_sql}
                    GROUP BY VND.CANAL
                    ORDER BY VENTA DESC
                """
                self._ejecutar_consulta(cursor, query_canal)
                datos['canal'] = [
                    {
                        'CANAL': row[0] or 'SIN CANAL',
                        'VENTA_SIN_IVA': int(round(Decimal(str(row[1] or 0)))),
                        'QCLIENTES': row[2]
                    }
                    for row in cursor.fetchall()
                ]

                # 2. Vendedores (Top)
                filtro_extra = "WHERE VND.ZONA_VENDEDOR IS NOT NULL" if not where_sql else f"{where_sql} AND VND.ZONA_VENDEDOR IS NOT NULL"
                query_treemap = f"""
                    SELECT VND.ZONA_VENDEDOR, SUM(VND.VENTA_NETA) AS VENTA
                    FROM BI_VENTAS_CLIENTE_MENSUAL VND {filtro_extra}
                    GROUP BY VND.ZONA_VENDEDOR
                    ORDER BY VENTA DESC
                """
                self._ejecutar_consulta(cursor, query_treemap)
                datos['treemap'] = [
                    {
                        'name': row[0],
                        'value': int(round(Decimal(str(row[1] or 0))))
                    }
                    for row in cursor.fetchall()
                ]

                # 3. Departamentos
                query_deptos = f"""
                    SELECT NVL(VND.DEPARTAMENTO, 'SIN DEPARTAMENTO') AS DEPTO, 
                           SUM(VND.VENTA_NETA) AS VENTA, 
                           COUNT(DISTINCT VND.ID_TERCERO) AS QCLIENTES
                    FROM BI_VENTAS_CLIENTE_MENSUAL VND {where_sql}
                    GROUP BY NVL(VND.DEPARTAMENTO, 'SIN DEPARTAMENTO')
                    ORDER BY VENTA DESC
                """
                self._ejecutar_consulta(cursor, query_deptos)
                datos['deptos'] = [
                    {
                        'nombre': row[0],
                        'venta': int(round(Decimal(str(row[1] or 0)))),
                        'clientes': row[2]
                    }
                    for row in cursor.fetchall()
                ]

                # 4. Municipios
                query_muni = f"""
                    SELECT NVL(VND.DEPARTAMENTO, 'SIN DEPARTAMENTO') AS DEPTO,
                           NVL(VND.MUNICIPIO, 'SIN MUNICIPIO') AS MUNICIPIO, 
                           SUM(VND.VENTA_NETA) AS VENTA, 
                           COUNT(DISTINCT VND.ID_TERCERO) AS QCLIENTES
                    FROM BI_VENTAS_CLIENTE_MENSUAL VND {where_sql}
                    GROUP BY NVL(VND.DEPARTAMENTO, 'SIN DEPARTAMENTO'), NVL(VND.MUNICIPIO, 'SIN MUNICIPIO')
                    ORDER BY VENTA DESC
                """
                self._ejecutar_consulta(cursor, query_muni)
                datos['municipios'] = [
                    {
                        'depto': row[0],
                        'nombre': row[1],
                        'venta': int(round(Decimal(str(row[2] or 0)))),
                        'clientes': row[3]
                    }
                    for row in cursor.fetchall()
                ]

                # 5. Barrios
                query_barrios = f"""
                    SELECT NVL(VND.DEPARTAMENTO, 'SIN DEPARTAMENTO') AS DEPTO,
                           NVL(VND.MUNICIPIO, 'SIN MUNICIPIO') AS MUNICIPIO,
                           NVL(VND.BARRIO, 'SIN BARRIO') AS BARRIO,
                           SUM(VND.VENTA_NETA) AS VENTA, 
                           COUNT(DISTINCT VND.ID_TERCERO) AS QCLIENTES
                    FROM BI_VENTAS_CLIENTE_MENSUAL VND {where_sql}
                    GROUP BY NVL(VND.DEPARTAMENTO, 'SIN DEPARTAMENTO'), NVL(VND.MUNICIPIO, 'SIN MUNICIPIO'), NVL(VND.BARRIO, 'SIN BARRIO')
                    ORDER BY VENTA DESC
                """
                self._ejecutar_consulta(cursor, query_barrios)
                datos['barrios'] = [
                    {
                        'depto': row[0],
                        'muni': row[1],
                        'nombre': row[2],
                        'venta': int(round(Decimal(str(row[3] or 0)))),
                        'clientes': row[4]
                    }
                    for row in cursor.fetchall()
                ]

        except Exception as e:
            logger.error("Error al obtener datos del tablero: %s", e)

        return datos

    def get_top_clientes(self, limit=10):
        """ Retorna exclusivamente el Top N de clientes, delegando el peso a Oracle. """
        where_sql = self._get_where_sql()

        query_total = f"""
            SELECT SUM(VND.VENTA_NETA) 
            FROM BI_VENTAS_CLIENTE_MENSUAL VND {where_sql}
        """

        query_top = f"""
            SELECT * FROM (
                SELECT NVL(VND.NOM_CLIENTE, 'SIN NOMBRE') AS CLIENTE, 
                       SUM(VND.VENTA_NETA) AS VENTA
                FROM BI_VENTAS_CLIENTE_MENSUAL VND {where_sql}
                GROUP BY VND.NOM_CLIENTE
                ORDER BY VENTA DESC
            ) WHERE ROWNUM <= {int(limit)}
        """

        total_venta = Decimal('0')
        top_clientes = []

        try:
            with connection.cursor() as cursor:
                self._ejecutar_consulta(cursor, query_total)
                row_total = cursor.fetchone()
                if row_total and row_total[0]:
                    total_venta = Decimal(str(row_total[0]))

                self._ejecutar_consulta(cursor, query_top)
                for row in cursor.fetchall():
                    venta_cliente = Decimal(str(row[1] or 0))
                    top_clientes.append({
                        'cliente': row[0],
                        'venta': float(venta_cliente),
                    })

        except Exception as e:
            logger.error("Error al obtener top clientes: %s", e)

        return top_clientes, float(total_venta)

    def get_matriz_pivot_mensual(self, offset=0, limit=50):
        where_matriz = [
            c for c in self.where_clauses if not c.startswith('VND.MES_NUM')]
        where_sql = ("WHERE " + " AND ".join(where_matriz)
                     ) if where_matriz else ""
        params_matriz = {k: v for k, v in self.params.items() if k != 'mes'}

        # Paginación nativa Oracle 11g con subconsultas para evitar colapso de memoria
        query = f"""
            WITH TOP_CLI_ALL AS (
                SELECT VND.NOM_CLIENTE, SUM(VND.VENTA_NETA) AS TOTAL_ANUAL
                FROM BI_VENTAS_CLIENTE_MENSUAL VND
                {where_sql}
                GROUP BY VND.NOM_CLIENTE
                ORDER BY TOTAL_ANUAL DESC
            ),
            PAGINATED_CLI AS (
                SELECT a.*, ROWNUM as rnum FROM (
                    SELECT * FROM TOP_CLI_ALL
                ) a WHERE ROWNUM <= {int(offset + limit)}
            )
            SELECT 
                VND.NOM_CLIENTE AS CLIENTE,
                TO_NUMBER(VND.MES_NUM) AS MES_NUM,
                SUM(VND.VENTA_NETA) AS VENTA
            FROM BI_VENTAS_CLIENTE_MENSUAL VND
            JOIN PAGINATED_CLI pc ON pc.NOM_CLIENTE = VND.NOM_CLIENTE
            {where_sql}
            AND pc.rnum > {int(offset)}
            GROUP BY VND.NOM_CLIENTE, TO_NUMBER(VND.MES_NUM)
        """

        try:
            with connection.cursor() as cursor:
                self._ejecutar_consulta(cursor, query, params=params_matriz)
                rows = cursor.fetchall()
                if not rows:
                    return pd.DataFrame()

                df = pd.DataFrame(
                    rows, columns=['CLIENTE', 'MES_NUM', 'VENTA'])
                df['VENTA'] = df['VENTA'].fillna(0).apply(
                    lambda x: int(round(Decimal(str(x)))))
                df['MES_NUM'] = df['MES_NUM'].astype(int)

            pivot_df = pd.pivot_table(
                df,
                index='CLIENTE',
                columns='MES_NUM',
                values='VENTA',
                aggfunc='sum',
                fill_value=0
            )

            meses_nombres = {
                1: 'Ene', 2: 'Feb', 3: 'Mar', 4: 'Abr',
                5: 'May', 6: 'Jun', 7: 'Jul', 8: 'Ago',
                9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dic'
            }

            for mes in range(1, 13):
                if mes not in pivot_df.columns:
                    pivot_df[mes] = 0

            pivot_df = pivot_df[[m for m in range(1, 13)]]
            pivot_df['Total'] = pivot_df.sum(axis=1)
            pivot_df = pivot_df.sort_values(by='Total', ascending=False)
            pivot_df.rename(columns=meses_nombres, inplace=True)

            return pivot_df

        except Exception as e:
            logger.error("Error al generar la matriz pivot: %s", e)
            return pd.DataFrame()
