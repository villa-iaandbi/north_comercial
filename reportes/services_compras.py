# -*- coding: utf-8 -*-
import datetime
import decimal
import logging
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.db import connection, transaction
from django.utils import timezone
from core.models import (
    CoDocumento,
    CoTipoDocumento,
    InArticulo,
    InBodega,
    InGravamen,
    InMovInventario,
    CoCentroCosto,
    PvOrdCompra,
    PvProveedor,
)
from core.utils_tributarios import CalculadoraTributaria

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
        self.grupo_codigo = str(raw_grupo).strip(
        ) if raw_grupo is not None else ''
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
            logger.error(
                "Error al obtener grupos disponibles en ServicioComprasSugeridas: %s", e)

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
                V.UNIDAD_VENTA,
                V.VLR_PROMEDIO,
                V.VALOR_TOTAL,
                V.ROTACION_DIARIA,
                V.FCH_ULT_VENTA,
                V.DIAS_SIN_VENTA,
                V.ID_GRAVAMEN,
                V.VLR_REPOSICION,
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
                columns = [col[0].upper()
                           for col in cursor.description] if cursor.description else []
                for row in cursor.fetchall():
                    registros.append(dict(zip(columns, row)))
        except Exception as e:
            logger.error(
                "Error al consultar datos de compras sugeridas en V_ANALISIS_INVENTARIO: %s", e)
            raise e

        # Paso 1: Acumular el valor total de inventario por LINEA para clasificación ABC
        totales_por_linea = {}
        for reg in registros:
            linea_key = str(reg.get('LINEA') or 'SIN LINEA').strip().upper()
            val_inv = self._to_decimal(reg.get('VALOR_TOTAL'), Decimal('0.0'))
            if val_inv > Decimal('0.0'):
                totales_por_linea[linea_key] = totales_por_linea.get(
                    linea_key, Decimal('0.0')) + val_inv

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
            id_gravamen = str(reg.get('ID_GRAVAMEN') or '1').strip()
            linea_key = linea.upper()

            existencia = self._to_decimal(
                reg.get('EXISTENCIA'), Decimal('0.0'))
            vlr_promedio = self._to_decimal(
                reg.get('VLR_PROMEDIO'), Decimal('0.0'))
            vlr_reposicion = self._to_decimal(
                reg.get('VLR_REPOSICION'), Decimal('0.0'))
            valor_total = self._to_decimal(
                reg.get('VALOR_TOTAL'), Decimal('0.0'))
            rotacion_diaria = self._to_decimal(
                reg.get('ROTACION_DIARIA'), Decimal('0.0'))
            pedidos_por_llegar = self._to_decimal(
                reg.get('PEDIDOS_POR_LLEGAR'), Decimal('0.0'))

            vta_30_unidades = self._to_decimal(
                reg.get('VTA_30'), Decimal('0.0'))
            vta_60_unidades = self._to_decimal(
                reg.get('VTA_60'), Decimal('0.0'))
            vta_90_unidades = self._to_decimal(
                reg.get('VTA_90'), Decimal('0.0'))

            embalaje = self._to_decimal(reg.get('EMBALAJE'), Decimal('1.0'))
            if embalaje <= Decimal('0.0'):
                embalaje = Decimal('1.0')

            vta_30_cajas = round(vta_30_unidades / embalaje, 1)
            vta_60_cajas = round(vta_60_unidades / embalaje, 1)
            vta_90_cajas = round(vta_90_unidades / embalaje, 1)

            dias_sin_venta = int(self._to_decimal(
                reg.get('DIAS_SIN_VENTA'), Decimal('0')))
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
            costo_referencia = vlr_reposicion if vlr_reposicion > Decimal(
                '0.0') else vlr_promedio
            valor_compra_sugerida = round(
                sugerido_unidades * costo_referencia, 2)

            # Clasificación ABC simplificada por Línea (> 10% del valor total de su línea => 'A')
            total_linea = totales_por_linea.get(linea_key, Decimal('0.0'))
            if total_linea > Decimal('0.0') and valor_total > Decimal('0.0'):
                participacion_linea = (
                    valor_total / total_linea) * Decimal('100.0')
            else:
                participacion_linea = Decimal('0.0')

            if participacion_linea > Decimal('10.0'):
                clasificacion_abc = 'A'
            elif participacion_linea > Decimal('3.0'):
                clasificacion_abc = 'B'
            else:
                clasificacion_abc = 'C'

            embalaje_out = int(embalaje) if embalaje == int(
                embalaje) else float(round(embalaje, 2))

            unidad_venta = str(reg.get('UNIDAD_VENTA') or 'UN').strip()[:2]

            resultado.append({
                'id_articulo': id_articulo,
                'referencia': referencia,
                'nom_articulo': nom_articulo,
                'estado': estado,
                'grupo': grupo,
                'grupo_codigo': cod_grupo or grupo,
                'linea': linea,
                'unidad_medida': unidad_venta,
                'UNIDAD_VENTA': unidad_venta,
                'id_gravamen': id_gravamen,
                'existencia': int(existencia) if existencia == int(existencia) else float(round(existencia, 2)),
                'vlr_promedio': round(vlr_promedio, 2),
                'vlr_reposicion': round(vlr_reposicion, 2),
                'costo_unitario': round(costo_referencia, 2),
                'valor_total': round(valor_total, 2),
                'rotacion_diaria': round(rotacion_diaria, 4),
                'pedidos_por_llegar': round(pedidos_por_llegar, 2),
                'embalaje': embalaje_out,
                'vta_30': vta_30_cajas,
                'vta_60': vta_60_cajas,
                'vta_90': vta_90_cajas,
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
                'ID_GRAVAMEN': id_gravamen,
                'EXISTENCIA': int(existencia) if existencia == int(existencia) else float(round(existencia, 2)),
                'VLR_PROMEDIO': round(vlr_promedio, 2),
                'VLR_REPOSICION': round(vlr_reposicion, 2),
                'COSTO_UNITARIO': round(costo_referencia, 2),
                'VALOR_TOTAL': round(valor_total, 2),
                'ROTACION_DIARIA': round(rotacion_diaria, 4),
                'PEDIDOS_POR_LLEGAR': round(pedidos_por_llegar, 2),
                'EMBALAJE': embalaje_out,
                'FCH_ULT_COMPRA': fch_ult_compra,
                'VTA_30': vta_30_cajas,
                'VTA_60': vta_60_cajas,
                'VTA_90': vta_90_cajas,
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
                str(item.get('abc') or item.get(
                    'clasificacion_abc') or 'Z').strip().upper(),
                -(int(item.get('sugerido_cajas') or 0)),
            )
        )

        return resultado


