# -*- coding: utf-8 -*-
from decimal import Decimal
import logging
from django.db import connection

logger = logging.getLogger(__name__)


class CarteraService:
    """
    Servicio de capa de negocio y extracción de datos para el Tablero
    de Cartera y Recaudos (SPEC-005).

    Estrategia de procesamiento:
      - Origen de datos: Vista V_ANALISIS_CARTERA (~5000 registros).
      - Ejecución de un único SELECT filtrado a la base de datos Oracle.
      - Iteración única en memoria para cálculo de KPIs de riesgo (DSO, mora ponderada,
        cartera vencida, cartera tóxica > 90d), agregación Top 10 por cliente y vendedor,
        y estructuración de tablas operativas (Comité de Cobranza y Proyección de Flujo).
      - Precisión decimal estricta mediante el uso de Decimal.
      - Retorno de listas truncadas a [:200] para paginación progresiva en frontend.
    """

    @classmethod
    def get_dashboard_data(cls, filtros: dict = None) -> dict:
        """
        Extrae y procesa los datos del tablero de cartera y recaudos a partir
        de la vista V_ANALISIS_CARTERA aplicando filtros dinámicos.
        """
        filtros = filtros or {}

        # 1. Consulta SQL base y aplicación dinámica de filtros
        sql_base = """
            SELECT ID_DOCUMENTO, CLIENTE_NOMBRE, VENDEDOR_NOMBRE, NUMERO_DOCUMENTO, FECHA_VENCIMIENTO, SALDO, DIAS_MORA, ESTADO_MORA
            FROM V_ANALISIS_CARTERA
            WHERE 1=1
        """
        where_parts = []
        params = []

        val_vendedor = filtros.get('vendedor_codigo') or filtros.get('vendedor') or filtros.get('VENDEDOR_CODIGO')
        if val_vendedor is not None:
            val_str = str(val_vendedor).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND TRIM(UPPER(VENDEDOR_CODIGO)) = %s")
                params.append(val_str.upper())

        val_cliente = filtros.get('cliente_codigo') or filtros.get('cliente') or filtros.get('CLIENTE_CODIGO')
        if val_cliente is not None:
            val_str = str(val_cliente).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND TRIM(UPPER(CLIENTE_CODIGO)) = %s")
                params.append(val_str.upper())

        val_estado = filtros.get('estado_mora') or filtros.get('estado') or filtros.get('ESTADO_MORA')
        if val_estado is not None:
            val_str = str(val_estado).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND TRIM(UPPER(ESTADO_MORA)) = %s")
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
            logger.error("Error al consultar V_ANALISIS_CARTERA: %s", e)
            raise e

        # 2. Procesamiento en Memoria (Iteración Única)
        cartera_total = Decimal('0.0')
        cartera_vencida = Decimal('0.0')
        cartera_toxica = Decimal('0.0')
        mora_ponderada = Decimal('0.0')

        clientes_riesgo = {}
        vendedores_riesgo = {}
        tabla_comite = []
        tabla_proyeccion = []

        for reg in registros:
            saldo = Decimal(str(reg.get('SALDO') or '0.0'))
            dias_mora = int(reg.get('DIAS_MORA') or 0)
            estado_mora = str(reg.get('ESTADO_MORA') or '').strip().upper()
            cliente_nombre = str(reg.get('CLIENTE_NOMBRE') or 'SIN CLIENTE').strip()
            vendedor_nombre = str(reg.get('VENDEDOR_NOMBRE') or 'SIN ASIGNAR').strip()
            id_doc = str(reg.get('ID_DOCUMENTO') or '').strip()
            num_doc = str(reg.get('NUMERO_DOCUMENTO') or '').strip()
            fch_venc = reg.get('FECHA_VENCIMIENTO')
            fch_venc_str = fch_venc.strftime('%Y-%m-%d') if hasattr(fch_venc, 'strftime') else str(fch_venc)[:10] if fch_venc else ''

            # Acumulador general de cartera
            cartera_total += saldo

            # Cartera vencida (días de mora > 0)
            if dias_mora > 0:
                cartera_vencida += saldo
                mora_ponderada += (saldo * Decimal(str(dias_mora)))

                clientes_riesgo[cliente_nombre] = clientes_riesgo.get(cliente_nombre, Decimal('0.0')) + saldo
                vendedores_riesgo[vendedor_nombre] = vendedores_riesgo.get(vendedor_nombre, Decimal('0.0')) + saldo

                tabla_comite.append({
                    'id_documento': id_doc,
                    'cliente_nombre': cliente_nombre,
                    'vendedor_nombre': vendedor_nombre,
                    'numero_documento': num_doc,
                    'fecha_vencimiento': fch_venc_str,
                    'saldo': saldo,
                    'dias_mora': dias_mora,
                    'estado_mora': estado_mora,
                    'ID_DOCUMENTO': id_doc,
                    'CLIENTE_NOMBRE': cliente_nombre,
                    'VENDEDOR_NOMBRE': vendedor_nombre,
                    'NUMERO_DOCUMENTO': num_doc,
                    'FECHA_VENCIMIENTO': fch_venc_str,
                    'SALDO': saldo,
                    'DIAS_MORA': dias_mora,
                    'ESTADO_MORA': estado_mora,
                })

            # Cartera tóxica (> 90 días o identificada como TOXICA)
            if dias_mora > 90 or estado_mora == 'TOXICA > 90':
                cartera_toxica += saldo

            # Cartera corriente (No vencida para proyección de recaudos)
            if estado_mora == 'CORRIENTE':
                tabla_proyeccion.append({
                    'id_documento': id_doc,
                    'cliente_nombre': cliente_nombre,
                    'vendedor_nombre': vendedor_nombre,
                    'numero_documento': num_doc,
                    'fecha_vencimiento': fch_venc_str,
                    'saldo': saldo,
                    'dias_mora': dias_mora,
                    'estado_mora': estado_mora,
                    'ID_DOCUMENTO': id_doc,
                    'CLIENTE_NOMBRE': cliente_nombre,
                    'VENDEDOR_NOMBRE': vendedor_nombre,
                    'NUMERO_DOCUMENTO': num_doc,
                    'FECHA_VENCIMIENTO': fch_venc_str,
                    'SALDO': saldo,
                    'DIAS_MORA': dias_mora,
                    'ESTADO_MORA': estado_mora,
                })

        # 3. Cálculos Finales y Ordenamiento
        porcentaje_vencida = round((cartera_vencida / cartera_total) * Decimal('100.0'), 2) if cartera_total > Decimal('0.0') else Decimal('0.0')
        dso_promedio = round(mora_ponderada / cartera_vencida, 1) if cartera_vencida > Decimal('0.0') else Decimal('0.0')

        # Top 10 Clientes con Mayor Deuda Vencida
        clientes_ordenados = sorted(clientes_riesgo.items(), key=lambda x: x[1], reverse=True)[:10]
        grafico_clientes = {
            'nombres': [x[0] for x in clientes_ordenados],
            'valores': [x[1] for x in clientes_ordenados]
        }

        # Top 10 Vendedores con Mayor Deuda Vencida
        vendedores_ordenados = sorted(vendedores_riesgo.items(), key=lambda x: x[1], reverse=True)[:10]
        grafico_vendedores = {
            'nombres': [x[0] for x in vendedores_ordenados],
            'valores': [x[1] for x in vendedores_ordenados]
        }

        # Ordenar tabla_comite por DIAS_MORA descendente y truncar a Top 200
        tabla_comite.sort(key=lambda x: x['dias_mora'], reverse=True)
        tabla_comite = tabla_comite[:200]

        # Ordenar tabla_proyeccion por FECHA_VENCIMIENTO ascendente y truncar a Top 200
        tabla_proyeccion.sort(key=lambda x: str(x['fecha_vencimiento']))
        tabla_proyeccion = tabla_proyeccion[:200]

        # 4. Retorno de estructura maestra consolidada
        return {
            'kpis': {
                'cartera_total': cartera_total,
                'cartera_vencida': cartera_vencida,
                'porcentaje_vencida': porcentaje_vencida,
                'dso_promedio': dso_promedio,
                'cartera_toxica': cartera_toxica,
            },
            'grafico_clientes': grafico_clientes,
            'grafico_vendedores': grafico_vendedores,
            'tabla_comite': tabla_comite,
            'tabla_proyeccion': tabla_proyeccion,
            # Aliases complementarios
            'graficos': {
                'clientes': grafico_clientes,
                'vendedores': grafico_vendedores,
            },
            'tablas': {
                'comite': tabla_comite,
                'proyeccion': tabla_proyeccion,
            }
        }

    @classmethod
    def get_filtros_disponibles(cls) -> dict:
        """
        Obtiene los valores únicos disponibles para los filtros de cartera (Vendedores, Estados de Mora).
        """
        filtros = {
            'vendedores': [],
            'estados_mora': [],
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT VENDEDOR_CODIGO, VENDEDOR_NOMBRE FROM V_ANALISIS_CARTERA WHERE VENDEDOR_CODIGO IS NOT NULL ORDER BY VENDEDOR_NOMBRE"
                )
                filtros['vendedores'] = [{'id': row[0], 'nombre': row[1] or row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT ESTADO_MORA FROM V_ANALISIS_CARTERA WHERE ESTADO_MORA IS NOT NULL ORDER BY ESTADO_MORA"
                )
                filtros['estados_mora'] = [{'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Error al obtener filtros disponibles para CarteraService: %s", e)

        return filtros
