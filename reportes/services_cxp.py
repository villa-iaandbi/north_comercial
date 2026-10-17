# -*- coding: utf-8 -*-
import datetime
from decimal import Decimal
import logging
from django.db import connection
from django.template.defaultfilters import register

logger = logging.getLogger(__name__)


if 'formato_moneda' not in register.filters:
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


class CxpService:
    """
    Servicio de capa de negocio y extracción de datos para el Tablero
    de Cuentas por Pagar - CxP (SPEC-006).

    Estrategia de procesamiento:
      - Consulta agrupada única a BI_VENTASNETAS + CT_CLIENTES para promedio semanal de ingresos de contado.
      - Consulta única a BI_CUENTAS_POR_PAGAR (SALDO > 0).
      - Iteración O(N) en memoria para KPIs (deuda_total, cxp_vencida, porcentaje_vencida,
        ahorro_potencial, DPO), Top Proveedores, Radar de Descuentos, Cronograma Regular
        y proyección a 4 semanas (pago_legal vs pago_inteligente).
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
    def _format_fecha(valor_fecha) -> str:
        """Formatea una fecha a YYYY-MM-DD."""
        if not valor_fecha:
            return ''
        if hasattr(valor_fecha, 'strftime'):
            return valor_fecha.strftime('%Y-%m-%d')
        return str(valor_fecha)[:10]

    @staticmethod
    def _obtener_indice_semana(dias_faltantes: int):
        """
        Agrupa los días faltantes en 4 semanas (índices 0 a 3):
          - Semana 1 (0): 0 a 7 días
          - Semana 2 (1): 8 a 14 días
          - Semana 3 (2): 15 a 21 días
          - Semana 4 (3): 22 a 30 días
        """
        if dias_faltantes is None or dias_faltantes < 0:
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
        Extrae y procesa en O(N) los datos del tablero de Cuentas por Pagar (SPEC-006).
        """
        filtros = filtros or {}

        # 1. Consulta Ingresos: Ventas de contado (plazo <= 9 días) últimos 90 días
        sql_ingresos_90d = """
            SELECT NVL(SUM(VNT.VENTA_CON_IVA), 0) AS TOTAL_CONTADO_90D
            FROM BI_VENTASNETAS VNT
            INNER JOIN CT_CLIENTES CLI ON VNT.ID_TERCERO = CLI.ID_TERCERO
            WHERE VNT.FECHA >= TRUNC(SYSDATE) - 90
              AND NVL(CLI.PLAZO_PAGO, 0) <= 9
        """

        # 2. Consulta CxP: Facturas con saldo vivo en BI_CUENTAS_POR_PAGAR
        sql_cxp = """
            SELECT *
            FROM BI_CUENTAS_POR_PAGAR
            WHERE SALDO > 0
        """
        where_parts = []
        params = []

        val_proveedor = filtros.get('proveedor_codigo') or filtros.get('proveedor') or filtros.get('PROVEEDOR_CODIGO')
        if val_proveedor is not None:
            val_str = str(val_proveedor).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND TRIM(UPPER(PROVEEDOR_CODIGO)) = %s")
                params.append(val_str.upper())

        val_estado = filtros.get('estado_mora') or filtros.get('estado') or filtros.get('ESTADO_MORA')
        if val_estado is not None:
            val_str = str(val_estado).strip()
            if val_str and val_str.upper() != 'TODOS':
                where_parts.append("AND TRIM(UPPER(ESTADO_MORA)) = %s")
                params.append(val_str.upper())

        if where_parts:
            sql_cxp += " " + " ".join(where_parts)

        promedio_ingreso_semanal = Decimal('0.0')
        registros = []

        try:
            with connection.cursor() as cursor:
                try:
                    cursor.execute(sql_ingresos_90d)
                    row_ing = cursor.fetchone()
                    if row_ing and row_ing[0] is not None:
                        total_contado_90d = Decimal(str(row_ing[0]))
                        promedio_ingreso_semanal = round(total_contado_90d / Decimal('12.85'), 2)
                except Exception as exc_ing:
                    logger.warning("No se pudo calcular promedio_ingreso_semanal en CxpService: %s", exc_ing)

                cursor.execute(sql_cxp, params)
                columns = [col[0].upper() for col in cursor.description] if cursor.description else []
                for row in cursor.fetchall():
                    registros.append(dict(zip(columns, row)))
        except Exception as e:
            logger.error("Error al consultar BI_CUENTAS_POR_PAGAR: %s", e)
            raise e

        # 3. Iteración O(N) en memoria
        deuda_total = Decimal('0.0')
        cxp_vencida = Decimal('0.0')
        ahorro_potencial = Decimal('0.0')
        top_proveedores = {}
        radar_descuentos = []
        cronograma_regular = []
        buckets_legal = [Decimal('0.0'), Decimal('0.0'), Decimal('0.0'), Decimal('0.0')]
        buckets_inteligente = [Decimal('0.0'), Decimal('0.0'), Decimal('0.0'), Decimal('0.0')]

        dpo_ponderado = Decimal('0.0')
        mora_ponderada = Decimal('0.0')
        hoy = datetime.date.today()

        for reg in registros:
            saldo = Decimal(str(reg.get('SALDO') or '0.0'))
            if saldo <= Decimal('0.0'):
                continue

            dias_mora = int(reg.get('DIAS_MORA') or 0)
            estado_mora = str(reg.get('ESTADO_MORA') or '').strip().upper()
            proveedor_codigo = str(reg.get('PROVEEDOR_CODIGO') or '').strip()
            proveedor_nombre = str(reg.get('PROVEEDOR_NOMBRE') or 'SIN PROVEEDOR').strip()
            id_doc = str(reg.get('ID_DOCUMENTO') or '').strip()
            num_doc = str(reg.get('NUMERO_DOCUMENTO') or '').strip()
            tipo_doc = str(reg.get('TIPO_DOCUMENTO') or 'COMPRA').strip()

            fch_doc_raw = reg.get('FECHA_DOCUMENTO')
            fch_venc_raw = reg.get('FECHA_VENCIMIENTO')
            fch_lim_desc_raw = reg.get('FECHA_LIMITE_DESCUENTO')

            fch_doc_date = cls._parse_fecha(fch_doc_raw)
            fch_venc_date = cls._parse_fecha(fch_venc_raw)
            fch_lim_desc_date = cls._parse_fecha(fch_lim_desc_raw)

            fch_doc_str = cls._format_fecha(fch_doc_raw)
            fch_venc_str = cls._format_fecha(fch_venc_raw)
            fch_lim_desc_str = cls._format_fecha(fch_lim_desc_raw)

            porc_descuento = Decimal(str(reg.get('PORC_DESCUENTO') or '0.0'))
            vlr_descuento = Decimal(str(reg.get('VALOR_DESCUENTO_PROYECTADO') or '0.0'))

            # 4.1 Acumulador general y cartera vencida
            deuda_total += saldo

            if fch_doc_date and fch_venc_date and fch_venc_date >= fch_doc_date:
                plazo_dias = (fch_venc_date - fch_doc_date).days
            else:
                plazo_dias = abs(dias_mora)
            dpo_ponderado += saldo * Decimal(str(plazo_dias))

            if estado_mora == 'VENCIDO':
                cxp_vencida += saldo
                top_proveedores[proveedor_nombre] = top_proveedores.get(proveedor_nombre, Decimal('0.0')) + saldo
                if dias_mora > 0:
                    mora_ponderada += saldo * Decimal(str(dias_mora))

            dias_faltantes_venc = (fch_venc_date - hoy).days if fch_venc_date else None
            dias_faltantes_desc = (fch_lim_desc_date - hoy).days if fch_lim_desc_date else None

            item_factura = {
                'id_documento': id_doc,
                'proveedor_codigo': proveedor_codigo,
                'proveedor_nombre': proveedor_nombre,
                'tipo_documento': tipo_doc,
                'numero_documento': num_doc,
                'fecha_documento': fch_doc_str,
                'fecha_vencimiento': fch_venc_str,
                'fecha_limite_descuento': fch_lim_desc_str,
                'saldo': saldo,
                'dias_mora': dias_mora,
                'dias_faltantes': dias_faltantes_venc if dias_faltantes_venc is not None else 0,
                'dias_faltantes_descuento': dias_faltantes_desc if dias_faltantes_desc is not None else 0,
                'estado_mora': estado_mora,
                'porc_descuento': porc_descuento,
                'valor_descuento_proyectado': vlr_descuento,
                'saldo_con_descuento': saldo - vlr_descuento,
                'ID_DOCUMENTO': id_doc,
                'PROVEEDOR_CODIGO': proveedor_codigo,
                'PROVEEDOR_NOMBRE': proveedor_nombre,
                'TIPO_DOCUMENTO': tipo_doc,
                'NUMERO_DOCUMENTO': num_doc,
                'FECHA_DOCUMENTO': fch_doc_str,
                'FECHA_VENCIMIENTO': fch_venc_str,
                'FECHA_LIMITE_DESCUENTO': fch_lim_desc_str,
                'SALDO': saldo,
                'DIAS_MORA': dias_mora,
                'ESTADO_MORA': estado_mora,
                'PORC_DESCUENTO': porc_descuento,
                'VALOR_DESCUENTO_PROYECTADO': vlr_descuento,
            }

            # 4.2 Clasificación por descuento financiero y acumulación pago_inteligente
            if vlr_descuento > Decimal('0.0'):
                ahorro_potencial += vlr_descuento
                radar_descuentos.append(item_factura)
                idx_sem_desc = cls._obtener_indice_semana(dias_faltantes_desc)
                if idx_sem_desc is not None:
                    buckets_inteligente[idx_sem_desc] += saldo
            else:
                cronograma_regular.append(item_factura)
                idx_sem_reg = cls._obtener_indice_semana(dias_faltantes_venc)
                if idx_sem_reg is not None:
                    buckets_inteligente[idx_sem_reg] += saldo

            # 4.3 Acumulación pago_legal (siempre según FECHA_VENCIMIENTO)
            idx_sem_legal = cls._obtener_indice_semana(dias_faltantes_venc)
            if idx_sem_legal is not None:
                buckets_legal[idx_sem_legal] += saldo

        # 5. Post-Proceso: Ordenamientos, Top 50 y KPIs Derivados
        radar_descuentos.sort(key=lambda x: str(x.get('fecha_limite_descuento') or '9999-12-31'))
        cronograma_regular.sort(key=lambda x: str(x.get('fecha_vencimiento') or '9999-12-31'))

        proveedores_ordenados = sorted(top_proveedores.items(), key=lambda x: x[1], reverse=True)[:50]
        top_proveedores_list = [
            {
                'nombre': nom,
                'proveedor': nom,
                'proveedor_nombre': nom,
                'PROVEEDOR_NOMBRE': nom,
                'saldo': val,
                'valor': val,
                'SALDO_VENCIDO': val,
                'valor_formateado': f"$ {int(round(val)):,}".replace(",", "."),
            }
            for nom, val in proveedores_ordenados
        ]

        grafico_proveedores = {
            'nombres': [x[0] for x in proveedores_ordenados],
            'valores': [int(round(x[1])) for x in proveedores_ordenados],
            'items': top_proveedores_list,
        }

        pago_legal = [int(round(val)) for val in buckets_legal]
        pago_inteligente = [int(round(val)) for val in buckets_inteligente]

        porcentaje_vencida = (
            round((cxp_vencida / deuda_total) * Decimal('100.0'), 2)
            if deuda_total > Decimal('0.0')
            else Decimal('0.0')
        )
        dpo = (
            round(dpo_ponderado / deuda_total, 1)
            if deuda_total > Decimal('0.0')
            else Decimal('0.0')
        )
        dpo_mora = (
            round(mora_ponderada / cxp_vencida, 1)
            if cxp_vencida > Decimal('0.0')
            else Decimal('0.0')
        )

        return {
            'kpis': {
                'deuda_total': deuda_total,
                'cxp_vencida': cxp_vencida,
                'porcentaje_vencida': porcentaje_vencida,
                'ahorro_potencial': ahorro_potencial,
                'dpo': dpo,
                'dpo_promedio': dpo,
                'dpo_mora': dpo_mora,
                'promedio_ingreso_semanal': promedio_ingreso_semanal,
            },
            'deuda_total': deuda_total,
            'cxp_vencida': cxp_vencida,
            'porcentaje_vencida': porcentaje_vencida,
            'ahorro_potencial': ahorro_potencial,
            'dpo': dpo,
            'dpo_promedio': dpo,
            'promedio_ingreso_semanal': promedio_ingreso_semanal,
            'pago_legal': pago_legal,
            'pago_inteligente': pago_inteligente,
            'top_proveedores': top_proveedores_list,
            'grafico_proveedores': grafico_proveedores,
            'radar_descuentos': radar_descuentos[:200],
            'cronograma_regular': cronograma_regular[:200],
        }

    @classmethod
    def get_filtros_disponibles(cls) -> dict:
        """
        Obtiene los valores únicos disponibles para filtros del tablero CxP.
        """
        filtros = {
            'proveedores': [],
            'estados_mora': [],
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT PROVEEDOR_CODIGO, PROVEEDOR_NOMBRE FROM BI_CUENTAS_POR_PAGAR WHERE SALDO > 0 AND PROVEEDOR_CODIGO IS NOT NULL ORDER BY PROVEEDOR_NOMBRE"
                )
                filtros['proveedores'] = [{'id': row[0], 'nombre': row[1] or row[0]} for row in cursor.fetchall()]

                cursor.execute(
                    "SELECT DISTINCT ESTADO_MORA FROM BI_CUENTAS_POR_PAGAR WHERE SALDO > 0 AND ESTADO_MORA IS NOT NULL ORDER BY ESTADO_MORA"
                )
                filtros['estados_mora'] = [{'id': row[0], 'nombre': row[0]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Error al obtener filtros disponibles para CxpService: %s", e)

        return filtros
