# -*- coding: utf-8 -*-
"""
SPEC-013 (Fase 2): Servicio del Tablero de Auditoría Exógena.

Origen de datos: vista Oracle V_EXOGENA_PRECALCULADA
    ID_ANO, FORMATO_DIAN, CONCEPTO_DIAN, COLUMNA_XML, TOPE_CUANTIA_MENOR,
    ID_TERCERO, NIT, RAZON_SOCIAL, TIPO_DOC_DIAN, MONTO_ANUALIZADO

Regla fiscal de CUANTÍAS MENORES
    Para cada (año, formato) se totaliza el MONTO_ANUALIZADO por NIT (sumando todos sus
    conceptos / columnas XML / sucursales dentro del formato). Si ese total es MENOR al
    TOPE_CUANTIA_MENOR aplicable, TODAS las filas de ese NIT se reasignan a
        NIT = '222222222'  /  RAZON_SOCIAL = 'CUANTIAS MENORES'
    y se consolidan en UNA sola fila por (concepto, columna XML).

    * Tope aplicable a un NIT: el MAYOR TOPE_CUANTIA_MENOR entre sus filas del formato
      (el tope es un parámetro del formato; si se configuró distinto por concepto se usa
      el más alto de forma determinística).
    * Un tope nulo o <= 0 significa "sin cuantía menor": el NIT nunca se consolida.
    * La comparación se hace sobre el monto con signo (tal como lo entrega la vista).

Convenciones Oracle 11g del proyecto: SQL crudo con placeholders %s, sin FETCH FIRST / OFFSET.
"""
import logging
import re
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.db import connection
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

NIT_CUANTIAS_MENORES = '222222222'
RAZON_CUANTIAS_MENORES = 'CUANTIAS MENORES'
# Código DIAN 43: "Sin identificación del exterior o para uso definido por la DIAN".
TIPO_DOC_CUANTIAS_MENORES = '43'

VISTA_EXOGENA = 'V_EXOGENA_PRECALCULADA'
CERO = Decimal('0')
DOS_DECIMALES = Decimal('0.01')

_PATRON_ANO = re.compile(r'^\d{4}$')
_PATRON_FORMATO = re.compile(r'^[A-Za-z0-9_\-]{1,10}$')

MIME_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class ParametrosInvalidosError(ValueError):
    """Año o formato con un valor no permitido."""


# ---------------------------------------------------------------------- #
# Utilidades
# ---------------------------------------------------------------------- #
def _a_decimal(valor):
    """Convierte cualquier valor de Oracle (None, int, float, Decimal, str) a Decimal seguro."""
    if valor is None or valor == '':
        return CERO
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError):
        logger.warning('SPEC-013: valor numérico inválido en %s: %r', VISTA_EXOGENA, valor)
        return CERO


def _a_texto(valor):
    """Normaliza a str sin espacios laterales ('' si es None). Los números enteros no llevan '.0'."""
    if valor is None:
        return ''
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor).strip()


def validar_parametros(ano, formato):
    """Valida y normaliza (año, formato). Lanza ParametrosInvalidosError si no son válidos."""
    ano = _a_texto(ano)
    formato = _a_texto(formato)
    if not _PATRON_ANO.match(ano):
        raise ParametrosInvalidosError('El año fiscal debe tener 4 dígitos (ej. 2025).')
    if not _PATRON_FORMATO.match(formato):
        raise ParametrosInvalidosError('El formato DIAN no es válido.')
    return ano, formato


# ---------------------------------------------------------------------- #
# Acceso a datos
# ---------------------------------------------------------------------- #
def obtener_opciones():
    """
    Pares (año, formato) disponibles en la vista, para poblar los selectores.
    Retorna: [{'ano': '2025', 'formato': '1001'}, ...] ordenado por año DESC, formato ASC.
    """
    sql = (
        'SELECT DISTINCT ID_ANO, FORMATO_DIAN '
        'FROM {vista} '
        'WHERE ID_ANO IS NOT NULL AND FORMATO_DIAN IS NOT NULL'
    ).format(vista=VISTA_EXOGENA)

    with connection.cursor() as cursor:
        cursor.execute(sql)
        filas = cursor.fetchall()

    pares = {(_a_texto(ano), _a_texto(formato)) for ano, formato in filas}
    pares.discard(('', ''))
    ordenados = sorted(pares, key=lambda p: (p[1], p[0]))        # formato ASC
    ordenados = sorted(ordenados, key=lambda p: p[0], reverse=True)  # año DESC (estable)
    return [{'ano': ano, 'formato': formato} for ano, formato in ordenados]


