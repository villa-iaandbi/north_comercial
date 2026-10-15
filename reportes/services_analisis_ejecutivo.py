# -*- coding: utf-8 -*-
from decimal import Decimal
import logging
from django.db import connection

logger = logging.getLogger(__name__)


class AnalisisEjecutivoService:
    """
    Servicio de capa de negocio y extracción de datos para el Tablero Ejecutivo
    de Análisis de Ventas Global y Rentabilidad (SPEC-003).

    Reglas arquitectónicas:
      - ADR-012: Toda agregación se procesa en el motor de base de datos Oracle (11g).
      - Precisión decimal: Uso estricto de Decimal para cálculos financieros.
      - Fuente de datos: Vista materializada BI_VENTAS_GLOBAL_MENSUAL.
    """

    MESES_NOMBRES = [
        'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
        'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
    ]

    @classmethod
    def _procesar_filtros(cls, filtros: dict):
        """
        Construye cláusulas WHERE adicionales y extrae parámetros limpios.
        Mapeo de filtros:
          - 'zona' (o 'zona_vendedor') -> ZONA_VENDEDOR
          - 'proveedor' -> PROVEEDOR
          - 'linea' -> LINEA
        """
        where_parts = []
        params = []
        if not filtros:
            return "", params

        val_zona = filtros.get('zona') or filtros.get('zona_vendedor')
        if val_zona is not None:
            val_str = str(val_zona).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND ZONA_VENDEDOR = %s")
                params.append(val_str.upper())

        val_prov = filtros.get('proveedor')
        if val_prov is not None:
            val_str = str(val_prov).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND PROVEEDOR = %s")
                params.append(val_str.upper())

        val_linea = filtros.get('linea')
        if val_linea is not None:
            val_str = str(val_linea).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND LINEA = %s")
                params.append(val_str.upper())

        sql_extra = (" " + " ".join(where_parts)) if where_parts else ""
        return sql_extra, params

    @classmethod
    def get_kpis_comparativos(cls, anio_actual, filtros: dict = None) -> dict:
        """
        Calcula las 5 métricas ejecutivas clave YoY (Año Actual vs Año Previo):
          1. Venta Neta
          2. Utilidad Bruta
          3. Rentabilidad (%)
          4. Devoluciones absolutas ($)
          5. Participación de Devoluciones sobre Venta Bruta (%)
        """
        anio_actual_int = int(anio_actual)
        anio_previo_int = anio_actual_int - 1

        anio_act_str = str(anio_actual_int)
        anio_prev_str = str(anio_previo_int)

        where_filtros, params_filtros = cls._procesar_filtros(filtros)

        sql = f"""
            SELECT
                -- Año Actual
                NVL(SUM(CASE WHEN ANIO = %s AND TIPO = 'VENTA' THEN VENTA_SIN_IVA ELSE 0 END), 0) AS VENTA_BRUTA_ACT,
                NVL(SUM(CASE WHEN ANIO = %s THEN VENTA_SIN_IVA ELSE 0 END), 0) AS VENTA_NETA_ACT,
                NVL(SUM(CASE WHEN ANIO = %s THEN COSTO_SIN_IVA ELSE 0 END), 0) AS COSTO_NETO_ACT,
                NVL(SUM(CASE WHEN ANIO = %s AND TIPO = 'DEVOL' THEN ABS(VENTA_SIN_IVA) ELSE 0 END), 0) AS DEVOL_ACT,

                -- Año Previo
                NVL(SUM(CASE WHEN ANIO = %s AND TIPO = 'VENTA' THEN VENTA_SIN_IVA ELSE 0 END), 0) AS VENTA_BRUTA_PREV,
                NVL(SUM(CASE WHEN ANIO = %s THEN VENTA_SIN_IVA ELSE 0 END), 0) AS VENTA_NETA_PREV,
                NVL(SUM(CASE WHEN ANIO = %s THEN COSTO_SIN_IVA ELSE 0 END), 0) AS COSTO_NETO_PREV,
                NVL(SUM(CASE WHEN ANIO = %s AND TIPO = 'DEVOL' THEN ABS(VENTA_SIN_IVA) ELSE 0 END), 0) AS DEVOL_PREV
            FROM BI_VENTAS_GLOBAL_MENSUAL
            WHERE ANIO IN (%s, %s)
            {where_filtros}
        """

        params = [
            anio_act_str, anio_act_str, anio_act_str, anio_act_str,
            anio_prev_str, anio_prev_str, anio_prev_str, anio_prev_str,
            anio_act_str, anio_prev_str
        ] + params_filtros

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()

        vb_act = Decimal(str(row[0] or 0))
        vn_act = Decimal(str(row[1] or 0))
        cn_act = Decimal(str(row[2] or 0))
        dev_act = Decimal(str(row[3] or 0))

        vb_prev = Decimal(str(row[4] or 0))
        vn_prev = Decimal(str(row[5] or 0))
        cn_prev = Decimal(str(row[6] or 0))
        dev_prev = Decimal(str(row[7] or 0))

        # Cálculos Año Actual
        ub_act = vn_act - cn_act
        rent_act = round((ub_act / vn_act) * Decimal('100.0'), 2) if vn_act != Decimal('0') else Decimal('0.0')
        part_dev_act = round((dev_act / vb_act) * Decimal('100.0'), 2) if vb_act != Decimal('0') else Decimal('0.0')

        # Cálculos Año Previo
        ub_prev = vn_prev - cn_prev
        rent_prev = round((ub_prev / vn_prev) * Decimal('100.0'), 2) if vn_prev != Decimal('0') else Decimal('0.0')
        part_dev_prev = round((dev_prev / vb_prev) * Decimal('100.0'), 2) if vb_prev != Decimal('0') else Decimal('0.0')

        # Cálculo de variaciones porcentuales (Deltas YoY)
        def _calc_delta(act: Decimal, prev: Decimal) -> Decimal:
            if not prev or prev == Decimal('0'):
                return Decimal('0.0')
            return round(((act - prev) / abs(prev)) * Decimal('100.0'), 2)

        delta_vb = _calc_delta(vb_act, vb_prev)
        delta_vn = _calc_delta(vn_act, vn_prev)
        delta_cn = _calc_delta(cn_act, cn_prev)
        delta_dev = _calc_delta(dev_act, dev_prev)
        delta_ub = _calc_delta(ub_act, ub_prev)
        delta_rent = _calc_delta(rent_act, rent_prev)
        delta_rent_puntos = round(rent_act - rent_prev, 2)
        delta_part_dev = _calc_delta(part_dev_act, part_dev_prev)

        return {
            'actual': {
                'anio': anio_actual_int,
                'venta_bruta': vb_act,
                'venta_neta': vn_act,
                'costo_neto': cn_act,
                'devoluciones': dev_act,
                'utilidad_bruta': ub_act,
                'rentabilidad': rent_act,
                'rentabilidad_porcentaje': rent_act,
                'participacion_devoluciones': part_dev_act,
                'participacion_devoluciones_porcentaje': part_dev_act,
            },
            'previo': {
                'anio': anio_previo_int,
                'venta_bruta': vb_prev,
                'venta_neta': vn_prev,
                'costo_neto': cn_prev,
                'devoluciones': dev_prev,
                'utilidad_bruta': ub_prev,
                'rentabilidad': rent_prev,
                'rentabilidad_porcentaje': rent_prev,
                'participacion_devoluciones': part_dev_prev,
                'participacion_devoluciones_porcentaje': part_dev_prev,
            },
            'deltas_yoy': {
                'venta_bruta': delta_vb,
                'venta_neta': delta_vn,
                'costo_neto': delta_cn,
                'devoluciones': delta_dev,
                'utilidad_bruta': delta_ub,
                'rentabilidad': delta_rent,
                'rentabilidad_puntos': delta_rent_puntos,
                'participacion_devoluciones': delta_part_dev,
            },
            'delta_venta_neta': delta_vn,
            'delta_utilidad_bruta': delta_ub,
            'delta_rentabilidad': delta_rent,
            'delta_devoluciones': delta_dev,
            'delta_participacion_devoluciones': delta_part_dev,
        }

    @classmethod
    def get_curvas_mensuales(cls, anio_actual, filtros: dict = None) -> dict:
        """
        Retorna las series mensuales (Ene-Dic) para el año actual y previo,
        con listas de 12 posiciones de venta_neta y rentabilidad_porcentaje.
        """
        anio_actual_int = int(anio_actual)
        anio_previo_int = anio_actual_int - 1

        anio_act_str = str(anio_actual_int)
        anio_prev_str = str(anio_previo_int)

        where_filtros, params_filtros = cls._procesar_filtros(filtros)

        sql = f"""
            SELECT
                ANIO,
                MES_NUM,
                NVL(SUM(VENTA_SIN_IVA), 0) AS VENTA_NETA,
                NVL(SUM(COSTO_SIN_IVA), 0) AS COSTO_NETO
            FROM BI_VENTAS_GLOBAL_MENSUAL
            WHERE ANIO IN (%s, %s)
            {where_filtros}
            GROUP BY ANIO, MES_NUM
            ORDER BY ANIO ASC, MES_NUM ASC
        """
        params = [anio_act_str, anio_prev_str] + params_filtros

        venta_neta_actual = [Decimal('0')] * 12
        rentabilidad_actual = [Decimal('0.0')] * 12
        venta_neta_previo = [Decimal('0')] * 12
        rentabilidad_previo = [Decimal('0.0')] * 12

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                try:
                    row_anio = int(row[0])
                    row_mes = int(row[1])
                    idx = row_mes - 1
                    if 0 <= idx < 12:
                        v = Decimal(str(row[2] or 0))
                        c = Decimal(str(row[3] or 0))
                        u = v - c
                        r = round((u / v) * Decimal('100.0'), 2) if v != Decimal('0') else Decimal('0.0')

                        if row_anio == anio_actual_int:
                            venta_neta_actual[idx] = v
                            rentabilidad_actual[idx] = r
                        elif row_anio == anio_previo_int:
                            venta_neta_previo[idx] = v
                            rentabilidad_previo[idx] = r
                except (ValueError, TypeError):
                    continue

        return {
            'actual': {
                'venta_neta': venta_neta_actual,
                'rentabilidad_porcentaje': rentabilidad_actual,
            },
            'previo': {
                'venta_neta': venta_neta_previo,
                'rentabilidad_porcentaje': rentabilidad_previo,
            }
        }

    @classmethod
    def get_historico_12_anios(cls, anio_base, filtros: dict = None) -> list:
        """
        Retorna la matriz acumulada de los últimos 12 años (orden descendente).
        Cada año contiene totales anuales y una sub-lista de 12 meses
        preparada para renderizado directo en acordeón Alpine.js.
        """
        anio_base_int = int(anio_base)
        anio_min_int = anio_base_int - 11

        where_filtros, params_filtros = cls._procesar_filtros(filtros)

        sql = f"""
            SELECT
                ANIO,
                MES_NUM,
                NVL(SUM(VENTA_SIN_IVA), 0) AS VENTA_NETA,
                NVL(SUM(COSTO_SIN_IVA), 0) AS COSTO_NETO
            FROM BI_VENTAS_GLOBAL_MENSUAL
            WHERE ANIO BETWEEN %s AND %s
            {where_filtros}
            GROUP BY ANIO, MES_NUM
            ORDER BY ANIO DESC, MES_NUM ASC
        """
        params = [str(anio_min_int), str(anio_base_int)] + params_filtros

        datos_por_anio = {
            y: {} for y in range(anio_base_int, anio_min_int - 1, -1)
        }

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                try:
                    row_anio = int(row[0])
                    row_mes = int(row[1])
                    if row_anio in datos_por_anio and 1 <= row_mes <= 12:
                        v = Decimal(str(row[2] or 0))
                        c = Decimal(str(row[3] or 0))
                        datos_por_anio[row_anio][row_mes] = (v, c)
                except (ValueError, TypeError):
                    continue

        historico = []
        for y in range(anio_base_int, anio_min_int - 1, -1):
            meses_lista = []
            venta_anual = Decimal('0')
            costo_anual = Decimal('0')

            for m in range(1, 13):
                v, c = datos_por_anio[y].get(m, (Decimal('0'), Decimal('0')))
                u = v - c
                r = round((u / v) * Decimal('100.0'), 2) if v != Decimal('0') else Decimal('0.0')

                venta_anual += v
                costo_anual += c

                meses_lista.append({
                    'mes': m,
                    'mes_num': f"{m:02d}",
                    'mes_nombre': cls.MESES_NOMBRES[m - 1],
                    'nombre': cls.MESES_NOMBRES[m - 1],
                    'venta_neta': v,
                    'utilidad': u,
                    'utilidad_bruta': u,
                    'costo_neto': c,
                    'rentabilidad_porcentaje': r,
                    'rentabilidad': r,
                })

            utilidad_anual = venta_anual - costo_anual
            rentabilidad_anual = (
                round((utilidad_anual / venta_anual) * Decimal('100.0'), 2)
                if venta_anual != Decimal('0')
                else Decimal('0.0')
            )

            historico.append({
                'anio': y,
                'venta_neta_anual': venta_anual,
                'utilidad_anual': utilidad_anual,
                'rentabilidad_anual_porcentaje': rentabilidad_anual,
                'meses': meses_lista,
            })

        return historico

    @classmethod
    def get_filtros_disponibles(cls) -> dict:
        """
        Obtiene los valores únicos disponibles para los filtros superiores.
        """
        filtros = {
            'anios': [],
            'zonas': [],
            'proveedores': [],
            'lineas': [],
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT ANIO FROM BI_VENTAS_GLOBAL_MENSUAL WHERE ANIO IS NOT NULL ORDER BY ANIO DESC"
                )
                filtros['anios'] = [row[0] for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT ZONA_VENDEDOR FROM BI_VENTAS_GLOBAL_MENSUAL WHERE ZONA_VENDEDOR IS NOT NULL ORDER BY ZONA_VENDEDOR"
                )
                filtros['zonas'] = [{'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT PROVEEDOR FROM BI_VENTAS_GLOBAL_MENSUAL WHERE PROVEEDOR IS NOT NULL ORDER BY PROVEEDOR"
                )
                filtros['proveedores'] = [{'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT LINEA FROM BI_VENTAS_GLOBAL_MENSUAL WHERE LINEA IS NOT NULL ORDER BY LINEA"
                )
                filtros['lineas'] = [{'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Error al obtener filtros disponibles para AnalisisEjecutivoService: %s", e)

        return filtros
