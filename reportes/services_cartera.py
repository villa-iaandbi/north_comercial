# -*- coding: utf-8 -*-
import datetime
from decimal import Decimal
import logging
from django.db import connection
from django.template.defaultfilters import register

logger = logging.getLogger(__name__)


@register.filter(name='formato_moneda')
def formato_moneda(value):
    """Formatea un valor numérico o Decimal como moneda local sin decimales (ej. 1.234.567)."""
    if value is None or value == '':
        return '0'
    try:
        dec_val = value if isinstance(value, Decimal) else Decimal(str(value))
        return f"{int(round(dec_val)):,}".replace(",", ".")
    except Exception:
        return '0'


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

    @staticmethod
    def _parse_fecha(valor_fecha):
        """Convierte un valor datetime, date o string ISO a datetime.date."""
        if not valor_fecha:
            return None
        if isinstance(valor_fecha, datetime.datetime):
            return valor_fecha.date()
        if isinstance(valor_fecha, datetime.date):
            return valor_fecha
        try:
            return datetime.date.fromisoformat(str(valor_fecha)[:10])
        except Exception:
            return None

    @staticmethod
    def _obtener_indice_semana(dias_faltantes: int):
        """
        Agrupa los días faltantes en 4 semanas (índices 0 a 3):
          - Semana 1 (0): 0 a 7 días
          - Semana 2 (1): 8 a 14 días
          - Semana 3 (2): 15 a 21 días
          - Semana 4 (3): 22 a 30 días
        """
        if dias_faltantes < 0:
            return None
        if dias_faltantes <= 7:
            return 0
        if dias_faltantes <= 14:
            return 1
        if dias_faltantes <= 21:
            return 2
        if dias_faltantes <= 30:
            return 3
        return None

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

        # Consulta agrupada única para Promedio Contado Semanal (últimos 90 días, plazo <= 9 días)
        sql_contado_90d = """
            SELECT NVL(SUM(VNT.VENTA_CON_IVA), 0) AS TOTAL_CONTADO_90D
            FROM BI_VENTASNETAS VNT
            INNER JOIN CT_CLIENTES CLI ON VNT.ID_TERCERO = CLI.ID_TERCERO
            WHERE VNT.FECHA >= TRUNC(SYSDATE) - 90
              AND NVL(CLI.PLAZO_PAGO, 0) <= 9
        """

        # Ejecución de las consultas
        registros = []
        promedio_contado_semanal = Decimal('0.0')
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql_base, params)
                columns = [col[0].upper() for col in cursor.description] if cursor.description else []
                for row in cursor.fetchall():
                    registros.append(dict(zip(columns, row)))

                try:
                    cursor.execute(sql_contado_90d)
                    row_contado = cursor.fetchone()
                    if row_contado and row_contado[0] is not None:
                        total_contado_90d = Decimal(str(row_contado[0]))
                        promedio_contado_semanal = round(total_contado_90d / Decimal('12.85'), 2)
                except Exception as exc_contado:
                    logger.warning("No se pudo calcular promedio_contado_semanal: %s", exc_contado)
        except Exception as e:
            logger.error("Error al consultar V_ANALISIS_CARTERA: %s", e)
            raise e

        # 2. Procesamiento en Memoria (Iteración O(N))
        cartera_total = Decimal('0.0')
        cartera_vencida = Decimal('0.0')
        cartera_toxica = Decimal('0.0')
        mora_ponderada = Decimal('0.0')

        clientes_riesgo = {}
        clientes_mora_ponderada = {}
        vendedores_riesgo = {}
        tabla_comite = []
        tabla_proyeccion = []
        facturas_corrientes_raw = []

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
            fch_venc_date = cls._parse_fecha(fch_venc)

            # Acumulador general de cartera
            cartera_total += saldo

            # Cartera vencida (días de mora > 0)
            if dias_mora > 0:
                cartera_vencida += saldo
                mora_doc = saldo * Decimal(str(dias_mora))
                mora_ponderada += mora_doc

                clientes_riesgo[cliente_nombre] = clientes_riesgo.get(cliente_nombre, Decimal('0.0')) + saldo
                clientes_mora_ponderada[cliente_nombre] = clientes_mora_ponderada.get(cliente_nombre, Decimal('0.0')) + mora_doc
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
            if estado_mora == 'CORRIENTE' or dias_mora <= 0:
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
                if fch_venc_date is not None and saldo > Decimal('0.0'):
                    facturas_corrientes_raw.append((fch_venc_date, saldo, cliente_nombre))

        # 3. Cálculos Finales, DSO Histórico por Cliente y Buckets de Proyección (4 semanas)
        porcentaje_vencida = round((cartera_vencida / cartera_total) * Decimal('100.0'), 2) if cartera_total > Decimal('0.0') else Decimal('0.0')
        dso_promedio = round(mora_ponderada / cartera_vencida, 1) if cartera_vencida > Decimal('0.0') else Decimal('0.0')

        dso_por_cliente = {}
        for cli_nom, saldo_cli in clientes_riesgo.items():
            if saldo_cli > Decimal('0.0'):
                dso_por_cliente[cli_nom] = int(round(clientes_mora_ponderada.get(cli_nom, Decimal('0.0')) / saldo_cli))

        hoy = datetime.date.today()
        buckets_teorico = [Decimal('0.0'), Decimal('0.0'), Decimal('0.0'), Decimal('0.0')]
        buckets_probable = [Decimal('0.0'), Decimal('0.0'), Decimal('0.0'), Decimal('0.0')]

        for fch_venc_date, saldo_corr, cli_nom in facturas_corrientes_raw:
            dias_faltantes = (fch_venc_date - hoy).days
            semana_teorica = cls._obtener_indice_semana(dias_faltantes)
            if semana_teorica is not None:
                buckets_teorico[semana_teorica] += saldo_corr

            dias_mora_hist = dso_por_cliente.get(cli_nom, 0)
            fch_probable = fch_venc_date + datetime.timedelta(days=dias_mora_hist)
            dias_probables = (fch_probable - hoy).days
            semana_probable = cls._obtener_indice_semana(dias_probables)
            if semana_probable is not None:
                buckets_probable[semana_probable] += saldo_corr

        proyeccion_teorica = [int(round(val)) for val in buckets_teorico]
        proyeccion_probable = [int(round(val)) for val in buckets_probable]

        # Clientes con Mayor Deuda Vencida (Top 50)
        clientes_ordenados = sorted(clientes_riesgo.items(), key=lambda x: x[1], reverse=True)[:50]
        grafico_clientes = {
            'nombres': [x[0] for x in clientes_ordenados],
            'valores': [x[1] for x in clientes_ordenados],
            'items': [
                {
                    'nombre': x[0],
                    'cliente': x[0],
                    'CLIENTE_NOMBRE': x[0],
                    'valor': x[1],
                    'SALDO_VENCIDO': x[1],
                    'valor_formateado': f"$ {int(round(x[1])):,}".replace(",", ".")
                }
                for x in clientes_ordenados
            ]
        }

        # Vendedores con Cartera Vencida (Todos los vendedores con cartera vencida)
        vendedores_ordenados = sorted(vendedores_riesgo.items(), key=lambda x: x[1], reverse=True)
        grafico_vendedores = {
            'nombres': [x[0] for x in vendedores_ordenados],
            'valores': [x[1] for x in vendedores_ordenados],
            'items': [
                {
                    'nombre': x[0],
                    'vendedor': x[0],
                    'VENDEDOR_NOMBRE': x[0],
                    'valor': x[1],
                    'SALDO_VENCIDO': x[1],
                    'valor_formateado': f"$ {int(round(x[1])):,}".replace(",", ".")
                }
                for x in vendedores_ordenados
            ]
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
                'promedio_contado_semanal': promedio_contado_semanal,
            },
            'promedio_contado_semanal': promedio_contado_semanal,
            'proyeccion_teorica': proyeccion_teorica,
            'proyeccion_probable': proyeccion_probable,
            'grafico_clientes': grafico_clientes,
            'grafico_vendedores': grafico_vendedores,
            'tabla_comite': tabla_comite,
            'tabla_proyeccion': tabla_proyeccion,
            # Aliases complementarios
            'graficos': {
                'clientes': grafico_clientes,
                'vendedores': grafico_vendedores,
                'proyeccion': {
                    'teorica': proyeccion_teorica,
                    'probable': proyeccion_probable,
                    'promedio_contado_semanal': promedio_contado_semanal,
                },
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
