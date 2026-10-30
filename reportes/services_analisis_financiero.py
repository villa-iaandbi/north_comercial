# -*- coding: utf-8 -*-
import datetime
from decimal import Decimal, ROUND_HALF_UP
from django.db import connection


def estructurar_balance_comprobacion(saldos_crudos, mapa_cuentas=None):
    """
    Convierte los saldos crudos en un array aplanado con jerarquía y terceros.
    saldos_crudos debe ser una lista de dicts con: codigo, saldo_inicial, debitos, creditos, saldo_final, nit, nom_tercero
    """
    agrupados = {}
    terceros = []
    conceptos_locales = dict(mapa_cuentas or {})

    # 1. Agrupar saldos por jerarquía contable
    for fila in saldos_crudos:
        codigo = str(fila.get('codigo') or fila.get('cta_nivel_8') or fila.get('cta_nivel_6') or fila.get('cta_nivel_4') or '').strip()
        if not codigo:
            continue

        s_ini = Decimal(str(fila.get('saldo_inicial') or '0.0'))
        deb = Decimal(str(fila.get('debitos') or '0.0'))
        cred = Decimal(str(fila.get('creditos') or '0.0'))
        s_fin = Decimal(str(fila.get('saldo_final') or '0.0'))

        if fila.get('nom_cuenta') and codigo not in conceptos_locales:
            conceptos_locales[codigo] = str(fila['nom_cuenta']).strip()
        
        # Acumular en los niveles (1, 2, 4, 6, 8 dígitos)
        for i in range(1, len(codigo) + 1):
            padre = codigo[:i]
            if len(padre) in [1, 2, 4, 6, 8]:
                if padre not in agrupados:
                    agrupados[padre] = {'ini': Decimal('0.0'), 'deb': Decimal('0.0'), 'cred': Decimal('0.0'), 'fin': Decimal('0.0')}
                
                agrupados[padre]['ini'] += s_ini
                agrupados[padre]['deb'] += deb
                agrupados[padre]['cred'] += cred
                agrupados[padre]['fin'] += s_fin

        # 2. Guardar el nodo del tercero de forma aislada
        # Validar si la fila trae movimiento de un tercero específico
        nit_val = fila.get('nit')
        if nit_val and str(nit_val).strip() != '':
            nom_tercero_val = str(fila.get('nom_tercero') or fila.get('nombre_tercero') or 'TERCERO NO REPORTADO').strip()
            terceros.append({
                'padre_codigo': codigo,
                'codigo': str(nit_val).strip(),
                'nit': str(nit_val).strip(),
                'concepto': nom_tercero_val,
                'nom_tercero': nom_tercero_val,
                'es_tercero': True,
                'saldo_inicial': float(abs(s_ini)),
                'debitos': float(abs(deb)),
                'creditos': float(abs(cred)),
                'saldo_final': float(abs(s_fin)),
                'nivel': 6, # Nivel visual para la sangría profunda en Alpine
                'tiene_hijos': False,
                'expandido': False,
                'visible': False
            })

    # 3. Ensamblar array final ordenado (Cuentas + Terceros)
    cuentas_balance = []
    codigos_ordenados = sorted(agrupados.keys())
    
    for cod in codigos_ordenados:
        datos = agrupados[cod]
        if datos['ini'] == Decimal('0.0') and datos['deb'] == Decimal('0.0') and datos['cred'] == Decimal('0.0') and datos['fin'] == Decimal('0.0'):
            continue

        nivel = 1 if len(cod) == 1 else 2 if len(cod) == 2 else 3 if len(cod) == 4 else 4 if len(cod) == 6 else 5
        
        # ¿Tiene subcuentas?
        tiene_hijos = any(c != cod and c.startswith(cod) for c in codigos_ordenados)
        
        # Filtrar los terceros que pertenecen específicamente a esta cuenta
        terceros_hijos = [t for t in terceros if t['padre_codigo'] == cod]
        if terceros_hijos:
            tiene_hijos = True # Forzar la flecha de despliegue si tiene terceros
        
        # Agregar cuenta contable con llamado real al maestro de cuentas (CO_CUENTAS)
        cuentas_balance.append({
            'codigo': cod,
            'concepto': conceptos_locales.get(cod, f"CUENTA {cod}"),
            'es_tercero': False,
            'saldo_inicial': float(abs(datos['ini'])),
            'debitos': float(abs(datos['deb'])),
            'creditos': float(abs(datos['cred'])),
            'saldo_final': float(abs(datos['fin'])),
            'nivel': nivel,
            'tiene_hijos': tiene_hijos,
            'expandido': False,
            'visible': nivel == 1 # Iniciar colapsado mostrando solo Clase 1, 2, 3...
        })
        
        # Insertar los terceros inmediatamente debajo de su cuenta padre
        if terceros_hijos:
            # Ordenar terceros por nombre
            terceros_hijos.sort(key=lambda x: x['concepto'])
            cuentas_balance.extend(terceros_hijos)
            
    return cuentas_balance

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
        self.periodo_inicio = self.periodo_inicio_anio
        self.periodo_fin = self.periodo

        # Soporte para filtros por rango de fechas (YYYY-MM-DD) en Balance de Comprobación (SPEC-012)
        fecha_ini_raw = str(self.filtros.get('fecha_inicio') or '').strip()
        fecha_fin_raw = str(self.filtros.get('fecha_fin') or '').strip()
        if len(fecha_ini_raw) >= 7 and '-' in fecha_ini_raw:
            partes_ini = fecha_ini_raw.split('-')
            self.anio = partes_ini[0]
            self.periodo_inicio = f"{partes_ini[0]}{int(partes_ini[1]):02d}"
            self.periodo_inicio_anio = f"{partes_ini[0]}01"
        if len(fecha_fin_raw) >= 7 and '-' in fecha_fin_raw:
            partes_fin = fecha_fin_raw.split('-')
            self.periodo_fin = f"{partes_fin[0]}{int(partes_fin[1]):02d}"
            self.periodo = self.periodo_fin

        self.id_centro_costo = self.filtros.get('id_centro_costo')
        self._saldos_crudos_cache = None
        self._tablero_procesado_cache = None

    def _ejecutar_consulta(self, query, params):
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0].lower() for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _safe_div(self, numerador, denominador):
        if not denominador or denominador == 0:
            return Decimal('0.00')
        return (numerador / denominador).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def extraer_saldos_db(self):
        """
        ÚNICA consulta a V_BI_CONTABILIDAD_DASHBOARD cruzada dinámicamente con CO_CUENTAS (PUC)
        para extraer saldos y nombres oficiales en todos los niveles (1, 2, 4, 6, 8) sin diccionarios quemados.
        """
        if self._saldos_crudos_cache is not None:
            return self._saldos_crudos_cache

        query = """
            WITH Puc AS (
                SELECT 
                    TRIM(COD_CUENTA) AS COD_CUENTA, 
                    MAX(TRIM(UPPER(NOM_CUENTA))) AS NOM_CUENTA
                FROM CO_CUENTAS
                WHERE ID_SISTEMA = '1'
                  AND LENGTH(TRIM(COD_CUENTA)) IN (1, 2, 4, 6, 8)
                GROUP BY TRIM(COD_CUENTA)
            )
            SELECT 
                SAL.CTA_NIVEL_1,
                NVL(P1.NOM_CUENTA, 'CLASE ' || SAL.CTA_NIVEL_1) AS NOM_NIVEL_1,
                SAL.CTA_NIVEL_2,
                NVL(P2.NOM_CUENTA, 'GRUPO ' || SAL.CTA_NIVEL_2) AS NOM_NIVEL_2,
                SAL.CTA_NIVEL_4,
                NVL(P4.NOM_CUENTA, 'CUENTA ' || SAL.CTA_NIVEL_4) AS NOM_NIVEL_4,
                SAL.CTA_NIVEL_6,
                NVL(P6.NOM_CUENTA, 'SUBCUENTA ' || SAL.CTA_NIVEL_6) AS NOM_NIVEL_6,
                SAL.CTA_NIVEL_8,
                NVL(P8.NOM_CUENTA, MAX(SAL.NOM_CUENTA)) AS NOM_NIVEL_8,
                SUM(SAL.MOVIMIENTO_NETO) AS ACUMULADO
            FROM V_BI_CONTABILIDAD_DASHBOARD SAL
            LEFT JOIN Puc P1 ON SAL.CTA_NIVEL_1 = P1.COD_CUENTA
            LEFT JOIN Puc P2 ON SAL.CTA_NIVEL_2 = P2.COD_CUENTA
            LEFT JOIN Puc P4 ON SAL.CTA_NIVEL_4 = P4.COD_CUENTA
            LEFT JOIN Puc P6 ON SAL.CTA_NIVEL_6 = P6.COD_CUENTA
            LEFT JOIN Puc P8 ON SAL.CTA_NIVEL_8 = P8.COD_CUENTA
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

        query += """
            GROUP BY 
                SAL.CTA_NIVEL_1, P1.NOM_CUENTA,
                SAL.CTA_NIVEL_2, P2.NOM_CUENTA,
                SAL.CTA_NIVEL_4, P4.NOM_CUENTA,
                SAL.CTA_NIVEL_6, P6.NOM_CUENTA,
                SAL.CTA_NIVEL_8, P8.NOM_CUENTA
            ORDER BY 
                SAL.CTA_NIVEL_1, SAL.CTA_NIVEL_2, SAL.CTA_NIVEL_4, SAL.CTA_NIVEL_6, SAL.CTA_NIVEL_8
        """
        self._saldos_crudos_cache = self._ejecutar_consulta(query, params)
        return self._saldos_crudos_cache

    def construir_arbol_pyg_aplanado(self, saldos_crudos=None):
        """
        Construye la lista aplanada `cuentas_pyg` con profundidad completa (niveles 1 a 5)
        aplicando consolidación algebraica y ajuste de signo al final.
        """
        if saldos_crudos is None:
            saldos_crudos = self.extraer_saldos_db()

        # 1. Primero, sumar algebraicamente agrupando por código (sin alterar signos)
        saldos_agrupados = {}
        conceptos_agrupados = {}

        for fila in saldos_crudos:
            n1 = str(fila.get('cta_nivel_1') or '').strip()
            if n1 not in ('4', '5', '6', '7'):
                continue

            n2 = str(fila.get('cta_nivel_2') or '').strip()
            n4 = str(fila.get('cta_nivel_4') or '').strip()
            n6 = str(fila.get('cta_nivel_6') or '').strip()
            n8 = str(fila.get('cta_nivel_8') or '').strip()

            codigo = n8 or n6 or n4 or n2 or n1
            raw_val = fila.get('acumulado') if 'acumulado' in fila else fila.get('total')
            saldo_real = Decimal(str(raw_val or 0))  # NO usar abs() aquí

            if saldo_real == Decimal('0'):
                continue

            if n1:
                conceptos_agrupados[n1] = str(fila.get('nom_nivel_1') or f'CLASE {n1}').strip()
            if n2:
                conceptos_agrupados[n2] = str(fila.get('nom_nivel_2') or f'GRUPO {n2}').strip()
            if n4:
                conceptos_agrupados[n4] = str(fila.get('nom_nivel_4') or f'CUENTA {n4}').strip()
            if n6:
                conceptos_agrupados[n6] = str(fila.get('nom_nivel_6') or f'SUBCUENTA {n6}').strip()
            if n8:
                conceptos_agrupados[n8] = str(fila.get('nom_nivel_8') or f'AUXILIAR {n8}').strip()

            # Acumular en el código específico y en sus padres
            # (Ej: si es 417505, suma en 417505, en 4175, en 41 y en 4)
            for i in range(1, len(codigo) + 1):
                padre = codigo[:i]
                # Filtrar para que solo agrupe niveles válidos contables (1, 2, 4, 6...)
                if len(padre) in [1, 2, 4, 6, 8]:
                    saldos_agrupados[padre] = saldos_agrupados.get(padre, Decimal('0.0')) + saldo_real

        # Filtrar códigos con saldo neto distinto de cero para el cálculo de jerarquía
        saldos_agrupados = {k: v for k, v in saldos_agrupados.items() if v != Decimal('0')}

        # 2. Construir el array final ajustando la presentación y jerarquía profunda
        cuentas_pyg = []
        
        # Ordenar las llaves garantiza que el árbol se renderice en orden contable estricto
        codigos_ordenados = sorted(saldos_agrupados.keys())
        
        for cod in codigos_ordenados:
            saldo_neto = saldos_agrupados[cod]
            saldo_presentacion = float(abs(saldo_neto))
            
            # Asignación de niveles: 1(Clase), 2(Grupo), 3(Cuenta), 4(Subcuenta), 5(Auxiliar)
            nivel = 1 if len(cod) == 1 else 2 if len(cod) == 2 else 3 if len(cod) == 4 else 4 if len(cod) == 6 else 5
            
            # Cálculo dinámico: ¿Hay algún código en la lista que empiece con este pero sea más largo?
            tiene_hijos = any(c != cod and c.startswith(cod) for c in codigos_ordenados)
            
            # AQUÍ: Mantén tu lógica existente para obtener el nombre de la cuenta
            concepto = conceptos_agrupados.get(cod, f'CUENTA {cod}')
            
            cuentas_pyg.append({
                'codigo': cod,
                'concepto': concepto,
                'saldo': saldo_presentacion,
                'nivel': nivel,
                'tiene_hijos': tiene_hijos,
                'expandido': False,
                'visible': nivel == 1 # Alpine inicia mostrando solo la clase (Nivel 1)
            })

        return cuentas_pyg, cuentas_pyg

    def procesar_tablero_financiero(self, saldos_crudos=None):
        """
        Single Source of Truth (Única Fuente de Verdad) para el Centro de Mando Financiero (SPEC-010).
        """
        if saldos_crudos is None and self._tablero_procesado_cache is not None:
            return self._tablero_procesado_cache

        if saldos_crudos is None:
            saldos_crudos = self.extraer_saldos_db()

        # 1. Construir la fuente de verdad (Lista de diccionarios `cuentas_pyg` y `arbol_pyg`)
        cuentas_pyg, lista_arbol = self.construir_arbol_pyg_aplanado(saldos_crudos)

        # -------------------------------------------------------------------------
        # INICIO CÁLCULO DE CASCADA (ECHARTS) DESDE LA FUENTE DE VERDAD (cuentas_pyg)
        # -------------------------------------------------------------------------
        def obtener_saldo(codigo_buscado, nivel_buscado):
            """Busca el saldo exacto de una cuenta en el array ya generado para Alpine."""
            for c in cuentas_pyg:
                if c['codigo'] == str(codigo_buscado) and (c.get('nivel') == nivel_buscado or len(c['codigo']) == nivel_buscado):
                    return float(c['saldo'])
            return 0.0

        # 1. Ventas Netas (La cuenta 4 ya trae las devoluciones descontadas de BD)
        ventas_netas = obtener_saldo('4', 1)
        
        # 2. Costos y Gastos Operativos
        costos = obtener_saldo('6', 1)
        g_admin = obtener_saldo('51', 2)
        
        # 3. Gastos de Ventas (Criterio SPEC-010: Excluimos depreciación 5260 para el EBITDA)
        g_ventas_total = obtener_saldo('52', 2)
        depreciacion_ventas = obtener_saldo('5260', 4)
        g_ventas_ebitda = g_ventas_total - depreciacion_ventas
        
        # 4. No Operacionales e Impuestos
        otros_ing = obtener_saldo('42', 2)
        g_finan = obtener_saldo('53', 2)
        impuestos = obtener_saldo('54', 2)
        
        # 5. Utilidad Neta de la Gráfica
        util_neta = (ventas_netas + otros_ing) - (costos + g_admin + g_ventas_ebitda + g_finan + impuestos)

        cascada_data = {
            'ventas': ventas_netas,
            'costos': costos,
            'g_admin': g_admin,
            'g_ventas': g_ventas_ebitda,
            'otros_ing': otros_ing,
            'g_finan': g_finan,
            'impuestos': impuestos,
            'util_neta': util_neta
        }
        # -------------------------------------------------------------------------

        metricas_decimales = {
            'ventas_netas': Decimal(str(ventas_netas)),
            'costos': Decimal(str(costos)),
            'gastos_admin': Decimal(str(g_admin)),
            'gastos_ventas_ebitda': Decimal(str(g_ventas_ebitda)),
            'depreciaciones': Decimal(str(depreciacion_ventas)),
            'otros_ingresos': Decimal(str(otros_ing)),
            'gastos_financieros': Decimal(str(g_finan)),
            'impuestos': Decimal(str(impuestos)),
            'utilidad_neta': Decimal(str(util_neta)),
        }

        resultado = (cuentas_pyg, cascada_data, lista_arbol, metricas_decimales)
        if saldos_crudos is self._saldos_crudos_cache:
            self._tablero_procesado_cache = resultado
        return resultado

    def get_kpis_completos(self):
        resultados = self.extraer_saldos_db()
        _, cascada_data, _, metricas_pyg = self.procesar_tablero_financiero(resultados)

        # 1. Variables de Balance (Acumuladas)
        activo_corriente = Decimal('0')
        activo_no_corriente = Decimal('0')
        pasivo_corriente = Decimal('0')
        pasivo_no_corriente = Decimal('0')
        patrimonio = Decimal('0')
        inventarios = Decimal('0')

        for f in resultados:
            n1, n2 = f.get('cta_nivel_1'), f.get('cta_nivel_2')
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

        # Convertimos las cuentas de naturaleza crédito a positivo SOLO al final para no romper los ratios
        pasivo_corriente = abs(pasivo_corriente)
        pasivo_no_corriente = abs(pasivo_no_corriente)
        patrimonio = abs(patrimonio)
        
        # 2. Variables de P&G derivadas de la Única Fuente de Verdad (procesar_tablero_financiero)
        ingresos_netos = metricas_pyg['ventas_netas']
        costo_ventas = metricas_pyg['costos']
        gastos_admin = metricas_pyg['gastos_admin']
        gastos_ventas = metricas_pyg['gastos_ventas_ebitda']
        depreciaciones = metricas_pyg['depreciaciones']
        ingresos_no_op = metricas_pyg['otros_ingresos']
        gastos_no_op = metricas_pyg['gastos_financieros']
        impuestos = metricas_pyg['impuestos']
        utilidad_neta = metricas_pyg['utilidad_neta']

        # Cálculos Derivados - Estructura
        activo_total = activo_corriente + activo_no_corriente
        pasivo_total = pasivo_corriente + pasivo_no_corriente
        
        # Cálculos Derivados - Resultados
        utilidad_bruta = ingresos_netos - costo_ventas
        ebitda = utilidad_bruta - gastos_admin - gastos_ventas
        utilidad_operativa = ebitda - depreciaciones

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
            
            # Detalle para Waterfall (Cascada) - Única Fuente de Verdad
            'detalle_cascada': cascada_data,
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
        """Compara gastos reales vs presupuesto del mes actual, devolviendo el Top 10 con nombres dinámicos desde CO_CUENTAS"""
        query = """
            WITH Puc AS (
                SELECT 
                    TRIM(COD_CUENTA) AS COD_CUENTA, 
                    MAX(TRIM(UPPER(NOM_CUENTA))) AS NOM_CUENTA
                FROM CO_CUENTAS
                WHERE ID_SISTEMA = '1'
                  AND LENGTH(TRIM(COD_CUENTA)) = 4
                GROUP BY TRIM(COD_CUENTA)
            ),
            Real_Gastos AS (
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
                NVL(C.NOM_CUENTA, 'CUENTA ' || NVL(R.CTA_NIVEL_4, P.CTA_NIVEL_4)) AS nombre_cuenta,
                NVL(R.valor_real, 0) AS valor_real,
                NVL(P.valor_presupuestado, 0) AS valor_presupuestado
            FROM Real_Gastos R
            FULL OUTER JOIN Presupuesto P ON R.CTA_NIVEL_4 = P.CTA_NIVEL_4
            LEFT JOIN Puc C ON NVL(R.CTA_NIVEL_4, P.CTA_NIVEL_4) = C.COD_CUENTA
            WHERE NVL(P.valor_presupuestado, 0) > 0 OR NVL(R.valor_real, 0) > 0
            ORDER BY NVL(R.valor_real, 0) DESC, NVL(P.valor_presupuestado, 0) DESC
        """
        params = {'periodo': self.periodo}
        if self.id_centro_costo: params['id_centro_costo'] = self.id_centro_costo
            
        resultados = self._ejecutar_consulta(query, params)
        
        total_real = sum(Decimal(str(r['valor_real'] or 0)) for r in resultados)
        total_presup = sum(Decimal(str(r['valor_presupuestado'] or 0)) for r in resultados)
        varianza = total_presup - total_real
        ejecucion = self._safe_div(total_real * Decimal('100'), total_presup) if total_presup > Decimal('0') else Decimal('0.00')

        # Extraer Top 10 para graficar
        top_10 = resultados[:10]
        
        return {
            'total_real': float(total_real),
            'total_presupuesto': float(total_presup),
            'varianza': float(varianza),
            'porcentaje_ejecucion': float(ejecucion),
            'cuentas': [f"{r['cuenta']} - {r.get('nombre_cuenta') or 'GASTOS OPERATIVOS'}" for r in top_10],
            'reales': [float(r['valor_real']) for r in top_10],
            'presupuestos': [float(r['valor_presupuestado']) for r in top_10]
        }

    def get_distribucion_gastos(self, prefijo_cuenta):
        """Genera distribución Top 10 + Otros para ECharts Doughnut desde la Única Fuente de Verdad (cuentas_pyg)"""
        cuentas_pyg, _, _, _ = self.procesar_tablero_financiero()
        filas_grupo = [
            c for c in cuentas_pyg
            if len(str(c.get('codigo') or '')) == 4 and str(c.get('codigo') or '').startswith(str(prefijo_cuenta))
        ]
        filas_grupo.sort(key=lambda c: Decimal(str(c.get('saldo') or 0)), reverse=True)

        datos_formateados = []
        otros_total = Decimal('0.00')

        for i, row in enumerate(filas_grupo):
            valor_dec = Decimal(str(row.get('saldo') or 0))
            if valor_dec == Decimal('0'):
                continue

            if i < 10:
                cta4 = str(row.get('codigo') or '').strip()
                nom4 = str(row.get('concepto') or f'CUENTA {cta4}').strip()
                nombre = f"{cta4} - {nom4}"
                datos_formateados.append({'value': float(valor_dec), 'name': nombre})
            else:
                otros_total += valor_dec

        if otros_total > Decimal('0'):
            datos_formateados.append({
                'value': float(otros_total),
                'name': 'Otras Subcuentas (Cola Larga)',
                'itemStyle': {'color': '#374151'}
            })

        return datos_formateados

    def get_arbol_pyg(self):
        """Retorna el árbol jerárquico Clase -> Grupo -> Cuenta desde la Única Fuente de Verdad"""
        _, _, lista_arbol, _ = self.procesar_tablero_financiero()
        return lista_arbol

    def get_cuentas_pyg_plano(self, arbol=None):
        """Retorna la lista aplanada `cuentas_pyg` con atributo `nivel` desde la Única Fuente de Verdad"""
        cuentas_pyg, _, _, _ = self.procesar_tablero_financiero()
        return cuentas_pyg

    def get_datos_tablero(self):
        cuentas_pyg, cascada_data, lista_arbol, _ = self.procesar_tablero_financiero()
        return {
            'periodo_actual': self.periodo,
            'kpis': self.get_kpis_completos(),
            'tendencias': self.get_tendencias_historicas(),
            'presupuesto': self.get_control_presupuestal(),
            'gastos_admin_pie': self.get_distribucion_gastos('51'),
            'gastos_ventas_pie': self.get_distribucion_gastos('52'),
            'arbol_pyg': lista_arbol,
            'cuentas_pyg': cuentas_pyg,
            'cuentasPyG': cuentas_pyg,
            'cascada_data': cascada_data,
            'cascadaData': cascada_data,
        }

    def get_balance_comprobacion(self):
        """
        Extrae los saldos de V_BI_CONTABILIDAD_DASHBOARD (con desglose por cuenta y tercero)
        y el maestro de cuentas CO_CUENTAS para estructurar el Balance de Comprobación (SPEC-012).
        """
        query_puc = """
            SELECT 
                TRIM(COD_CUENTA) AS COD_CUENTA, 
                MAX(TRIM(UPPER(NOM_CUENTA))) AS NOM_CUENTA
            FROM CO_CUENTAS
            WHERE ID_SISTEMA = '1'
              AND LENGTH(TRIM(COD_CUENTA)) IN (1, 2, 4, 6, 8)
            GROUP BY TRIM(COD_CUENTA)
        """
        filas_puc = self._ejecutar_consulta(query_puc, {})
        mapa_cuentas = {str(r['cod_cuenta']).strip(): str(r['nom_cuenta']).strip() for r in filas_puc if r.get('cod_cuenta')}

        query_saldos = """
            SELECT
                NVL(TRIM(SAL.CTA_NIVEL_8), NVL(TRIM(SAL.CTA_NIVEL_6), TRIM(SAL.CTA_NIVEL_4))) AS CODIGO,
                MAX(SAL.NOM_CUENTA) AS NOM_CUENTA,
                NVL(TRIM(SAL.NIT), TRIM(TO_CHAR(SAL.ID_TERCERO))) AS ID_TERCERO,
                MAX(SAL.NOM_TERCERO) AS NOMBRE_TERCERO,
                SUM(
                    CASE 
                        WHEN SAL.CTA_NIVEL_1 IN ('1', '2', '3') AND SAL.PERIODO < %(periodo_inicio)s THEN SAL.MOVIMIENTO_NETO
                        WHEN SAL.CTA_NIVEL_1 IN ('4', '5', '6', '7') AND SAL.PERIODO >= %(inicio_anio)s AND SAL.PERIODO < %(periodo_inicio)s THEN SAL.MOVIMIENTO_NETO
                        ELSE 0 
                    END
                ) AS SALDO_INICIAL,
                SUM(
                    CASE 
                        WHEN SAL.PERIODO BETWEEN %(periodo_inicio)s AND %(periodo_fin)s THEN SAL.DEBITOS_MES 
                        ELSE 0 
                    END
                ) AS DEBITOS,
                SUM(
                    CASE 
                        WHEN SAL.PERIODO BETWEEN %(periodo_inicio)s AND %(periodo_fin)s THEN SAL.CREDITOS_MES 
                        ELSE 0 
                    END
                ) AS CREDITOS,
                SUM(
                    CASE 
                        WHEN SAL.CTA_NIVEL_1 IN ('1', '2', '3') AND SAL.PERIODO <= %(periodo_fin)s THEN SAL.MOVIMIENTO_NETO
                        WHEN SAL.CTA_NIVEL_1 IN ('4', '5', '6', '7') AND SAL.PERIODO BETWEEN %(inicio_anio)s AND %(periodo_fin)s THEN SAL.MOVIMIENTO_NETO
                        ELSE 0 
                    END
                ) AS SALDO_FINAL
            FROM V_BI_CONTABILIDAD_DASHBOARD SAL
            WHERE 
                (
                    (SAL.CTA_NIVEL_1 IN ('1', '2', '3') AND SAL.PERIODO <= %(periodo_fin)s)
                    OR
                    (SAL.CTA_NIVEL_1 IN ('4', '5', '6', '7') AND SAL.PERIODO BETWEEN %(inicio_anio)s AND %(periodo_fin)s)
                )
        """
        params = {
            'inicio_anio': f"{self.anio}01",
            'periodo_inicio': self.periodo_inicio,
            'periodo_fin': self.periodo_fin,
        }
        if self.id_centro_costo:
            query_saldos += " AND SAL.ID_CENTRO_COSTO = %(id_centro_costo)s"
            params['id_centro_costo'] = self.id_centro_costo

        query_saldos += """
            GROUP BY 
                NVL(TRIM(SAL.CTA_NIVEL_8), NVL(TRIM(SAL.CTA_NIVEL_6), TRIM(SAL.CTA_NIVEL_4))),
                NVL(TRIM(SAL.NIT), TRIM(TO_CHAR(SAL.ID_TERCERO)))
            HAVING 
                SUM(
                    CASE 
                        WHEN SAL.CTA_NIVEL_1 IN ('1', '2', '3') AND SAL.PERIODO < %(periodo_inicio)s THEN SAL.MOVIMIENTO_NETO
                        WHEN SAL.CTA_NIVEL_1 IN ('4', '5', '6', '7') AND SAL.PERIODO >= %(inicio_anio)s AND SAL.PERIODO < %(periodo_inicio)s THEN SAL.MOVIMIENTO_NETO
                        ELSE 0 
                    END
                ) <> 0
                OR SUM(CASE WHEN SAL.PERIODO BETWEEN %(periodo_inicio)s AND %(periodo_fin)s THEN SAL.DEBITOS_MES ELSE 0 END) <> 0
                OR SUM(CASE WHEN SAL.PERIODO BETWEEN %(periodo_inicio)s AND %(periodo_fin)s THEN SAL.CREDITOS_MES ELSE 0 END) <> 0
                OR SUM(
                    CASE 
                        WHEN SAL.CTA_NIVEL_1 IN ('1', '2', '3') AND SAL.PERIODO <= %(periodo_fin)s THEN SAL.MOVIMIENTO_NETO
                        WHEN SAL.CTA_NIVEL_1 IN ('4', '5', '6', '7') AND SAL.PERIODO BETWEEN %(inicio_anio)s AND %(periodo_fin)s THEN SAL.MOVIMIENTO_NETO
                        ELSE 0 
                    END
                ) <> 0
        """
        saldos_crudos = self._ejecutar_consulta(query_saldos, params)
        return estructurar_balance_comprobacion(saldos_crudos, mapa_cuentas=mapa_cuentas)