def consultar_vista(ano, formato):
    """Lee las filas crudas de V_EXOGENA_PRECALCULADA para un año y formato. Retorna lista de dicts."""
    ano, formato = validar_parametros(ano, formato)
    sql = (
        'SELECT ID_ANO, FORMATO_DIAN, CONCEPTO_DIAN, COLUMNA_XML, TOPE_CUANTIA_MENOR, '
        'ID_TERCERO, NIT, RAZON_SOCIAL, TIPO_DOC_DIAN, MONTO_ANUALIZADO '
        'FROM {vista} '
        'WHERE ID_ANO = %s AND FORMATO_DIAN = %s'
    ).format(vista=VISTA_EXOGENA)

    with connection.cursor() as cursor:
        cursor.execute(sql, [ano, formato])
        columnas = [c[0].upper() for c in cursor.description]
        return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]


# ---------------------------------------------------------------------- #
# Regla fiscal: cuantías menores
# ---------------------------------------------------------------------- #
def procesar_cuantias_menores(registros):
    """
    Aplica la regla de cuantías menores sobre filas crudas de la vista (de UN solo formato/año).

    Retorna dict:
        'filas':   lista de dicts ordenada por concepto, columna XML y NIT (cuantías menores al
                   final de cada concepto/columna), con claves:
                   concepto_dian, nit, razon_social, tipo_doc_dian, columna_xml, monto,
                   es_cuantia_menor
        'resumen': totales para el tablero y la hoja "Resumen" del Excel.
    """
    # 1) Datos normalizados y total por NIT (a nivel de formato).
    normalizados = []
    total_por_nit = {}
    tope_por_nit = {}

    for r in registros:
        nit = _a_texto(r.get('NIT'))
        monto = _a_decimal(r.get('MONTO_ANUALIZADO'))
        tope = _a_decimal(r.get('TOPE_CUANTIA_MENOR'))
        fila = {
            'concepto_dian': _a_texto(r.get('CONCEPTO_DIAN')),
            'columna_xml': _a_texto(r.get('COLUMNA_XML')),
            'nit': nit,
            'razon_social': _a_texto(r.get('RAZON_SOCIAL')),
            'tipo_doc_dian': _a_texto(r.get('TIPO_DOC_DIAN')),
            'monto': monto,
        }
        normalizados.append(fila)
        total_por_nit[nit] = total_por_nit.get(nit, CERO) + monto
        if nit not in tope_por_nit or tope > tope_por_nit[nit]:
            tope_por_nit[nit] = tope

    # 2) NITs que caen en cuantías menores (total < tope y tope > 0).
    nits_menores = {
        nit for nit, total in total_por_nit.items()
        if tope_por_nit[nit] > CERO and total < tope_por_nit[nit]
    }

    # 3) Agrupación: detalle por (concepto, columna, nit) y consolidado por (concepto, columna).
    agrupado = OrderedDict()
    for fila in normalizados:
        es_menor = fila['nit'] in nits_menores
        if es_menor:
            llave = (fila['concepto_dian'], fila['columna_xml'], NIT_CUANTIAS_MENORES)
        else:
            llave = (fila['concepto_dian'], fila['columna_xml'], fila['nit'])

        destino = agrupado.get(llave)
        if destino is None:
            agrupado[llave] = {
                'concepto_dian': fila['concepto_dian'],
                'nit': NIT_CUANTIAS_MENORES if es_menor else fila['nit'],
                'razon_social': RAZON_CUANTIAS_MENORES if es_menor else fila['razon_social'],
                'tipo_doc_dian': TIPO_DOC_CUANTIAS_MENORES if es_menor else fila['tipo_doc_dian'],
                'columna_xml': fila['columna_xml'],
                'monto': fila['monto'],
                'es_cuantia_menor': es_menor,
            }
        else:
            destino['monto'] += fila['monto']
            if not destino['razon_social'] and fila['razon_social']:
                destino['razon_social'] = fila['razon_social']
            if not destino['tipo_doc_dian'] and fila['tipo_doc_dian']:
                destino['tipo_doc_dian'] = fila['tipo_doc_dian']

    filas = list(agrupado.values())
    for f in filas:
        f['monto'] = f['monto'].quantize(DOS_DECIMALES)
    filas.sort(key=lambda f: (f['concepto_dian'], f['columna_xml'], f['es_cuantia_menor'], f['nit']))

    monto_total = sum((f['monto'] for f in filas), CERO)
    monto_menores = sum((f['monto'] for f in filas if f['es_cuantia_menor']), CERO)
    resumen = {
        'total_filas': len(filas),
        'filas_cuantias_menores': sum(1 for f in filas if f['es_cuantia_menor']),
        'terceros_originales': len(total_por_nit),
        'terceros_consolidados': len(nits_menores),
        'monto_total': monto_total.quantize(DOS_DECIMALES),
        'monto_cuantias_menores': monto_menores.quantize(DOS_DECIMALES),
        'monto_terceros_detallados': (monto_total - monto_menores).quantize(DOS_DECIMALES),
    }
    return {'filas': filas, 'resumen': resumen}


def obtener_datos_procesados(ano, formato):
    """Punto de entrada del servicio: consulta la vista y aplica la regla de cuantías menores."""
    ano, formato = validar_parametros(ano, formato)
    registros = consultar_vista(ano, formato)
    resultado = procesar_cuantias_menores(registros)
    resultado['ano'] = ano
    resultado['formato'] = formato
    return resultado


