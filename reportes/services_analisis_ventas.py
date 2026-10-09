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
                self.where_clauses.append("v.ANIO = :anio")
                self.params['anio'] = str(self.filtros['anio'])
            if self.filtros.get('mes'):
                mes_val = str(self.filtros['mes']).zfill(2)
                self.where_clauses.append("v.MES_NUM = :mes")
                self.params['mes'] = mes_val
            if self.filtros.get('zona_vendedor'):
                self.where_clauses.append("v.ZONA_VENDEDOR = :zona_vendedor")
                self.params['zona_vendedor'] = str(
                    self.filtros['zona_vendedor'])
            if self.filtros.get('proveedor'):
                self.where_clauses.append("v.PROVEEDOR = :proveedor")
                self.params['proveedor'] = str(self.filtros['proveedor'])
            if self.filtros.get('linea'):
                self.where_clauses.append("v.LINEA = :linea")
                self.params['linea'] = str(self.filtros['linea'])
            if self.filtros.get('canal'):
                self.where_clauses.append("v.CANAL = :canal")
                self.params['canal'] = str(self.filtros['canal'])

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
                    "SELECT DISTINCT ANIO FROM BI_VENTAS_CLIENTE_MENSUAL WHERE ANIO IS NOT NULL ORDER BY ANIO DESC"
                )
                filtros['anios'] = [row[0] for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT ZONA_VENDEDOR FROM BI_VENTAS_CLIENTE_MENSUAL WHERE ZONA_VENDEDOR IS NOT NULL ORDER BY ZONA_VENDEDOR"
                )
                filtros['vendedores'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT PROVEEDOR FROM BI_VENTAS_CLIENTE_MENSUAL WHERE PROVEEDOR IS NOT NULL ORDER BY PROVEEDOR"
                )
                filtros['proveedores'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT LINEA FROM BI_VENTAS_CLIENTE_MENSUAL WHERE LINEA IS NOT NULL ORDER BY LINEA"
                )
                filtros['lineas'] = [{'id': row[0], 'nombre': row[0]}
                                     for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT CANAL FROM BI_VENTAS_CLIENTE_MENSUAL WHERE CANAL IS NOT NULL ORDER BY CANAL"
                )
                filtros['canales'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Error al obtener filtros: %s", e)
        return filtros

    def get_datos_tablero(self):
        where_sql = self._get_where_sql()
        datos = {
            'canal': [],
            'municipios': [],
            'treemap': []
        }

        try:
            with connection.cursor() as cursor:
                # 1. Canal desde BI_VENTAS_CLIENTE_MENSUAL
                query_canal = f"""
                    SELECT CANAL, SUM(VENTA_NETA) AS VENTA, COUNT(DISTINCT ID_TERCERO) AS QCLIENTES
                    FROM BI_VENTAS_CLIENTE_MENSUAL v {where_sql}
                    GROUP BY CANAL
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

                # 2. Treemap Vendedores desde BI_VENTAS_CLIENTE_MENSUAL
                filtro_extra = "WHERE v.ZONA_VENDEDOR IS NOT NULL" if not where_sql else f"{where_sql} AND v.ZONA_VENDEDOR IS NOT NULL"
                query_treemap = f"""
                    SELECT ZONA_VENDEDOR, SUM(VENTA_NETA) AS VENTA
                    FROM BI_VENTAS_CLIENTE_MENSUAL v {filtro_extra}
                    GROUP BY ZONA_VENDEDOR
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

                # 3. Municipios Top 20 (desde BI_VENTASNETAS)
                query_municipios = f"""
                    SELECT MUNICIPIO, SUM(VENTA_SIN_IVA) AS VENTA, COUNT(DISTINCT ID_TERCERO) AS QCLIENTES
                    FROM BI_VENTASNETAS v {where_sql}
                    GROUP BY MUNICIPIO
                    ORDER BY VENTA DESC
                """
                self._ejecutar_consulta(cursor, query_municipios)
                datos['municipios'] = [
                    {
                        'MUNICIPIO': row[0] or 'SIN MUNICIPIO',
                        'VENTA_SIN_IVA': int(round(Decimal(str(row[1] or 0)))),
                        'QCLIENTES': row[2]
                    }
                    for row in cursor.fetchall()[:20]
                ]

        except Exception as e:
            logger.error("Error al obtener datos del tablero: %s", e)

        return datos

    def get_matriz_pivot_mensual(self):
        where_matriz = [
            c for c in self.where_clauses if not c.startswith('v.MES_NUM')]
        where_sql = ("WHERE " + " AND ".join(where_matriz)
                     ) if where_matriz else ""
        params_matriz = {k: v for k, v in self.params.items() if k != 'mes'}

        query = f"""
            WITH TOP_CLI AS (
                SELECT * FROM (
                    SELECT v.NOM_CLIENTE, SUM(v.VENTA_NETA) AS TOTAL_ANUAL
                    FROM BI_VENTAS_CLIENTE_MENSUAL v
                    {where_sql}
                    GROUP BY v.NOM_CLIENTE
                    ORDER BY TOTAL_ANUAL DESC
                ) WHERE ROWNUM <= 50
            )
            SELECT 
                v.NOM_CLIENTE AS CLIENTE,
                TO_NUMBER(v.MES_NUM) AS MES_NUM,
                SUM(v.VENTA_NETA) AS VENTA
            FROM BI_VENTAS_CLIENTE_MENSUAL v
            JOIN TOP_CLI tc ON tc.NOM_CLIENTE = v.NOM_CLIENTE
            {where_sql}
            GROUP BY v.NOM_CLIENTE, TO_NUMBER(v.MES_NUM)
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
            pivot_df.loc['Total General'] = pivot_df.sum()

            return pivot_df

        except Exception as e:
            logger.error("Error al generar la matriz pivot: %s", e)
            return pd.DataFrame()