def _parse_decimal(valor, default: Decimal = Decimal('0.00')) -> Decimal:
    if valor is None or valor == '':
        return default
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError, TypeError):
        return default


def obtener_consecutivo(tipo_documento, doc_consecutivo=None):
    raw_cursor = connection.connection.cursor()
    try:
        out_val = raw_cursor.var(str, 50)
        raw_cursor.callproc('CONSECUTIVO_TIPO_DOC', [
                            tipo_documento, doc_consecutivo, out_val])
        numero_generado = out_val.getvalue()

        raw_cursor.execute(
            "SELECT ID_DOC_CONSECUTIVO FROM CO_TIPO_DOC_CONSECUTIVOS WHERE ID_TIPO_DOCUMENTO = :1 AND ROWNUM = 1", [tipo_documento])
        row = raw_cursor.fetchone()
        id_consecutivo = row[0] if row else None

        return numero_generado, id_consecutivo
    finally:
        raw_cursor.close()


def generar_orden_compra(payload: dict, id_usuario: str = "1") -> dict:
    """
    Genera una Orden de Compra transaccional en Oracle 11g (SPEC-007):
      - Cabecera general en CO_DOCUMENTOS (estado_doc='GRABADO', id_vendedor=None,
        id_responsable=id_usuario, terminal='COMPRAS INTELIGENTES').
      - Bypass del Mutating Trigger (INS_DOCUMENTO_CONSEC) actualizando id_doc_consecutivo con .filter().update().
      - Cabecera de orden de compra en PV_ORD_COMPRAS.
      - Detalle físico en IN_MOV_INVENTARIOS vía bulk_create con entra_sale='N' y SIN asientos contables.
    """
    try:
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un diccionario válido.")

        id_proveedor = str(payload.get('id_proveedor') or '').strip()
        id_bodega = str(payload.get('id_bodega') or '1').strip()
        plazo_pago = int(payload.get('plazo_pago') or 0)
        articulos = payload.get('articulos') or []
        siono_iva_proveedor_global = str(payload.get(
            'siono_iva_proveedor') or 'N').strip().upper()

        if not id_proveedor:
            raise ValueError("El campo 'id_proveedor' es obligatorio.")
        if not articulos or not isinstance(articulos, list):
            raise ValueError("La lista de 'articulos' no puede estar vacía.")

        # Extraer de 'id_centro_costo', o de 'centro_costo', o usar '1' por defecto
        raw_cc = payload.get('id_centro_costo') or payload.get(
            'centro_costo') or '1'
        cc_input = str(raw_cc).strip()

        cc_list = list(CoCentroCosto.objects.filter(id_centro_costo=cc_input))
        if not cc_list:
            cc_list = list(CoCentroCosto.objects.filter(
                cod_centro_costo=cc_input))

        if not cc_list:
            raise RuntimeError(f"Centro de costo no válido: '{cc_input}'")

        real_id_centro_costo = cc_list[0].id_centro_costo

        calculadora = CalculadoraTributaria(cargar_retenciones=False)

        tot_mercancia = Decimal('0.00')
        tot_iva = Decimal('0.00')
        items_procesados = []

        for idx, item in enumerate(articulos, start=1):
            print(f"==== DEBUG ITEM DESDE ALPINE ==== \n{item}\n=================================")
            # Normalizar llaves a minúsculas para extracción a prueba de fallos
            item_lower = {str(k).lower(): v for k, v in item.items()}

            id_articulo = str(item_lower.get('id_articulo') or '').strip()
            cantidad_unidades = _parse_decimal(item_lower.get('cantidad_unidades') or item_lower.get('sugerido_unidades'))
            vlr_unitario = _parse_decimal(item_lower.get('vlr_unitario') or item_lower.get('costo_unitario'))
            id_gravamen = str(item_lower.get('id_gravamen') or '1').strip()
            siono_iva_proveedor = str(item_lower.get('siono_iva_proveedor', siono_iva_proveedor_global) or 'N').strip().upper()

            # Campos corregidos:
            nom_articulo_real = str(item_lower.get('nom_articulo') or item_lower.get('nombre') or '')[:250]
            unidad_medida_real = str(item_lower.get('unidad_medida') or item_lower.get('unidad_venta') or 'UN')[:2]
            existencia_real = _parse_decimal(item_lower.get('existencia'))

            if not id_articulo or cantidad_unidades <= Decimal('0'):
                continue

            subtotal_linea, vlr_iva_linea, _ = calculadora.calcular_impuestos_linea(
                cantidad=cantidad_unidades,
                vlr_unitario=vlr_unitario,
                id_gravamen=id_gravamen,
                siono_iva_proveedor=siono_iva_proveedor
            )

            tot_mercancia += subtotal_linea
            tot_iva += vlr_iva_linea

            items_procesados.append({
                'idx': idx,
                'id_articulo': id_articulo,
                'cantidad_unidades': cantidad_unidades,
                'vlr_unitario': vlr_unitario,
                'vlr_iva': vlr_iva_linea,
                'id_gravamen': id_gravamen,
                'nom_articulo': nom_articulo_real,
                'id_unidad_medida': unidad_medida_real,
                'existencia': existencia_real,
            })

        if not items_procesados:
            raise ValueError(
                "No hay artículos válidos con cantidad mayor a cero para procesar.")

        tot_servicios = Decimal('0.00')
        tot_descuento = Decimal('0.00')

        with transaction.atomic():
            fch_actual = timezone.now()
            current_year = str(fch_actual.year)

            # Obtener consecutivo nativo de Oracle
            ano_actual = str(timezone.now().year)
            docs = list(CoTipoDocumento.objects.filter(
                cod_tipo_documento='135', id_ano=ano_actual))
            tipo_doc = docs[0] if docs else None

            if not tipo_doc:
                raise RuntimeError(
                    f"El tipo de documento 135 no está configurado para el año {ano_actual} en co_tipo_documentos.")
            id_tipo_doc_real = tipo_doc.id_tipo_documento
            num_doc_generado, id_consec = obtener_consecutivo(id_tipo_doc_real)

            if not num_doc_generado:
                raise RuntimeError(
                    "Fallo al generar consecutivo en CONSECUTIVO_TIPO_DOC para el tipo de documento '135'."
                )

            # A. Obtener ID de Documento desde la secuencia principal
            with connection.cursor() as cursor:
                cursor.execute("SELECT SEC_DOCUMENTO.NEXTVAL FROM DUAL")
                row_sec = cursor.fetchone()
                id_documento = str(row_sec[0]).strip()

            num_documento_str = str(num_doc_generado).strip()
            id_doc_consecutivo_str = str(
                id_consec).strip() if id_consec is not None else None

            gran_total_red = (
                tot_mercancia + tot_iva).quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP)

            # C. Crear cabecera en CoDocumento
            doc = CoDocumento.objects.create(
                id_documento=id_documento,
                id_tipo_documento=id_tipo_doc_real,
                num_documento=num_documento_str,
                fch_documento=fch_actual,
                id_tercero_id=id_proveedor,
                id_vendedor=None,
                id_responsable=str(id_usuario)[:15],
                tot_documento=gran_total_red,
                obser=payload.get(
                    'obser') or f"Orden de Compra Generada Nro. {num_documento_str}",
                estado_doc="GRABADO",
                fch_registro=fch_actual,
                terminal="COMPRAS INTELIGENTES",
                id_sistema="1",
                id_ano=current_year,
                id_moneda="1",
                vlr_moneda=Decimal('1.00'),
                vlr_comision_vend=Decimal('0.00'),
                cambios=1,
            )

            # D. Bypass del Mutating Trigger (ORA-04091 INS_DOCUMENTO_CONSEC) actualizando id_doc_consecutivo
            CoDocumento.objects.filter(pk=id_documento).update(
                id_doc_consecutivo=id_doc_consecutivo_str, terminal="COMPRAS INTELIGENTES"
            )

            # E. Crear registro en PvOrdCompra usando el plazo_pago del payload y totales calculados
            # Extraer porcentaje de descuento
            porc_desc_fin = _parse_decimal(payload.get('porc_descuento_financiero') or payload.get('desc_financiero') or 0)

            # Extraer fecha límite de descuento (y fecha entrega). Usamos fallback a hoy si viene vacía.
            raw_fch_entrega = payload.get('fch_entrega') or payload.get('fecha_entrega')
            raw_fch_limite = payload.get('fch_limite_descuento') or raw_fch_entrega

            try:
                if raw_fch_limite:
                    fch_limite_val = datetime.datetime.strptime(str(raw_fch_limite)[:10], '%Y-%m-%d').date()
                else:
                    fch_limite_val = fch_actual.date()
            except ValueError:
                fch_limite_val = fch_actual.date()

            try:
                if raw_fch_entrega:
                    fch_entrega_val = datetime.datetime.strptime(str(raw_fch_entrega)[:10], '%Y-%m-%d').date()
                else:
                    fch_entrega_val = fch_actual.date()
            except ValueError:
                fch_entrega_val = fch_actual.date()

            PvOrdCompra.objects.create(
                id_documento=doc,
                fch_entrega_mcia=fch_entrega_val,
                porc_descuento_financiero=porc_desc_fin,
                fch_limite_descuento=fch_limite_val,
                condiciones_pago=str(payload.get(
                    'condiciones_pago') or f"{plazo_pago} DIAS")[:30],
                plazo_pago=plazo_pago,
                tot_mercancia=tot_mercancia,
                tot_servicios=tot_servicios,
                tot_iva=tot_iva,
                tot_descuento=tot_descuento,
                vlr_ord_compra=gran_total_red,
                vlr_saldo=gran_total_red,
            )

            # E. Preparar lista de InMovInventario e insertar con bulk_create (entra_sale="N", SIN asientos contables)
            movimientos = []
            for item_data in items_procesados:
                movimientos.append(
                    InMovInventario(
                        id_item=str(item_data['idx']),
                        id_documento=doc,
                        id_articulo=item_data['id_articulo'],
                        fch_documento=fch_actual,
                        entra_sale="N",
                        cantidad=item_data['cantidad_unidades'],
                        vlr_unitario=item_data['vlr_unitario'],
                        porc_descuento_1=Decimal('0.00'),
                        vlr_iva=item_data['vlr_iva'],
                        impoconsumo=Decimal('0.00'),
                        vlr_promedio_ini=item_data['vlr_unitario'],
                        vlr_comision_vend=Decimal('0.00'),
                        existencia=item_data['existencia'],
                        saldo=item_data['cantidad_unidades'],
                        id_centro_costo=real_id_centro_costo,
                        id_bodega=id_bodega,
                        id_sistema="1",
                        id_gravamen=item_data['id_gravamen'],
                        id_unidad_medida=item_data['id_unidad_medida'],
                        vlr_reposicion=item_data['vlr_unitario'],
                        obser=item_data['nom_articulo'],
                    )
                )

            InMovInventario.objects.bulk_create(movimientos)

        return {
            'success': True,
            'num_documento': num_documento_str,
            'id_documento': id_documento,
        }

    except Exception as e:
        logger.exception("Error al generar orden de compra: %s", e)
        return {
            'success': False,
            'error': str(e),
        }


