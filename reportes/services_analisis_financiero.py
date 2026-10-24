# -*- coding: utf-8 -*-
import datetime
from decimal import Decimal, ROUND_HALF_UP
from django.db import connection

class ServicioAnalisisFinanciero:
    @classmethod
    def get_filtros_disponibles(cls):
        """Devuelve los listados para los combos de filtros en la cabecera del tablero"""
        return {
            'anios': [{'id': str(y), 'nombre': str(y)} for y in range(2020, 2027)],
            'meses': [{'id': f"{m:02d}", 'nombre': str(m)} for m in range(1, 13)],
            'centros_costo': [] # Lo poblaremos desde Oracle más adelante
        }

    def __init__(self, filtros=None):
        self.filtros = {str(k).lower(): v for k, v in (filtros or {}).items()}
        hoy = datetime.date.today()
        self.anio = str(self.filtros.get('anio') or hoy.year)
        self.mes = str(self.filtros.get('mes') or f"{hoy.month:02d}")
        self.periodo = f"{self.anio}{self.mes}"
        self.periodo_inicio_anio = f"{self.anio}01"
        self.id_centro_costo = self.filtros.get('id_centro_costo')

    def _ejecutar_consulta(self, query, params):
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0].lower() for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _safe_div(self, numerador, denominador):
        if not denominador or denominador == 0:
            return Decimal('0.00')
        return (numerador / denominador).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def get_kpis_completos(self):
        # Magia Arquitectónica: Suma historia completa para Balance (1,2,3) y Acumulado del Año (YTD) para P&G (4,5,6,7)
        query = """
            SELECT 
                SAL.CTA_NIVEL_1,
                SAL.CTA_NIVEL_2,
                SAL.CTA_NIVEL_4,
                SUM(SAL.MOVIMIENTO_NETO) AS ACUMULADO
            FROM V_BI_CONTABILIDAD_DASHBOARD SAL
            WHERE 
                (
                    (SAL.CTA_NIVEL_1 IN ('1', '2', '3') AND SAL.PERIODO <= %(periodo)s)
                    OR 
                    (SAL.CTA_NIVEL_1 IN ('4', '5', '6', '7') AND SAL.PERIODO BETWEEN %(periodo_inicio)s AND %(periodo)s)
                )
        """
        params = {'periodo': self.periodo, 'periodo_inicio': self.periodo_inicio_anio}
        
        if self.id_centro_costo:
            query += " AND SAL.ID_CENTRO_COSTO = %(id_centro_costo)s"
            params['id_centro_costo'] = self.id_centro_costo
            
        query += " GROUP BY SAL.CTA_NIVEL_1, SAL.CTA_NIVEL_2, SAL.CTA_NIVEL_4"
        resultados = self._ejecutar_consulta(query, params)

        # 1. Variables de Balance (Acumuladas)
        activo_corriente = Decimal('0')
        activo_no_corriente = Decimal('0')
        pasivo_corriente = Decimal('0')
        pasivo_no_corriente = Decimal('0')
        patrimonio = Decimal('0')
        inventarios = Decimal('0')

        # 2. Variables de P&G (YTD)
        ingresos_netos = Decimal('0')
        costo_ventas = Decimal('0')
        gastos_admin = Decimal('0')
        gastos_ventas = Decimal('0')
        gastos_financieros = Decimal('0')
        depreciaciones = Decimal('0')
        impuestos = Decimal('0')

        for f in resultados:
            n1, n2, n4 = f.get('cta_nivel_1'), f.get('cta_nivel_2'), f.get('cta_nivel_4')
            valor = Decimal(str(f.get('acumulado') or 0))

            if n1 == '1': # ACTIVOS (Naturaleza Débito)
                if n2 in ('11', '12', '13', '14'):
                    activo_corriente += valor
                    if n2 == '14': inventarios += valor
                else:
                    activo_no_corriente += valor
            elif n1 == '2': # PASIVOS (Naturaleza Crédito)
                if n2 in ('21', '22', '23', '24', '25'):
                    pasivo_corriente += abs(valor)
                else:
                    pasivo_no_corriente += abs(valor)
            elif n1 == '3': # PATRIMONIO (Naturaleza Crédito)
                patrimonio += abs(valor)
            
            elif n1 == '4': # INGRESOS
                if n2 == '41': ingresos_netos += abs(valor) # Se resta 4175 (Devoluciones) si viene en débito, el abs(suma de saldos) ya lo netea si la cuenta padre netea.
            elif n1 == '5': # GASTOS (Naturaleza Débito)
                if n2 == '51':
                    if n4 in ('5160', '5165'): depreciaciones += valor
                    else: gastos_admin += valor
                elif n2 == '52':
                    if n4 in ('5260', '5265'): depreciaciones += valor
                    else: gastos_ventas += valor
                elif n2 == '53':
                    if n4 == '5305': gastos_financieros += valor
                elif n2 == '54':
                    impuestos += valor
            elif n1 == '6': # COSTOS
                costo_ventas += valor

        # Cálculos Derivados - Estructura
        activo_total = activo_corriente + activo_no_corriente
        pasivo_total = pasivo_corriente + pasivo_no_corriente
        
        # Cálculos Derivados - Resultados
        utilidad_bruta = ingresos_netos - costo_ventas
        ebitda = utilidad_bruta - gastos_admin - gastos_ventas
        utilidad_operativa = ebitda - depreciaciones
        utilidad_neta = utilidad_operativa - gastos_financieros - impuestos

        return {
            # Tab 1: Rentabilidad
            'ingresos_netos': ingresos_netos,
            'ebitda': ebitda,
            'margen_operativo': self._safe_div(utilidad_operativa * 100, ingresos_netos),
            'margen_neto': self._safe_div(utilidad_neta * 100, ingresos_netos),
            
            # Tab 2: Liquidez
            'razon_corriente': self._safe_div(activo_corriente, pasivo_corriente),
            'prueba_acida': self._safe_div((activo_corriente - inventarios), pasivo_corriente),
            'ctn': activo_corriente - pasivo_corriente,
            
            # Tab 3: Endeudamiento
            'nivel_endeudamiento': self._safe_div(pasivo_total * 100, activo_total),
            'cobertura_intereses': self._safe_div(ebitda, gastos_financieros),
            
            # Tab 4: DuPont
            'roa': self._safe_div(utilidad_neta * 100, activo_total),
            'roe': self._safe_div(utilidad_neta * 100, patrimonio),
            'multiplicador_capital': self._safe_div(activo_total, patrimonio),
            
            # Detalle para Waterfall (Cascada)
            'detalle_cascada': {
                'ventas': ingresos_netos,
                'costos': costo_ventas,
                'gastos_admin': gastos_admin,
                'gastos_ventas': gastos_ventas,
                'gastos_financieros': gastos_financieros,
                'impuestos': impuestos,
                'utilidad_neta': utilidad_neta
            }
        }

    def get_datos_tablero(self):
        kpis = self.get_kpis_completos()
        return {
            'periodo_actual': self.periodo,
            'kpis': kpis
        }

