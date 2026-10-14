# -*- coding: utf-8 -*-
from decimal import Decimal
import logging
from django.db import connection

logger = logging.getLogger(__name__)


class VentasProductosAnalyticsService:

    def __init__(self, filtros=None):
        self.filtros = filtros if filtros else {}
        self.filtros_base = []
        self.where_clauses = []
        self.params_base = {}
        self.params = {}
        self.anio_filtro = None

        if self.filtros:
            if self.filtros.get('anio'):
                self.anio_filtro = str(self.filtros['anio'])
                self.where_clauses.append("VND.ANIO = :anio")
                self.params['anio'] = self.anio_filtro

            if self.filtros.get('mes'):
                mes_val = str(self.filtros['mes']).zfill(2)
                self.__add_filter("VND.MES_NUM = :mes", 'mes', mes_val)
            if self.filtros.get('zona_vendedor'):
                self.__add_filter("TRIM(UPPER(VND.ZONA_VENDEDOR)) = TRIM(UPPER(:zona_vendedor))",
                                  'zona_vendedor', str(self.filtros['zona_vendedor']))
            if self.filtros.get('proveedor'):
                self.__add_filter("TRIM(UPPER(VND.PROVEEDOR)) = TRIM(UPPER(:proveedor))", 'proveedor', str(
                    self.filtros['proveedor']))
            if self.filtros.get('linea'):
                self.__add_filter("TRIM(UPPER(VND.LINEA)) = TRIM(UPPER(:linea))", 'linea', str(
                    self.filtros['linea']))
            if self.filtros.get('canal'):
                self.__add_filter("TRIM(UPPER(VND.CANAL)) = TRIM(UPPER(:canal))", 'canal', str(
                    self.filtros['canal']))

    def __add_filter(self, clause, param_name, param_value):
        self.filtros_base.append(clause)
        self.where_clauses.append(clause)
        self.params_base[param_name] = param_value
        self.params[param_name] = param_value

    def _get_where_sql(self, include_anio=True):
        clauses = self.where_clauses if include_anio else self.filtros_base
        if not clauses:
            return ""
        return "WHERE " + " AND ".join(clauses)

    def _ejecutar_consulta(self, cursor, query, params=None):
        consulta_params = params if params is not None else self.params
        if consulta_params:
            cursor.execute(query, consulta_params)
        else:
            cursor.execute(query)

    def get_filtros_disponibles(self):
        filtros = {
            'anios': [], 'vendedores': [], 'proveedores': [], 'lineas': [], 'canales': []
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT ANIO FROM BI_VENTAS_CLIENTE_MENSUAL WHERE ANIO IS NOT NULL ORDER BY ANIO DESC")
                filtros['anios'] = [row[0] for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT DISTINCT ZONA_VENDEDOR FROM BI_VENTASNETAS WHERE ZONA_VENDEDOR IS NOT NULL ORDER BY ZONA_VENDEDOR")
                filtros['vendedores'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT DISTINCT PROVEEDOR FROM BI_VENTASNETAS WHERE PROVEEDOR IS NOT NULL AND PROVEEDOR != 'SIN GRUPO' ORDER BY PROVEEDOR")
                filtros['proveedores'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT DISTINCT LINEA FROM BI_VENTASNETAS WHERE LINEA IS NOT NULL AND LINEA != 'SIN LINEA' ORDER BY LINEA")
                filtros['lineas'] = [{'id': row[0], 'nombre': row[0]}
                                     for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT DISTINCT CANAL FROM BI_VENTASNETAS WHERE CANAL IS NOT NULL AND CANAL != 'SIN CANAL' ORDER BY CANAL")
                filtros['canales'] = [
                    {'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Error al obtener filtros SPEC-002: %s", e)
        return filtros

    def get_datos_tablero(self):
        where_sql = self._get_where_sql(include_anio=True)
        datos = {
            'comparativo_proveedor': [], 'cubrimiento_zona': [],
            'kilos_mes': [], 'ventas_grupo': [], 'ventas_linea': [],
            'total_productos_activos': 0
        }

        try:
            with connection.cursor() as cursor:
                # 1. Total Productos Activos - CONTEO DISTINCT GLOBAL CORREGIDO
                query_total_prod = f"SELECT COUNT(DISTINCT VND.ID_ARTICULO) FROM BI_VENTASNETAS VND {where_sql}"
                self._ejecutar_consulta(cursor, query_total_prod)
                row_prod = cursor.fetchone()
                datos['total_productos_activos'] = row_prod[0] if row_prod and row_prod[0] else 0

                # 2. Comparativo Proveedor (Año Actual vs Año Previo)
                if self.anio_filtro:
                    anio_previo = str(int(self.anio_filtro) - 1)
                    where_base = self._get_where_sql(include_anio=False)
                    where_comparativo = f"{where_base} AND VND.ANIO IN (:anio, :anio_previo)" if where_base else "WHERE VND.ANIO IN (:anio, :anio_previo)"
                    params_comp = self.params_base.copy()
                    params_comp['anio'] = self.anio_filtro
                    params_comp['anio_previo'] = anio_previo

                    query_comp = f"""
                        SELECT NVL(VND.PROVEEDOR, 'SIN GRUPO') AS PROVEEDOR,
                               SUM(CASE WHEN VND.ANIO = :anio THEN VND.VENTA_SIN_IVA ELSE 0 END) AS VENTA_ACTUAL,
                               SUM(CASE WHEN VND.ANIO = :anio_previo THEN VND.VENTA_SIN_IVA ELSE 0 END) AS VENTA_ANTERIOR
                        FROM BI_VENTASNETAS VND {where_comparativo}
                        GROUP BY NVL(VND.PROVEEDOR, 'SIN GRUPO')
                        ORDER BY VENTA_ACTUAL DESC
                    """
                    self._ejecutar_consulta(
                        cursor, query_comp, params=params_comp)
                    datos['comparativo_proveedor'] = [
                        {'proveedor': row[0], 'actual': float(
                            row[1] or 0), 'anterior': float(row[2] or 0)}
                        for row in cursor.fetchall()
                    ]

                # 3. Cubrimiento de Catálogo por Zona
                query_cubrimiento = f"""
                    SELECT NVL(VND.ZONA_VENDEDOR, 'SIN ZONA') AS ZONA,
                           COUNT(DISTINCT VND.ID_ARTICULO) AS Q_PRODUCTOS,
                           SUM(VND.VENTA_SIN_IVA) AS VENTA
                    FROM BI_VENTASNETAS VND {where_sql}
                    GROUP BY NVL(VND.ZONA_VENDEDOR, 'SIN ZONA')
                    ORDER BY Q_PRODUCTOS DESC
                """
                self._ejecutar_consulta(cursor, query_cubrimiento)
                datos['cubrimiento_zona'] = [
                    {'zona': row[0], 'q_productos': row[1],
                        'venta': float(row[2] or 0)}
                    for row in cursor.fetchall()
                ]

                # 4. Ventas por Grupo
                query_grupos = f"SELECT NVL(VND.PROVEEDOR, 'SIN GRUPO') AS AGRUPADOR, SUM(VND.VENTA_SIN_IVA) AS VENTA FROM BI_VENTASNETAS VND {where_sql} GROUP BY NVL(VND.PROVEEDOR, 'SIN GRUPO') ORDER BY VENTA DESC"
                self._ejecutar_consulta(cursor, query_grupos)
                datos['ventas_grupo'] = [{'nombre': row[0], 'venta': float(
                    row[1] or 0)} for row in cursor.fetchall()]

                # 5. Ventas por Línea
                query_lineas = f"SELECT NVL(VND.LINEA, 'SIN LINEA') AS AGRUPADOR, SUM(VND.VENTA_SIN_IVA) AS VENTA FROM BI_VENTASNETAS VND {where_sql} GROUP BY NVL(VND.LINEA, 'SIN LINEA') ORDER BY VENTA DESC"
                self._ejecutar_consulta(cursor, query_lineas)
                datos['ventas_linea'] = [{'nombre': row[0], 'venta': float(
                    row[1] or 0)} for row in cursor.fetchall()]

                # 6. Kilos Mensuales
                query_kilos = f"SELECT TO_NUMBER(VND.MES_NUM) AS MES, SUM(VND.CANTIDAD * NVL(VND.PESO, 0)) AS KILOS FROM BI_VENTASNETAS VND {where_sql} GROUP BY TO_NUMBER(VND.MES_NUM) ORDER BY MES"
                self._ejecutar_consulta(cursor, query_kilos)
                datos['kilos_mes'] = [{'mes': row[0], 'kilos': float(
                    row[1] or 0)} for row in cursor.fetchall()]

        except Exception as e:
            logger.error("Error al obtener datos del tablero SPEC-002: %s", e)

        return datos

    def get_resumen_articulos_paginado(self, offset=0, limit=100):
        """ Retorna el resumen de artículos con paginación nativa en Oracle. """
        where_sql = self._get_where_sql(include_anio=True)
        query = f"""
            WITH TOP_ART_ALL AS (
                SELECT NVL(VND.NOM_ARTICULO, 'SIN NOMBRE') AS ARTICULO,
                       SUM(VND.VENTA_SIN_IVA) AS VENTA,
                       SUM(VND.VENTA_SIN_IVA - VND.COSTO_SIN_IVA) AS UTILIDAD,
                       SUM(VND.CANTIDAD * NVL(VND.PESO, 0)) AS KILOS,
                       COUNT(DISTINCT VND.ID_TERCERO) AS Q_CLIENTES,
                       COUNT(DISTINCT VND.NUM_DOCUMENTO) AS Q_FACTURAS
                FROM BI_VENTASNETAS VND {where_sql}
                GROUP BY NVL(VND.NOM_ARTICULO, 'SIN NOMBRE')
                ORDER BY VENTA DESC
            ),
            PAGINATED_ART AS (
                SELECT a.*, ROWNUM as rnum FROM TOP_ART_ALL a WHERE ROWNUM <= {int(offset + limit)}
            )
            SELECT * FROM PAGINATED_ART WHERE rnum > {int(offset)}
        """
        resumen = []
        try:
            with connection.cursor() as cursor:
                self._ejecutar_consulta(cursor, query)
                for row in cursor.fetchall():
                    venta = float(row[1] or 0)
                    utilidad = float(row[2] or 0)
                    rentabilidad = round(
                        (utilidad / venta) * 100, 2) if venta > 0 else 0
                    resumen.append({
                        'articulo': row[0], 'venta': venta, 'rentabilidad': rentabilidad,
                        'kilos': float(row[3] or 0), 'q_clientes': row[4], 'q_facturas': row[5]
                    })
        except Exception as e:
            logger.error("Error en paginacion resumen articulos: %s", e)
        return resumen

    def get_top_productos_dinamico(self, dimension='PROVEEDOR', valor=None, limit=20):
        filtros_dinamicos = self.where_clauses.copy()
        params_dinamicos = self.params.copy()

        if valor and valor.upper() != 'GLOBAL':
            columna_sql = 'VND.PROVEEDOR' if dimension == 'PROVEEDOR' else 'VND.LINEA'
            filtros_dinamicos.append(
                f"TRIM(UPPER({columna_sql})) = TRIM(UPPER(:valor_dinamico))")
            params_dinamicos['valor_dinamico'] = valor

        where_sql = ("WHERE " + " AND ".join(filtros_dinamicos)
                     ) if filtros_dinamicos else ""

        query_total = f"SELECT SUM(VND.VENTA_SIN_IVA) FROM BI_VENTASNETAS VND {where_sql}"
        query_top = f"""
            SELECT * FROM (
                SELECT NVL(VND.NOM_ARTICULO, 'SIN NOMBRE') AS ARTICULO, SUM(VND.VENTA_SIN_IVA) AS VENTA
                FROM BI_VENTASNETAS VND {where_sql} GROUP BY NVL(VND.NOM_ARTICULO, 'SIN NOMBRE') ORDER BY VENTA DESC
            ) WHERE ROWNUM <= {int(limit)}
        """

        total_venta = Decimal('0')
        top_productos = []
        try:
            with connection.cursor() as cursor:
                self._ejecutar_consulta(
                    cursor, query_total, params=params_dinamicos)
                row_total = cursor.fetchone()
                if row_total and row_total[0]:
                    total_venta = Decimal(str(row_total[0]))

                self._ejecutar_consulta(
                    cursor, query_top, params=params_dinamicos)
                for row in cursor.fetchall():
                    venta_prod = Decimal(str(row[1] or 0))
                    top_productos.append({
                        'articulo': row[0], 'venta': float(venta_prod),
                        'participacion': round((float(venta_prod) / float(total_venta)) * 100, 2) if total_venta > 0 else 0
                    })
        except Exception as e:
            logger.error("Error al obtener top dinamico de productos: %s", e)
        return top_productos
