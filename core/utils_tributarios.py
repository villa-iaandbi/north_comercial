import decimal
from core.models import InGravamen, CoRetencion, SgParametro

class CalculadoraTributaria:
    def __init__(self, cargar_retenciones=False):
        # Caché Nivel 1: IVA (Universal para todas las transacciones)
        self.cache_gravamenes = {str(g.id_gravamen): g.porc_gravamen for g in InGravamen.objects.all()}
        
        # Caché Nivel 2: Parámetros y Retenciones (Solo para Facturas y Notas)
        self.parametros = {}
        self.cache_retenciones = {}
        if cargar_retenciones:
            params = SgParametro.objects.filter(id_parametro__in=[
                'CAUSA_RTEFUENTE', 'RTE_COMPRAS', 'RTE_AGRICOLA', 'RTE_ICA', 'RTE_IVA'
            ])
            self.parametros = {p.id_parametro: p.vlr_chr for p in params}
            self.cache_retenciones = {str(r.id_retencion): r for r in CoRetencion.objects.all()}

    def calcular_impuestos_linea(self, cantidad, vlr_unitario, id_gravamen, siono_iva_proveedor='N', impoconsumo_unitario=None):
        """Calcula Subtotal, IVA e Impoconsumo de una línea sin redondear (precisión máxima)."""
        subtotal = decimal.Decimal(str(cantidad)) * decimal.Decimal(str(vlr_unitario))
        
        if siono_iva_proveedor == 'S':
            porc_iva = decimal.Decimal('0.00')
        else:
            porc_iva = self.cache_gravamenes.get(str(id_gravamen), decimal.Decimal('0.00'))
        
        vlr_iva = subtotal * (porc_iva / decimal.Decimal('100.00'))
        vlr_impoconsumo = decimal.Decimal(str(cantidad)) * (impoconsumo_unitario or decimal.Decimal('0.00'))
        
        return subtotal, vlr_iva, vlr_impoconsumo

    def calcular_retenciones_documento(self, base_agricola, base_compras, base_total, id_regimen_tercero):
        """Calcula la cascada de retenciones a nivel de documento evaluando desbordamientos."""
        if self.parametros.get('CAUSA_RTEFUENTE') != 'SI':
            return {'rte_compras': decimal.Decimal('0.00'), 'rte_agricola': decimal.Decimal('0.00'), 'rte_ica': decimal.Decimal('0.00'), 'rte_iva': decimal.Decimal('0.00')}
            
        tot_rte_agricola = tot_rte_compras = tot_rte_ica = tot_rte_iva = decimal.Decimal('0.00')
        
        id_rte_agricola = self.parametros.get('RTE_AGRICOLA')
        id_rte_compras = self.parametros.get('RTE_COMPRAS')
        id_rte_ica = self.parametros.get('RTE_ICA')
        id_rte_iva = self.parametros.get('RTE_IVA')

        if id_rte_agricola and id_rte_agricola in self.cache_retenciones:
            rte_obj = self.cache_retenciones[id_rte_agricola]
            if base_agricola >= rte_obj.vlr_base:
                tot_rte_agricola = base_agricola * (rte_obj.porc_retencion / decimal.Decimal('100.00'))
            else:
                base_compras += base_agricola  # Desbordamiento de base

        if id_rte_compras and id_rte_compras in self.cache_retenciones:
            rte_obj = self.cache_retenciones[id_rte_compras]
            if base_compras >= rte_obj.vlr_base:
                tot_rte_compras = base_compras * (rte_obj.porc_retencion / decimal.Decimal('100.00'))

        if id_rte_ica and id_rte_ica in self.cache_retenciones:
            rte_obj = self.cache_retenciones[id_rte_ica]
            if base_total >= rte_obj.vlr_base:
                tot_rte_ica = base_total * (rte_obj.porc_retencion / decimal.Decimal('100.00'))

        if str(id_regimen_tercero) == '3' and id_rte_iva and id_rte_iva in self.cache_retenciones:
            rte_obj = self.cache_retenciones[id_rte_iva]
            if base_total >= rte_obj.vlr_base:
                tot_rte_iva = base_total * (rte_obj.porc_retencion / decimal.Decimal('100.00'))

        return {'rte_compras': tot_rte_compras, 'rte_agricola': tot_rte_agricola, 'rte_ica': tot_rte_ica, 'rte_iva': tot_rte_iva}