# ---------------------------------------------------------------------- #
# Exportación a Excel
# ---------------------------------------------------------------------- #
def _escribir_texto(celda, valor):
    """Escribe SIEMPRE como texto (evita que un valor que empiece por '=' sea tratado como fórmula)."""
    celda.value = valor
    if isinstance(valor, str):
        celda.data_type = 's'
    celda.number_format = '@'


def generar_excel_response(datos):
    """
    Genera el .xlsx descargable a partir del resultado de obtener_datos_procesados().
    Hoja 1 "Formato <n>": Concepto, NIT, Razón Social, Tipo Doc, Columna XML, Monto.
    Hoja 2 "Resumen": totales de control para el contador.
    """
    ano = datos['ano']
    formato = datos['formato']
    filas = datos['filas']
    resumen = datos['resumen']

    borde = Border(*(Side(style='thin', color='D1D5DB') for _ in range(4)))
    fuente_cabecera = Font(bold=True, color='FFFFFF')
    relleno_cabecera = PatternFill(start_color='1E3A8A', end_color='1E3A8A', fill_type='solid')
    relleno_menores = PatternFill(start_color='FEF3C7', end_color='FEF3C7', fill_type='solid')
    formato_monto = '#,##0.00;[Red]-#,##0.00'

    wb = Workbook()

    # ------------------------- Hoja de detalle ------------------------- #
    ws = wb.active
    ws.title = ('Formato %s' % formato)[:31]
    cabeceras = ['Concepto', 'NIT', 'Razón Social', 'Tipo Doc DIAN', 'Columna XML', 'Monto Anualizado']
    ws.append(cabeceras)
    for celda in ws[1]:
        celda.font = fuente_cabecera
        celda.fill = relleno_cabecera
        celda.border = borde
        celda.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    for indice, f in enumerate(filas, start=2):
        _escribir_texto(ws.cell(row=indice, column=1), f['concepto_dian'])
        _escribir_texto(ws.cell(row=indice, column=2), f['nit'])
        _escribir_texto(ws.cell(row=indice, column=3), f['razon_social'])
        _escribir_texto(ws.cell(row=indice, column=4), f['tipo_doc_dian'])
        _escribir_texto(ws.cell(row=indice, column=5), f['columna_xml'])
        celda_monto = ws.cell(row=indice, column=6, value=float(f['monto']))
        celda_monto.number_format = formato_monto
        celda_monto.alignment = Alignment(horizontal='right')
        for col in range(1, 7):
            celda = ws.cell(row=indice, column=col)
            celda.border = borde
            if f['es_cuantia_menor']:
                celda.fill = relleno_menores

    anchos = [12, 16, 48, 14, 14, 20]
    for posicion, ancho in enumerate(anchos, start=1):
        ws.column_dimensions[get_column_letter(posicion)].width = ancho
    ws.freeze_panes = 'A2'
    if filas:
        ws.auto_filter.ref = 'A1:F%d' % (len(filas) + 1)

    # --------------------------- Hoja resumen -------------------------- #
    wr = wb.create_sheet('Resumen')
    wr.append(['Auditoría Exógena DIAN'])
    wr['A1'].font = Font(bold=True, size=14)
    wr.append([])
    lineas = [
        ('Año fiscal', ano),
        ('Formato DIAN', formato),
        ('Total de filas exportadas', resumen['total_filas']),
        ('Terceros originales (NIT distintos)', resumen['terceros_originales']),
        ('Terceros consolidados en cuantías menores', resumen['terceros_consolidados']),
        ('Filas de cuantías menores', resumen['filas_cuantias_menores']),
        ('Monto terceros detallados', float(resumen['monto_terceros_detallados'])),
        ('Monto cuantías menores (NIT %s)' % NIT_CUANTIAS_MENORES, float(resumen['monto_cuantias_menores'])),
        ('Monto total del formato', float(resumen['monto_total'])),
    ]
    for indice, (etiqueta, valor) in enumerate(lineas, start=3):
        celda_etiqueta = wr.cell(row=indice, column=1, value=etiqueta)
        celda_etiqueta.font = Font(bold=True)
        if isinstance(valor, str):
            _escribir_texto(wr.cell(row=indice, column=2), valor)
        else:
            celda_valor = wr.cell(row=indice, column=2, value=valor)
            if isinstance(valor, float):
                celda_valor.number_format = formato_monto
            celda_valor.alignment = Alignment(horizontal='right')
    wr.column_dimensions['A'].width = 48
    wr.column_dimensions['B'].width = 22

    nombre = 'Exogena_Formato_%s_%s.xlsx' % (formato, ano)
    respuesta = HttpResponse(content_type=MIME_XLSX)
    respuesta['Content-Disposition'] = 'attachment; filename="%s"' % nombre
    wb.save(respuesta)
    return respuesta
