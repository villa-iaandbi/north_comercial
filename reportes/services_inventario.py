# -*- coding: utf-8 -*-
from decimal import Decimal
import logging
from django.db import connection

logger = logging.getLogger(__name__)


class InventarioService:
    """
    Servicio de capa de negocio y extracción de datos para el Tablero
    de Análisis de Inventarios (SPEC-004).

    Estrategia de procesamiento:
      - Origen de datos: Vista V_ANALISIS_INVENTARIO (~1700 registros).
      - Ejecución de un único SELECT filtrado a la base de datos Oracle.
      - Iteración única en memoria para clasificación de franjas de edad
        (Sano, Lento, Atrapado/Crítico), agregación de KPIs y extracción de Top 20.
      - Precisión decimal estricta mediante el uso de Decimal.
    """

    @classmethod
    def get_dashboard_data(cls, filtros: dict = None) -> dict:
        """
        Extrae y procesa los datos del tablero de inventarios a partir de la vista
        V_ANALISIS_INVENTARIO aplicando filtros opcionales de grupo, línea y estado.
        """
        filtros = filtros or {}

        # 1. Consulta SQL base y aplicación dinámica de filtros
        sql_base = """
            SELECT REFERENCIA, NOM_ARTICULO, GRUPO, EXISTENCIA, VALOR_TOTAL, ROTACION_DIARIA, DIAS_SIN_VENTA, ESTADO, VLR_PROMEDIO
            FROM V_ANALISIS_INVENTARIO
            WHERE 1=1
        """
        where_parts = []
        params = []

        val_grupo = filtros.get('grupo') or filtros.get('GRUPO')
        if val_grupo is not None:
            val_str = str(val_grupo).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND TRIM(UPPER(GRUPO)) = %s")
                params.append(val_str.upper())

        val_linea = filtros.get('linea') or filtros.get('LINEA')
        if val_linea is not None:
            val_str = str(val_linea).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND TRIM(UPPER(LINEA)) = %s")
                params.append(val_str.upper())

        val_estado = filtros.get('estado') or filtros.get('ESTADO')
        if val_estado is not None:
            val_str = str(val_estado).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND TRIM(UPPER(ESTADO)) = %s")
                params.append(val_str.upper())

        if where_parts:
            sql_base += " " + " ".join(where_parts)

        # Ejecución de la consulta
        registros = []
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql_base, params)
                columns = [col[0].upper() for col in cursor.description]
                for row in cursor.fetchall():
                    registros.append(dict(zip(columns, row)))
        except Exception as e:
            logger.error("Error al consultar V_ANALISIS_INVENTARIO: %s", e)
            raise e

        # 2. Procesamiento en Memoria (Iteración Única)
        valor_total = Decimal('0.0')
        valor_sano = Decimal('0.0')
        valor_lento = Decimal('0.0')
        valor_atrapado = Decimal('0.0')

        grupos_atrapados = {}
        lista_agotados = []
        lista_morosos = []

        for reg in registros:
            referencia = str(reg.get('REFERENCIA') or '').strip()
            nom_articulo = str(reg.get('NOM_ARTICULO') or '').strip()
            grupo = str(reg.get('GRUPO') or 'SIN GRUPO').strip()
            estado = str(reg.get('ESTADO') or '').strip().upper()
            dias_sin_venta = int(reg.get('DIAS_SIN_VENTA') or 0)

            val_tot = Decimal(str(reg.get('VALOR_TOTAL') or '0.0'))
            existencia = Decimal(str(reg.get('EXISTENCIA') or '0'))
            rotacion_diaria = Decimal(str(reg.get('ROTACION_DIARIA') or '0.0'))
            vlr_promedio = Decimal(str(reg.get('VLR_PROMEDIO') or '0.0'))

            # Clasificación por Edad
            if estado == 'I' or dias_sin_venta >= 90:
                valor_atrapado += val_tot
                grupos_atrapados[grupo] = grupos_atrapados.get(grupo, Decimal('0.0')) + val_tot
                if val_tot > Decimal('0.0'):
                    lista_morosos.append({
                        'referencia': referencia,
                        'nom_articulo': nom_articulo,
                        'grupo': grupo,
                        'existencia': existencia,
                        'valor_total': val_tot,
                        'rotacion_diaria': rotacion_diaria,
                        'dias_sin_venta': dias_sin_venta,
                        'estado': estado,
                        'vlr_promedio': vlr_promedio,
                        'REFERENCIA': referencia,
                        'NOM_ARTICULO': nom_articulo,
                        'GRUPO': grupo,
                        'EXISTENCIA': existencia,
                        'VALOR_TOTAL': val_tot,
                        'ROTACION_DIARIA': rotacion_diaria,
                        'DIAS_SIN_VENTA': dias_sin_venta,
                        'ESTADO': estado,
                        'VLR_PROMEDIO': vlr_promedio,
                    })
            elif 31 <= dias_sin_venta < 90:
                valor_lento += val_tot
            else:  # dias_sin_venta <= 30
                valor_sano += val_tot
                if rotacion_diaria > Decimal('0.0'):
                    dias_restantes = round(existencia / rotacion_diaria, 1)
                    if dias_restantes <= Decimal('10.0'):
                        # Cálculo de Venta Perdida Estimada (cobertura 7 días)
                        dias_cobertura = Decimal('7')
                        if existencia <= Decimal('0'):
                            venta_perdida_estimada = round(rotacion_diaria * dias_cobertura * vlr_promedio, 2)
                        else:
                            brecha = (rotacion_diaria * dias_cobertura) - existencia
                            if brecha > Decimal('0'):
                                venta_perdida_estimada = round(brecha * vlr_promedio, 2)
                            else:
                                venta_perdida_estimada = Decimal('0.0')

                        lista_agotados.append({
                            'referencia': referencia,
                            'nom_articulo': nom_articulo,
                            'grupo': grupo,
                            'existencia': existencia,
                            'valor_total': val_tot,
                            'rotacion_diaria': rotacion_diaria,
                            'dias_sin_venta': dias_sin_venta,
                            'estado': estado,
                            'dias_restantes': dias_restantes,
                            'vlr_promedio': vlr_promedio,
                            'venta_perdida_estimada': venta_perdida_estimada,
                            'REFERENCIA': referencia,
                            'NOM_ARTICULO': nom_articulo,
                            'GRUPO': grupo,
                            'EXISTENCIA': existencia,
                            'VALOR_TOTAL': val_tot,
                            'ROTACION_DIARIA': rotacion_diaria,
                            'DIAS_SIN_VENTA': dias_sin_venta,
                            'ESTADO': estado,
                            'DIAS_RESTANTES': dias_restantes,
                            'VLR_PROMEDIO': vlr_promedio,
                            'VENTA_PERDIDA_ESTIMADA': venta_perdida_estimada,
                        })

            valor_total += val_tot

        # 3. Cálculos de KPIs y Estructuras Finales
        costo_oportunidad = round(valor_atrapado * Decimal('0.015'), 2)
        if valor_total > Decimal('0.0'):
            indice_salud = (valor_sano / valor_total) * Decimal('100.0')
        else:
            indice_salud = Decimal('0.0')

        # Ordenar lista_agotados por dias_restantes ascendente y cortar al Top 200
        lista_agotados.sort(key=lambda x: x['dias_restantes'])
        lista_agotados = lista_agotados[:200]

        # Ordenar lista_morosos por VALOR_TOTAL descendente y cortar al Top 200
        lista_morosos.sort(key=lambda x: x['valor_total'], reverse=True)
        lista_morosos = lista_morosos[:200]

        # Ordenar grupos_atrapados por valor descendente y tomar Top 10 para ECharts
        grupos_ordenados = sorted(grupos_atrapados.items(), key=lambda x: x[1], reverse=True)[:10]
        grafico_grupos = {
            'nombres': [item[0] for item in grupos_ordenados],
            'valores': [item[1] for item in grupos_ordenados]
        }

        # 4. Retorno de estructura consolidada con aliases directos para Fase 3
        dist_edades = {
            'sano': valor_sano,
            'lento': valor_lento,
            'atrapado': valor_atrapado
        }

        return {
            'kpis': {
                'valor_total': valor_total,
                'valor_sano': valor_sano,
                'valor_lento': valor_lento,
                'valor_atrapado': valor_atrapado,
                'costo_oportunidad': costo_oportunidad,
                'indice_salud': round(indice_salud, 2)
            },
            'graficos': {
                'edades': dist_edades,
                'grupos_atrapados': grafico_grupos
            },
            'tablas': {
                'agotados': lista_agotados,
                'morosos': lista_morosos
            },
            # Aliases directos requeridos por SPEC-004 Fase 3
            'tabla_agotados': lista_agotados,
            'tabla_morosos': lista_morosos,
            'grafico_grupos': grafico_grupos,
            'distribucion_edades': dist_edades
        }

    @classmethod
    def get_filtros_disponibles(cls) -> dict:
        """
        Obtiene los valores únicos disponibles para los filtros de inventario (Grupo, Línea, Estado).
        """
        filtros = {
            'grupos': [],
            'lineas': [],
            'estados': [],
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT GRUPO FROM V_ANALISIS_INVENTARIO WHERE GRUPO IS NOT NULL ORDER BY GRUPO"
                )
                filtros['grupos'] = [{'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT LINEA FROM V_ANALISIS_INVENTARIO WHERE LINEA IS NOT NULL ORDER BY LINEA"
                )
                filtros['lineas'] = [{'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT ESTADO FROM V_ANALISIS_INVENTARIO WHERE ESTADO IS NOT NULL ORDER BY ESTADO"
                )
                filtros['estados'] = [{'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Error al obtener filtros disponibles para InventarioService: %s", e)

        return filtros
