import oracledb
import pandas as pd
from decimal import Decimal
from django.db import connection

class VentasClientesAnalyticsService:
    """
    Servicio para el análisis de ventas por clientes. Lee de la vista agregada BI_VENTASNETAS.
    """

    def __init__(self, filtros=None):
        self.filtros = filtros if filtros else {}
        self.where_clauses = []
        self.params = []

        if self.filtros:
            if self.filtros.get('anio'):
                self.where_clauses.append("v.ANIO = :anio")
                self.params.append(self.filtros['anio'])
            if self.filtros.get('zona_vendedor'):
                self.where_clauses.append("v.ZONA_VENDEDOR = :zona_vendedor")
                self.params.append(self.filtros['zona_vendedor'])
            if self.filtros.get('proveedor'):
                self.where_clauses.append("v.PROVEEDOR = :proveedor")
                self.params.append(self.filtros['proveedor'])
            if self.filtros.get('linea'):
                self.where_clauses.append("v.LINEA = :linea")
                self.params.append(self.filtros['linea'])
            if self.filtros.get('canal'):
                self.where_clauses.append("v.CANAL = :canal")
                self.params.append(self.filtros['canal'])

    def _get_where_sql(self):
        if not self.where_clauses:
            return ""
        return "WHERE " + " AND ".join(self.where_clauses)

    def get_filtros_disponibles(self):
        """Obtiene los valores únicos para los filtros desde la vista."""
        filtros = {
            'anios': [],
            'vendedores': [],
            'proveedores': [],
            'lineas': [],
            'canales': []
        }
        try:
            with connection.cursor() as cursor:
                # Años
                cursor.execute("SELECT DISTINCT ANIO FROM BI_VENTASNETAS ORDER BY ANIO DESC")
                filtros['anios'] = [row[0] for row in cursor.fetchall()]

                # Vendedores
                cursor.execute("""
                    SELECT V.ID_VENDEDOR, NVL(V.COD_VENDEDOR, V.ID_VENDEDOR) || ' - ' || NVL(P.NOM_PERSONA, 'SIN ASIGNAR') AS NOMBRE
                    FROM CT_VENDEDORES V
                    LEFT JOIN SG_PERSONAS P ON V.ID_PERSONA = P.ID_PERSONA
                    ORDER BY NOMBRE
                """)
                filtros['vendedores'] = [{'id': row[0], 'nombre': row[1]} for row in cursor.fetchall()]

                # Proveedores (Grupos)
                cursor.execute("SELECT ID_GRUPO, NOM_GRUPO FROM IN_GRUPOS ORDER BY NOM_GRUPO")
                filtros['proveedores'] = [{'id': row[0], 'nombre': row[1]} for row in cursor.fetchall()]

                # Líneas
                cursor.execute("SELECT ID_LINEA, NOM_LINEA FROM IN_LINEAS ORDER BY NOM_LINEA")
                filtros['lineas'] = [{'id': row[0], 'nombre': row[1]} for row in cursor.fetchall()]

                # Canales
                cursor.execute("SELECT ID_CANAL, NOM_CANAL FROM CT_CANALES ORDER BY NOM_CANAL")
                filtros['canales'] = [{'id': row[0], 'nombre': row[1]} for row in cursor.fetchall()]
        except oracledb.DatabaseError as e:
            print(f"Error al obtener filtros: {e}")
            # En caso de error (ej. vista no existe), devolver vacío para no bloquear la UI
            pass
        return filtros

    def get_datos_tablero(self):
        """Consulta los datos agregados para los gráficos y tablas del tablero."""
        where_sql = self._get_where_sql()
        datos = {
            'canal': [],
            'municipios': [],
            'treemap': []
        }

        try:
            with connection.cursor() as cursor:
                # Gráfico Canal
                query_canal = f"""
                    SELECT CANAL, SUM(VENTA_SIN_IVA) AS VENTA, COUNT(DISTINCT ID_TERCERO) AS QCLIENTES
                    FROM BI_VENTASNETAS v {where_sql}
                    GROUP BY CANAL
                    ORDER BY VENTA DESC
                """
                cursor.execute(query_canal, self.params)
                datos['canal'] = [{'CANAL': row[0], 'VENTA_SIN_IVA': Decimal(row[1]), 'QCLIENTES': row[2]} for row in cursor.fetchall()]

                # Tabla Municipios
                query_municipios = f"""
                    SELECT MUNICIPIO, SUM(VENTA_SIN_IVA) AS VENTA, COUNT(DISTINCT ID_TERCERO) AS QCLIENTES
                    FROM BI_VENTASNETAS v {where_sql}
                    GROUP BY MUNICIPIO
                    ORDER BY VENTA DESC
                """
                cursor.execute(query_municipios, self.params)
                datos['municipios'] = [{'MUNICIPIO': row[0], 'VENTA_SIN_IVA': Decimal(row[1]), 'QCLIENTES': row[2]} for row in cursor.fetchall()[:20]] # Top 20

                # Treemap Vendedor
                query_treemap = f"""
                    SELECT ZONA_VENDEDOR, SUM(VENTA_SIN_IVA) AS VENTA
                    FROM BI_VENTASNETAS v {where_sql}
                    WHERE v.ZONA_VENDEDOR IS NOT NULL
                    GROUP BY ZONA_VENDEDOR
                    ORDER BY VENTA DESC
                """
                cursor.execute(query_treemap, self.params)
                datos['treemap'] = [{'name': row[0], 'value': Decimal(row[1])} for row in cursor.fetchall()]

        except oracledb.DatabaseError as e:
            print(f"Error al obtener datos del tablero: {e}")

        return datos

    def get_matriz_pivot_mensual(self):
        """Obtiene los datos para la matriz pivot y la procesa con Pandas."""
        where_sql = self._get_where_sql()
        
        query = f"""
            SELECT NOM_CLIENTE, MES_NUM, VENTA_SIN_IVA
            FROM BI_VENTASNETAS v {where_sql}
        """
        
        try:
            # Usar Pandas para leer directamente de la consulta
            with connection.cursor() as cursor:
                # Pandas no puede manejar cursores directamente, se necesita un paso intermedio
                cursor.execute(query, self.params)
                rows = cursor.fetchall()
                if not rows:
                    return pd.DataFrame() # Devuelve un DataFrame vacío si no hay datos
                
                df = pd.DataFrame(rows, columns=['NOM_CLIENTE', 'MES_NUM', 'VENTA_SIN_IVA'])
                df['VENTA_SIN_IVA'] = df['VENTA_SIN_IVA'].apply(Decimal)

            # Crear la tabla pivot
            pivot_df = pd.pivot_table(
                df,
                index='NOM_CLIENTE',
                columns='MES_NUM',
                values='VENTA_SIN_IVA',
                aggfunc='sum',
                fill_value=Decimal(0)
            )

            # Asegurarse que todos los meses (1-12) están presentes
            for mes in range(1, 13):
                if mes not in pivot_df.columns:
                    pivot_df[mes] = Decimal(0)
            
            # Ordenar columnas por mes
            pivot_df = pivot_df[sorted(pivot_df.columns)]

            # Añadir totales
            pivot_df['Total'] = pivot_df.sum(axis=1)
            pivot_df.loc['Total'] = pivot_df.sum()

            return pivot_df

        except (oracledb.DatabaseError, Exception) as e:
            print(f"Error al generar la matriz pivot: {e}")
            return pd.DataFrame() # Retornar DF vacío en caso de error
