# -*- coding: utf-8 -*-
"""SPEC-013 Fase 2: pruebas de la regla de cuantías menores y la exportación (sin acceso a Oracle)."""
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase
from openpyxl import load_workbook

from reportes import services_exogena as svc


def _fila(concepto, columna, nit, razon, monto, tope, tercero=1, tipo='31'):
    return {
        'ID_ANO': '2025', 'FORMATO_DIAN': '1001', 'CONCEPTO_DIAN': concepto, 'COLUMNA_XML': columna,
        'TOPE_CUANTIA_MENOR': tope, 'ID_TERCERO': tercero, 'NIT': nit, 'RAZON_SOCIAL': razon,
        'TIPO_DOC_DIAN': tipo, 'MONTO_ANUALIZADO': monto,
    }


class CuantiasMenoresTests(SimpleTestCase):
    def test_consolida_nits_bajo_el_tope_por_concepto_y_columna(self):
        registros = [
            _fila('5001', 'pago', '900111', 'GRANDE SAS', 5000000, 100000),
            _fila('5001', 'pago', '800222', 'PEQUENO A', 30000, 100000),
            _fila('5001', 'pago', '800333', 'PEQUENO B', 20000.5, 100000),
            _fila('5002', 'pago', '800222', 'PEQUENO A', 10000, 100000),
        ]
        r = svc.procesar_cuantias_menores(registros)
        filas = {(f['concepto_dian'], f['nit']): f for f in r['filas']}

        self.assertEqual(len(r['filas']), 3)
        self.assertEqual(filas[('5001', '900111')]['monto'], Decimal('5000000.00'))
        cm = filas[('5001', svc.NIT_CUANTIAS_MENORES)]
        self.assertEqual(cm['razon_social'], 'CUANTIAS MENORES')
        self.assertEqual(cm['monto'], Decimal('50000.50'))
        self.assertTrue(cm['es_cuantia_menor'])
        self.assertEqual(filas[('5002', svc.NIT_CUANTIAS_MENORES)]['monto'], Decimal('10000.00'))
        self.assertEqual(r['resumen']['terceros_consolidados'], 2)
        self.assertEqual(r['resumen']['monto_total'], Decimal('5060000.50'))

    def test_el_tope_se_evalua_sobre_el_total_del_nit_en_el_formato(self):
        # 60k + 60k = 120k >= tope 100k  => NO es cuantía menor aunque cada fila sea < tope.
        registros = [
            _fila('5001', 'pago', '800222', 'TERCERO', 60000, 100000),
            _fila('5002', 'pago', '800222', 'TERCERO', 60000, 100000),
        ]
        r = svc.procesar_cuantias_menores(registros)
        self.assertEqual(r['resumen']['terceros_consolidados'], 0)
        self.assertTrue(all(f['nit'] == '800222' for f in r['filas']))

    def test_monto_igual_al_tope_no_es_cuantia_menor_y_tope_cero_nunca_consolida(self):
        registros = [
            _fila('5001', 'pago', '800222', 'IGUAL', 100000, 100000),
            _fila('5001', 'pago', '800333', 'SIN TOPE', 1, 0),
            _fila('5001', 'pago', '800444', 'TOPE NULO', 1, None),
        ]
        r = svc.procesar_cuantias_menores(registros)
        self.assertEqual(r['resumen']['terceros_consolidados'], 0)
        self.assertEqual(len(r['filas']), 3)

    def test_suma_sucursales_del_mismo_nit_y_no_pierde_montos(self):
        registros = [
            _fila('5001', 'pago', '900111', 'GRANDE', 400000, 100000, tercero=1),
            _fila('5001', 'pago', '900111', 'GRANDE', 600000, 100000, tercero=2),
        ]
        r = svc.procesar_cuantias_menores(registros)
        self.assertEqual(len(r['filas']), 1)
        self.assertEqual(r['filas'][0]['monto'], Decimal('1000000.00'))

    def test_cuantias_menores_van_al_final_de_cada_concepto_columna(self):
        registros = [
            _fila('5001', 'pago', '100', 'PEQ', 10, 1000),
            _fila('5001', 'pago', '999', 'GRANDE', 9000, 1000),
        ]
        r = svc.procesar_cuantias_menores(registros)
        self.assertEqual([f['nit'] for f in r['filas']], ['999', svc.NIT_CUANTIAS_MENORES])

    def test_sin_registros(self):
        r = svc.procesar_cuantias_menores([])
        self.assertEqual(r['filas'], [])
        self.assertEqual(r['resumen']['total_filas'], 0)


