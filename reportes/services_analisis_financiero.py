# -*- coding: utf-8 -*-
import datetime
from decimal import Decimal, ROUND_HALF_UP
from django.db import connection

class ServicioAnalisisFinanciero:
    @classmethod
    def get_filtros_disponibles(cls, anio_actual=None):
        centros = []
        try:
            with connection.cursor() as cursor:
                if anio_actual:
                    # Filtro exacto por Año y Sistema '1' (North Comercial)
                    cursor.execute("""
                        SELECT ID_CENTRO_COSTO, COD_CENTRO_COSTO, NOM_CENTRO_COSTO 
                        FROM CO_CENTRO_COSTOS 
                        WHERE ID_SISTEMA = '1' AND ID_ANO = %s 
                        ORDER BY COD_CENTRO_COSTO
                    """, [str(anio_actual)])
                else:
                    # Fallback global si la vista aún no pasa el año
                    cursor.execute("""
                        SELECT DISTINCT ID_CENTRO_COSTO, COD_CENTRO_COSTO, NOM_CENTRO_COSTO 
                        FROM CO_CENTRO_COSTOS 
                        WHERE ID_SISTEMA = '1' 
                        ORDER BY COD_CENTRO_COSTO
                    """)
                    
                for r in cursor.fetchall():
                    centros.append({'id': r[0], 'nombre': f"{r[1]} - {r[2]}"})
        except Exception:
            pass
            
        return {
            'anios': [{'id': str(y), 'nombre': str(y)} for y in range(2020, 2027)],
            'periodos': [
                {'id': f"{m:02d}", 'nombre': f"Acumulado a Mes {m:02d} (YTD)"} for m in range(1, 13)
            ] + [
                {'id': 'T1', 'nombre': 'Trimestre 1 (Ene-Mar)'}, {'id': 'T2', 'nombre': 'Trimestre 2 (Abr-Jun)'},
                {'id': 'T3', 'nombre': 'Trimestre 3 (Jul-Sep)'}, {'id': 'T4', 'nombre': 'Trimestre 4 (Oct-Dic)'},
                {'id': 'C1', 'nombre': 'Cuatrimestre 1 (Ene-Abr)'}, {'id': 'C2', 'nombre': 'Cuatrimestre 2 (May-Ago)'},
                {'id': 'C3', 'nombre': 'Cuatrimestre 3 (Sep-Dic)'},
                {'id': 'S1', 'nombre': 'Semestre 1 (Ene-Jun)'}, {'id': 'S2', 'nombre': 'Semestre 2 (Jul-Dic)'},
                {'id': 'AA', 'nombre': 'Año Completo (Ene-Dic)'}
            ],
            'centros_costo': centros
        }

    def __init__(self, filtros=None):
        self.filtros = {str(k).lower(): v for k, v in (filtros or {}).items()}
        hoy = datetime.date.today()
        self.anio = str(self.filtros.get('anio') or hoy.year)
        raw_periodo = str(self.filtros.get('periodo') or self.filtros.get('mes') or f"{hoy.month:02d}").upper()

        rangos = {
            'T1': ('01', '03'), 'T2': ('04', '06'), 'T3': ('07', '09'), 'T4': ('10', '12'),
            'C1': ('01', '04'), 'C2': ('05', '08'), 'C3': ('09', '12'),
            'S1': ('01', '06'), 'S2': ('07', '12'),
            'AA': ('01', '12'),
        }
        if raw_periodo in rangos:
            mes_ini, mes_fin = rangos[raw_periodo]
        else:
            mes_ini, mes_fin = '01', f"{int(raw_periodo):02d}" if raw_periodo.isdigit() else f"{hoy.month:02d}"

        self.mes = mes_fin
        self.periodo_inicio_anio = f"{self.anio}{mes_ini}"
        self.periodo = f"{self.anio}{mes_fin}"
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
        ingresos_no_op = Decimal('0')
        costo_ventas = Decimal('0')
        gastos_admin = Decimal('0')
        gastos_ventas = Decimal('0')
        gastos_no_op = Decimal('0')
        depreciaciones = Decimal('0')
        impuestos = Decimal('0')

        for f in resultados:
            n1, n2, n4 = f.get('cta_nivel_1'), f.get('cta_nivel_2'), f.get('cta_nivel_4')
            valor = Decimal(str(f.get('acumulado') or 0)) # Respetamos el signo original de Oracle

            if n1 == '1': # ACTIVOS (Débito +)
                if n2 in ('11', '12', '13', '14'):
                    activo_corriente += valor
                    if n2 == '14': inventarios += valor
                else:
                    activo_no_corriente += valor
            elif n1 == '2': # PASIVOS (Crédito -)
                if n2 in ('21', '22', '23', '24', '25'):
                    pasivo_corriente += valor
                else:
                    pasivo_no_corriente += valor
            elif n1 == '3': # PATRIMONIO (Crédito -)
                patrimonio += valor
            elif n1 == '4': # INGRESOS (Crédito -)
                if n2 == '41': ingresos_netos += valor
                elif n2 == '42': ingresos_no_op += valor
            elif n1 == '5': # GASTOS (Débito +)
                if n2 == '51':
                    if n4 in ('5160', '5165'): depreciaciones += valor
                    else: gastos_admin += valor
                elif n2 == '52':
                    if n4 in ('5260', '5265'): depreciaciones += valor
                    else: gastos_ventas += valor
                elif n2 == '53': # Gastos No Operacionales / Financieros (Todos)
                    gastos_no_op += valor
                elif n2 == '54':
                    impuestos += valor
            elif n1 == '6': # COSTOS (Débito +)
                costo_ventas += valor

        # Convertimos las cuentas de naturaleza crédito a positivo SOLO al final para no romper los ratios
        pasivo_corriente = abs(pasivo_corriente)
        pasivo_no_corriente = abs(pasivo_no_corriente)
        patrimonio = abs(patrimonio)
        ingresos_netos = abs(ingresos_netos)
        ingresos_no_op = abs(ingresos_no_op)
        
        # Cálculos Derivados - Estructura
        activo_total = activo_corriente + activo_no_corriente
        pasivo_total = pasivo_corriente + pasivo_no_corriente
        
        # Cálculos Derivados - Resultados
        utilidad_bruta = ingresos_netos - costo_ventas
        ebitda = utilidad_bruta - gastos_admin - gastos_ventas
        utilidad_operativa = ebitda - depreciaciones
        # La cuenta 42 suma, la cuenta 53 resta
        utilidad_neta = utilidad_operativa + ingresos_no_op - gastos_no_op - impuestos

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
            'cobertura_intereses': self._safe_div(ebitda, gastos_no_op),
            'concentracion_pasivo_cp': self._safe_div(pasivo_corriente * 100, pasivo_total),
            
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
                'otros_ingresos': ingresos_no_op,
                'gastos_financieros': gastos_no_op,
                'impuestos': impuestos,
                'utilidad_neta': utilidad_neta
            },
            'detalle_dupont': {
                'roe': self._safe_div(utilidad_neta * 100, patrimonio),
                'roa': self._safe_div(utilidad_neta * 100, activo_total),
                'margen_neto': self._safe_div(utilidad_neta * 100, ingresos_netos),
                'rotacion_activos': self._safe_div(ingresos_netos, activo_total),
                'apalancamiento': self._safe_div(activo_total, patrimonio),
                'utilidad_neta': utilidad_neta,
                'ventas': ingresos_netos,
                'activo_total': activo_total,
                'patrimonio': patrimonio
            }
        }

    def get_tendencias_historicas(self):
        """Usa funciones analíticas para tendencias de 12 meses (Liquidez y Estructura)"""
        query = """
            WITH Base AS (
                SELECT
                    PERIODO,
                    SUM(CASE WHEN CTA_NIVEL_1 = '1' AND CTA_NIVEL_2 IN ('11', '12', '13', '14') THEN MOVIMIENTO_NETO ELSE 0 END) AS mov_activo_cte,
                    SUM(CASE WHEN CTA_NIVEL_1 = '2' AND CTA_NIVEL_2 IN ('21', '22', '23', '24', '25') THEN MOVIMIENTO_NETO ELSE 0 END) AS mov_pasivo_cte,
                    SUM(CASE WHEN CTA_NIVEL_1 = '2' THEN MOVIMIENTO_NETO ELSE 0 END) AS mov_pasivo_total,
                    SUM(CASE WHEN CTA_NIVEL_1 = '3' THEN MOVIMIENTO_NETO ELSE 0 END) AS mov_patrimonio
                FROM V_BI_CONTABILIDAD_DASHBOARD
                WHERE PERIODO <= %(periodo)s
        """
        if self.id_centro_costo:
            query += " AND ID_CENTRO_COSTO = %(id_centro_costo)s "

        query += """
                GROUP BY PERIODO
            ),
            Acumulados AS (
                SELECT
                    PERIODO,
                    SUM(mov_activo_cte) OVER (ORDER BY PERIODO ASC) as activo_corriente,
                    ABS(SUM(mov_pasivo_cte) OVER (ORDER BY PERIODO ASC)) as pasivo_corriente,
                    ABS(SUM(mov_pasivo_total) OVER (ORDER BY PERIODO ASC)) as pasivo_total,
                    ABS(SUM(mov_patrimonio) OVER (ORDER BY PERIODO ASC)) as patrimonio
                FROM Base
            )
            SELECT * FROM (SELECT * FROM Acumulados ORDER BY PERIODO DESC) WHERE ROWNUM <= 12
        """
        params = {'periodo': self.periodo}
        if self.id_centro_costo: params['id_centro_costo'] = self.id_centro_costo

        resultados = self._ejecutar_consulta(query, params)
        resultados.reverse()

        return {
            'periodos': [r.get('periodo') for r in resultados],
            'activo_corriente': [float(r.get('activo_corriente') or 0) for r in resultados],
            'pasivo_corriente': [float(r.get('pasivo_corriente') or 0) for r in resultados],
            'pasivo_total': [float(r.get('pasivo_total') or 0) for r in resultados],
            'patrimonio': [float(r.get('patrimonio') or 0) for r in resultados]
        }

    def get_control_presupuestal(self):
        """Compara gastos reales vs presupuesto del mes actual, devolviendo el Top 10"""
        query = """
            WITH Real_Gastos AS (
                SELECT CTA_NIVEL_4, SUM(MOVIMIENTO_NETO) AS valor_real
                FROM V_BI_CONTABILIDAD_DASHBOARD
                WHERE CTA_NIVEL_1 = '5' AND PERIODO = %(periodo)s
        """
        if self.id_centro_costo: query += " AND ID_CENTRO_COSTO = %(id_centro_costo)s "
        
        query += """
                GROUP BY CTA_NIVEL_4
            ),
            Presupuesto AS (
                SELECT CTA_NIVEL_4, SUM(VALOR_PRESUPUESTADO) AS valor_presupuestado
                FROM BI_PRESUPUESTOS
                WHERE SUBSTR(CTA_NIVEL_4, 1, 1) = '5' AND PERIODO = %(periodo)s
        """
        if self.id_centro_costo: query += " AND ID_CENTRO_COSTO = %(id_centro_costo)s "
        
        query += """
                GROUP BY CTA_NIVEL_4
            )
            SELECT 
                NVL(R.CTA_NIVEL_4, P.CTA_NIVEL_4) AS cuenta,
                NVL(R.valor_real, 0) AS valor_real,
                NVL(P.valor_presupuestado, 0) AS valor_presupuestado
            FROM Real_Gastos R
            FULL OUTER JOIN Presupuesto P ON R.CTA_NIVEL_4 = P.CTA_NIVEL_4
            WHERE NVL(P.valor_presupuestado, 0) > 0 OR NVL(R.valor_real, 0) > 0
            ORDER BY NVL(R.valor_real, 0) DESC, NVL(P.valor_presupuestado, 0) DESC
        """
        params = {'periodo': self.periodo}
        if self.id_centro_costo: params['id_centro_costo'] = self.id_centro_costo
            
        resultados = self._ejecutar_consulta(query, params)
        
        # Mapeo estándar del PUC Colombiano para gastos (Clase 5)
        nombres_puc = {
            '5105': 'Personal (Admin)', '5205': 'Personal (Ventas)',
            '5110': 'Honorarios (Admin)', '5210': 'Honorarios (Ventas)',
            '5115': 'Impuestos (Admin)', '5215': 'Impuestos (Ventas)',
            '5120': 'Arrendamientos (Admin)', '5220': 'Arrendamientos (Ventas)',
            '5125': 'Contribuciones y Afil.', '5225': 'Contribuciones y Afil.',
            '5130': 'Seguros (Admin)', '5230': 'Seguros (Ventas)',
            '5135': 'Servicios (Admin)', '5235': 'Servicios (Ventas)',
            '5140': 'Gastos Legales', '5240': 'Gastos Legales',
            '5145': 'Mantenimiento (Admin)', '5245': 'Mantenimiento (Ventas)',
            '5150': 'Adecuaciones', '5250': 'Adecuaciones',
            '5155': 'Viajes (Admin)', '5255': 'Viajes (Ventas)',
            '5160': 'Depreciaciones', '5260': 'Depreciaciones',
            '5195': 'Diversos (Admin)', '5295': 'Diversos (Ventas)',
            '5305': 'Financieros', '5315': 'Gastos Extraordinarios',
            '5405': 'Impuesto de Renta'
        }
        
        total_real = sum(float(r['valor_real']) for r in resultados)
        total_presup = sum(float(r['valor_presupuestado']) for r in resultados)
        varianza = total_presup - total_real
        ejecucion = self._safe_div(Decimal(str(total_real)) * 100, Decimal(str(total_presup))) if total_presup > 0 else Decimal('0')

        # Extraer Top 10 para graficar
        top_10 = resultados[:10]
        
        return {
            'total_real': total_real,
            'total_presupuesto': total_presup,
            'varianza': varianza,
            'porcentaje_ejecucion': float(ejecucion),
            'cuentas': [f"{r['cuenta']} - {nombres_puc.get(r['cuenta'], 'Gastos Operativos')}" for r in top_10],
            'reales': [float(r['valor_real']) for r in top_10],
            'presupuestos': [float(r['valor_presupuestado']) for r in top_10]
        }

    def get_datos_tablero(self):
        return {
            'periodo_actual': self.periodo,
            'kpis': self.get_kpis_completos(),
            'tendencias': self.get_tendencias_historicas(),
            'presupuesto': self.get_control_presupuestal()
        }

