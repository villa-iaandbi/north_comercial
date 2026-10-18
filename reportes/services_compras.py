# -*- coding: utf-8 -*-
import datetime
import logging
import math
from decimal import Decimal, InvalidOperation
from django.db import connection

logger = logging.getLogger(__name__)


class ServicioComprasSugeridas:
    """
    Servicio de negocio para el Tablero de Compras Inteligentes / Compras Sugeridas (SPEC-007).

    Calcula el sugerido de compra en cajas cerradas con precisión financiera (Decimal)
    a partir de V_ANALISIS_INVENTARIO y BI_FOTO_INVENTARIO_DIARIO:
      Sugerido_Cajas = ((ROTACION_DIARIA * (DIAS_SUGERIDOS + STOCK_MINIMO)) - EXISTENCIA - PEDIDOS_POR_LLEGAR) / EMBALAJE
    """

    def __init__(self, filtros: dict = None, grupo_codigo=None, dias_sugeridos: int = 30, stock_minimo: int = 15):
        filtros = filtros if isinstance(filtros, dict) else {}

        raw_grupo = (
            filtros.get('grupo_codigo')
            or filtros.get('grupo')
            or filtros.get('GRUPO')
            or filtros.get('proveedor')
            or grupo_codigo
        )
        self.grupo_codigo = str(raw_grupo).strip() if raw_grupo is not None else ''
        if self.grupo_codigo.upper() == 'TODOS':
            self.grupo_codigo = ''

        raw_dias = filtros.get('dias_sugeridos', dias_sugeridos)
        raw_stock = filtros.get('stock_minimo', stock_minimo)

        self.dias_sugeridos = self._to_decimal(raw_dias, default=Decimal('30'))
        if self.dias_sugeridos < Decimal('0'):
            self.dias_sugeridos = Decimal('30')

        self.stock_minimo = self._to_decimal(raw_stock, default=Decimal('15'))
        if self.stock_minimo < Decimal('0'):
            self.stock_minimo = Decimal('15')

    @staticmethod
    def _to_decimal(valor, default: Decimal = Decimal('0.0')) -> Decimal:
        if valor is None or valor == '':
            return default
        if isinstance(valor, Decimal):
            return valor
        try:
            return Decimal(str(valor).strip())
        except (InvalidOperation, ValueError, TypeError):
            return default

    @staticmethod
    def _format_fecha(valor_fecha) -> str:
        if valor_fecha is None:
            return ''
        if isinstance(valor_fecha, (datetime.date, datetime.datetime)):
            return valor_fecha.strftime('%d/%m/%Y')
        return str(valor_fecha)[:10]


    def get_filtros_disponibles(self) -> list:
        """
        Retorna la lista de Grupos (Proveedores) únicos activos para poblar el <select> del frontend.
        """
        grupos = []
        sql = """
            SELECT DISTINCT
                NVL(TRIM(UPPER(G.COD_GRUPO)), TRIM(UPPER(V.GRUPO))) AS GRUPO_CODIGO,
                TRIM(UPPER(V.GRUPO)) AS GRUPO_NOMBRE
            FROM V_ANALISIS_INVENTARIO V
            LEFT JOIN IN_ARTICULOS A ON V.ID_ARTICULO = A.ID_ARTICULO
            LEFT JOIN IN_GRUPOS G ON A.ID_GRUPO = G.ID_GRUPO
            WHERE V.GRUPO IS NOT NULL
              AND NVL(V.ESTADO, 'A') = 'A'
            ORDER BY TRIM(UPPER(V.GRUPO))
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                vistos = set()
                for row in cursor.fetchall():
                    cod = str(row[0] or '').strip()
                    nom = str(row[1] or '').strip()
                    if not nom or nom in vistos:
                        continue
                    vistos.add(nom)
                    grupos.append({
                        'id': nom,
                        'codigo': cod or nom,
                        'grupo_codigo': cod or nom,
                        'nombre': nom,
                        'grupo_nombre': nom,
                    })
        except Exception as e:
            logger.error("Error al obtener grupos disponibles en ServicioComprasSugeridas: %s", e)

        return grupos

    def get_datos_tablero(self) -> list:
        """
        Extrae el inventario filtrado por Grupo, calcula el sugerido en cajas cerradas
        y la clasificación ABC por línea, retornando una lista de diccionarios
        serializable por DjangoJSONEncoder.
        """
        sql = """
            SELECT
                V.FECHA_FOTO,
                V.ID_ARTICULO,
                V.REFERENCIA,
                V.NOM_ARTICULO,
                V.ESTADO,
                V.GRUPO,
                V.LINEA,
                V.EXISTENCIA,
                V.VLR_PROMEDIO,
                V.VALOR_TOTAL,
                V.ROTACION_DIARIA,
                V.FCH_ULT_VENTA,
                V.DIAS_SIN_VENTA,
                NVL(F.VTA_30, 0) AS VTA_30,
                NVL(F.VTA_60, 0) AS VTA_60,
                NVL(F.VTA_90, 0) AS VTA_90,
                NVL(F.PEDIDOS_POR_LLEGAR, 0) AS PEDIDOS_POR_LLEGAR,
                NVL(F.EMBALAJE, 1) AS EMBALAJE,
                F.FCH_ULT_COMPRA,
                G.COD_GRUPO
            FROM V_ANALISIS_INVENTARIO V
            LEFT JOIN BI_FOTO_INVENTARIO_DIARIO F
                ON V.ID_ARTICULO = F.ID_ARTICULO
               AND V.FECHA_FOTO = F.FECHA_FOTO
            LEFT JOIN IN_ARTICULOS A
                ON V.ID_ARTICULO = A.ID_ARTICULO
            LEFT JOIN IN_GRUPOS G
                ON A.ID_GRUPO = G.ID_GRUPO
            WHERE 1=1
        """
        params = []

        if self.grupo_codigo:
            filtro_upper = self.grupo_codigo.upper()
            sql += """
                AND (
                    TRIM(UPPER(V.GRUPO)) = %s
                    OR TRIM(UPPER(G.COD_GRUPO)) = %s
                    OR TRIM(UPPER(TO_CHAR(G.ID_GRUPO))) = %s
                )
            """
            params.extend([filtro_upper, filtro_upper, filtro_upper])

        registros = []
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                columns = [col[0].upper() for col in cursor.description] if cursor.description else []
                for row in cursor.fetchall():
                    registros.append(dict(zip(columns, row)))
        except Exception as e:
            logger.error("Error al consultar datos de compras sugeridas en V_ANALISIS_INVENTARIO: %s", e)
            raise e

        # Paso 1: Acumular el valor total de inventario por LINEA para clasificación ABC
        totales_por_linea = {}
        for reg in registros:
            linea_key = str(reg.get('LINEA') or 'SIN LINEA').strip().upper()
            val_inv = self._to_decimal(reg.get('VALOR_TOTAL'), Decimal('0.0'))
            if val_inv > Decimal('0.0'):
                totales_por_linea[linea_key] = totales_por_linea.get(linea_key, Decimal('0.0')) + val_inv

        # Paso 2: Cálculo O(N) de Sugerido en Cajas y Clasificación ABC
        dias_cobertura_total = self.dias_sugeridos + self.stock_minimo
        resultado = []

        for reg in registros:
            id_articulo = str(reg.get('ID_ARTICULO') or '').strip()
            referencia = str(reg.get('REFERENCIA') or '').strip()
            nom_articulo = str(reg.get('NOM_ARTICULO') or '').strip()
            estado = str(reg.get('ESTADO') or 'A').strip().upper()
            grupo = str(reg.get('GRUPO') or 'SIN GRUPO').strip()
            cod_grupo = str(reg.get('COD_GRUPO') or '').strip()
            linea = str(reg.get('LINEA') or 'SIN LINEA').strip()
            linea_key = linea.upper()

            existencia = self._to_decimal(reg.get('EXISTENCIA'), Decimal('0.0'))
            vlr_promedio = self._to_decimal(reg.get('VLR_PROMEDIO'), Decimal('0.0'))
            valor_total = self._to_decimal(reg.get('VALOR_TOTAL'), Decimal('0.0'))
            rotacion_diaria = self._to_decimal(reg.get('ROTACION_DIARIA'), Decimal('0.0'))
            pedidos_por_llegar = self._to_decimal(reg.get('PEDIDOS_POR_LLEGAR'), Decimal('0.0'))

            vta_30 = self._to_decimal(reg.get('VTA_30'), Decimal('0.0'))
            vta_60 = self._to_decimal(reg.get('VTA_60'), Decimal('0.0'))
            vta_90 = self._to_decimal(reg.get('VTA_90'), Decimal('0.0'))

            embalaje = self._to_decimal(reg.get('EMBALAJE'), Decimal('1.0'))
            if embalaje <= Decimal('0.0'):
                embalaje = Decimal('1.0')

            dias_sin_venta = int(self._to_decimal(reg.get('DIAS_SIN_VENTA'), Decimal('0')))
            fecha_foto = self._format_fecha(reg.get('FECHA_FOTO'))
            fch_ult_venta = self._format_fecha(reg.get('FCH_ULT_VENTA'))
            fch_ult_compra = self._format_fecha(reg.get('FCH_ULT_COMPRA'))

            # Fórmula de negocio:
            # Sugerido_Cajas = ((ROTACION_DIARIA * (DIAS_SUGERIDOS + STOCK_MINIMO)) - EXISTENCIA - PEDIDOS_POR_LLEGAR) / EMBALAJE
            demanda_objetivo = rotacion_diaria * dias_cobertura_total
            requerimiento_neto = demanda_objetivo - existencia - pedidos_por_llegar
            sugerido_cajas_decimal = requerimiento_neto / embalaje

            # Regla dura: Si es negativo o <= 0.01 se fuerza a 0; si supera 0.01 se redondea al entero superior (math.ceil)
            if sugerido_cajas_decimal <= Decimal('0.01'):
                sugerido_cajas = 0
            else:
                sugerido_cajas = int(math.ceil(sugerido_cajas_decimal))

            sugerido_unidades = Decimal(sugerido_cajas) * embalaje
            valor_compra_sugerida = round(sugerido_unidades * vlr_promedio, 2)

            # Clasificación ABC simplificada por Línea (> 10% del valor total de su línea => 'A')
            total_linea = totales_por_linea.get(linea_key, Decimal('0.0'))
            if total_linea > Decimal('0.0') and valor_total > Decimal('0.0'):
                participacion_linea = (valor_total / total_linea) * Decimal('100.0')
            else:
                participacion_linea = Decimal('0.0')

            if participacion_linea > Decimal('10.0'):
                clasificacion_abc = 'A'
            elif participacion_linea > Decimal('3.0'):
                clasificacion_abc = 'B'
            else:
                clasificacion_abc = 'C'

            embalaje_out = int(embalaje) if embalaje == int(embalaje) else float(round(embalaje, 2))

            resultado.append({
                'id_articulo': id_articulo,
                'referencia': referencia,
                'nom_articulo': nom_articulo,
                'estado': estado,
                'grupo': grupo,
                'grupo_codigo': cod_grupo or grupo,
                'linea': linea,
                'existencia': int(existencia) if existencia == int(existencia) else float(round(existencia, 2)),
                'vlr_promedio': round(vlr_promedio, 2),
                'costo_unitario': round(vlr_promedio, 2),
                'valor_total': round(valor_total, 2),
                'rotacion_diaria': round(rotacion_diaria, 4),
                'pedidos_por_llegar': round(pedidos_por_llegar, 2),
                'embalaje': embalaje_out,
                'vta_30': round(vta_30, 2),
                'vta_60': round(vta_60, 2),
                'vta_90': round(vta_90, 2),
                'dias_sin_venta': dias_sin_venta,
                'fecha_foto': fecha_foto,
                'fch_ult_venta': fch_ult_venta,
                'fch_ult_compra': fch_ult_compra,
                'dias_sugeridos': int(self.dias_sugeridos),
                'stock_minimo': int(self.stock_minimo),
                'sugerido_cajas': sugerido_cajas,
                'sugerido_unidades': round(sugerido_unidades, 2),
                'valor_sugerido': valor_compra_sugerida,
                'abc': clasificacion_abc,
                'clasificacion_abc': clasificacion_abc,
                'participacion_linea': round(participacion_linea, 2),
                # Claves en mayúsculas para compatibilidad directa con plantillas/componentes
                'ID_ARTICULO': id_articulo,
                'REFERENCIA': referencia,
                'NOM_ARTICULO': nom_articulo,
                'ESTADO': estado,
                'GRUPO': grupo,
                'LINEA': linea,
                'EXISTENCIA': int(existencia) if existencia == int(existencia) else float(round(existencia, 2)),
                'VLR_PROMEDIO': round(vlr_promedio, 2),
                'COSTO_UNITARIO': round(vlr_promedio, 2),
                'VALOR_TOTAL': round(valor_total, 2),
                'ROTACION_DIARIA': round(rotacion_diaria, 4),
                'PEDIDOS_POR_LLEGAR': round(pedidos_por_llegar, 2),
                'EMBALAJE': embalaje_out,
                'FCH_ULT_COMPRA': fch_ult_compra,
                'VTA_30': round(vta_30, 2),
                'VTA_60': round(vta_60, 2),
                'VTA_90': round(vta_90, 2),
                'DIAS_SIN_VENTA': dias_sin_venta,
                'SUGERIDO_CAJAS': sugerido_cajas,
                'VALOR_SUGERIDO': valor_compra_sugerida,
                'ABC': clasificacion_abc,
                'CLASIFICACION_ABC': clasificacion_abc,
            })

        # Ordenamiento inteligente: 1) grupo ASC, 2) abc ASC ('A', 'B', 'C'), 3) sugerido_cajas DESC
        resultado.sort(
            key=lambda item: (
                str(item.get('grupo') or '').strip().upper(),
                str(item.get('abc') or item.get('clasificacion_abc') or 'Z').strip().upper(),
                -(int(item.get('sugerido_cajas') or 0)),
            )
        )

        return resultado