def anular_orden_compra(id_documento: str, motivo: str, id_usuario: str) -> dict:
    """
    Anula una Orden de Compra de forma transaccional (SPEC-007).
      - Cambia estado_doc a 'ANULADO' en CO_DOCUMENTOS.
      - Pone en cero los totales en CO_DOCUMENTOS y PV_ORD_COMPRAS.
      - Concatena el motivo de anulación en la observación.
      - Borra físicamente el detalle en IN_MOV_INVENTARIOS.
    """
    with transaction.atomic():
        # Evitar .get() o .first() para prevenir ORA-00933 en Oracle 11g
        docs = list(CoDocumento.objects.filter(pk=id_documento))
        if not docs:
            return {'success': False, 'error': f"No se encontró el documento con ID {id_documento}"}
        
        doc = docs[0]
        
        if doc.estado_doc == 'ANULADO':
            return {'success': False, 'error': 'El documento ya se encuentra anulado.'}

        nueva_obser = f"{doc.obser} | ANULADO: {motivo}"[:250]

        # 1. Actualizar CoDocumento usando filter().update() para evitar triggers
        CoDocumento.objects.filter(pk=id_documento).update(
            estado_doc='ANULADO',
            tot_documento=Decimal('0.00'),
            obser=nueva_obser
        )

        # 2. Actualizar PvOrdCompra usando filter().update()
        PvOrdCompra.objects.filter(id_documento=id_documento).update(
            tot_mercancia=Decimal('0.00'),
            tot_servicios=Decimal('0.00'),
            tot_iva=Decimal('0.00'),
            tot_descuento=Decimal('0.00'),
            vlr_ord_compra=Decimal('0.00'),
            vlr_saldo=Decimal('0.00')
        )

        # 3. Borrar físicamente el detalle del inventario
        InMovInventario.objects.filter(id_documento=id_documento).delete()

        return {'success': True, 'num_documento': doc.num_documento}