class ValidacionYExcelTests(SimpleTestCase):
    def test_validar_parametros(self):
        self.assertEqual(svc.validar_parametros(2025, ' 1001 '), ('2025', '1001'))
        for ano, fmt in (('25', '1001'), ('2025', ''), ('2025', "1001'; DROP"), (None, '1001')):
            with self.assertRaises(svc.ParametrosInvalidosError):
                svc.validar_parametros(ano, fmt)

    def test_excel_generado(self):
        registros = [
            _fila('5001', 'pago', '900111', '=CMD()', 5000000, 100000),
            _fila('5001', 'pago', '800222', 'PEQUENO', 30000, 100000),
        ]
        datos = svc.procesar_cuantias_menores(registros)
        datos.update(ano='2025', formato='1001')
        resp = svc.generar_excel_response(datos)
        self.assertIn('Exogena_Formato_1001_2025.xlsx', resp['Content-Disposition'])
        wb = load_workbook(BytesIO(resp.content))
        ws = wb['Formato 1001']
        self.assertEqual([c.value for c in ws[1]][:3], ['Concepto', 'NIT', 'Razón Social'])
        self.assertEqual(ws['C2'].value, '=CMD()')
        self.assertEqual(ws['C2'].data_type, 's')       # no se interpreta como fórmula
        self.assertEqual(ws['B3'].value, svc.NIT_CUANTIAS_MENORES)
        self.assertEqual(ws['F3'].value, 30000.0)
        self.assertIn('Resumen', wb.sheetnames)


class VistasTests(SimpleTestCase):
    def test_api_datos_parametros_invalidos(self):
        r = self.client.get('/reportes/exogena/auditoria/api/datos/?ano=x&formato=1001')
        self.assertEqual(r.status_code, 400)

    def test_excel_sin_datos_responde_404_json(self):
        with patch.object(svc, 'consultar_vista', return_value=[]):
            r = self.client.get('/reportes/exogena/auditoria/api/excel/?ano=2025&formato=1001')
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()['ok'])

    def test_excel_con_datos(self):
        with patch.object(svc, 'consultar_vista', return_value=[_fila('5001', 'pago', '900', 'X', 500, 100)]):
            r = self.client.get('/reportes/exogena/auditoria/api/excel/?ano=2025&formato=1001')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], svc.MIME_XLSX)

    def test_api_datos_paginada(self):
        regs = [_fila('5001', 'pago', str(1000 + i), 'T%d' % i, 1000, 100) for i in range(150)]
        with patch.object(svc, 'consultar_vista', return_value=regs):
            r = self.client.get('/reportes/exogena/auditoria/api/datos/?ano=2025&formato=1001&page=2')
        d = r.json()
        self.assertEqual(len(d['items']), 50)
        self.assertEqual(d['paginacion']['total_paginas'], 2)

    def test_error_de_bd(self):
        with patch.object(svc, 'consultar_vista', side_effect=DatabaseError('x')):
            r = self.client.get('/reportes/exogena/auditoria/api/datos/?ano=2025&formato=1001')
        self.assertEqual(r.status_code, 500)

    def test_pagina_principal_renderiza(self):
        with patch.object(svc, 'obtener_opciones', return_value=[{'ano': '2025', 'formato': '1001'}]):
            r = self.client.get('/reportes/exogena/auditoria/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Exportar Formato a Excel')
        self.assertContains(r, 'Auditoría Exógena')   # enlace del menú lateral
