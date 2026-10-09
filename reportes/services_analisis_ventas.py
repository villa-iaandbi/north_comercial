# -*- coding: utf-8 -*-
import pandas as pd
from decimal import Decimal
from django.db import connection


class VentasClientesAnalyticsService:

    def __init__(self, filtros=None):
        self.filtros = filtros if filtros else {}
        self.where_clauses = []
        self.params = {}

        if self.filtros:
            if self.filtros.get('anio'):
                self.where_clauses.append("v.ANIO = :anio")
                self.params['anio'] = int(self.filtros['anio'])
            if self.filtros.get('mes'):
                self.where_clauses.append("v.MES_NUM = :mes")
                self.params['mes'] = int(self.filtros['mes'])
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
                    "SELECT DISTINCT ANIO FROM BI_VENTASNETAS WHERE ANIO IS NOT NULL ORDER BY ANIO DESC")
                filtros['anios'] = [row[0] for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT ZONA_VENDEDOR FROM BI_VENTASNETAS WHERE ZONA_VENDEDOR IS NOT NULL ORDER BY ZONA_VENDEDOR")
                filtros['vendedores'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT PROVEEDOR FROM BI_VENTASNETAS WHERE PROVEEDOR IS NOT NULL ORDER BY PROVEEDOR")
                filtros['proveedores'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT LINEA FROM BI_VENTASNETAS WHERE LINEA IS NOT NULL ORDER BY LINEA")
                filtros['lineas'] = [{'id': row[0], 'nombre': row[0]}
                                     for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT CANAL FROM BI_VENTASNETAS WHERE CANAL IS NOT NULL ORDER BY CANAL")
                filtros['canales'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
        except Exception as e:
            print(f"Error al obtener filtros: {e}")
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
                # 1. Canal
                query_canal = f"""
                    SELECT CANAL, SUM(VENTA_SIN_IVA) AS VENTA, COUNT(DISTINCT ID_TERCERO) AS QCLIENTES
                    FROM BI_VENTASNETAS v {where_sql}
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

                # 2. Municipios Top 20
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

                # 3. Treemap Vendedores
                filtro_extra = "WHERE v.ZONA_VENDEDOR IS NOT NULL" if not where_sql else f"{where_sql} AND v.ZONA_VENDEDOR IS NOT NULL"
                query_treemap = f"""
                    SELECT ZONA_VENDEDOR, SUM(VENTA_SIN_IVA) AS VENTA
                    FROM BI_VENTASNETAS v {filtro_extra}
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
        except Exception as e:
            print(f"Error al obtener datos del tablero: {e}")

        return datos

    def get_matriz_pivot_mensual(self):
        # Excluir el filtro de mes para ver la tendencia de los 12 meses
        where_matriz = [
            c for c in self.where_clauses if not c.startswith('v.MES_NUM')]
        where_sql = ("WHERE " + " AND ".join(where_matriz)
                     ) if where_matriz else ""
        params_matriz = {k: v for k, v in self.params.items() if k != 'mes'}

        query = f"""
            SELECT NVL(v.NOM_CLIENTE, 'CLIENTE SIN NOMBRE') AS CLIENTE, v.MES_NUM, SUM(v.VENTA_SIN_IVA) AS VENTA
            FROM BI_VENTASNETAS v {where_sql}
            GROUP BY NVL(v.NOM_CLIENTE, 'CLIENTE SIN NOMBRE'), v.MES_NUM
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

            top_clientes = df.groupby(
                'CLIENTE')['VENTA'].sum().nlargest(50).index
            df = df[df['CLIENTE'].isin(top_clientes)]

            pivot_df = pd.pivot_table(
                df,
                index='CLIENTE',
                columns='MES_NUM',
                values='VENTA',
                aggfunc='sum',
                fill_value=0
            )

            for mes in range(1, 13):
                if mes not in pivot_df.columns:
                    pivot_df[mes] = 0

            pivot_df = pivot_df[sorted(pivot_df.columns)]
            pivot_df['Total'] = pivot_df.sum(axis=1)
            pivot_df.loc['Total'] = pivot_df.sum()

            return pivot_df

        except Exception as e:
            print(f"Error al generar la matriz pivot: {e}")
            return pd.DataFrame()
