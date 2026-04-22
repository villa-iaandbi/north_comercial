from django.db import models

class CoDocumento(models.Model):
    id_documento = models.CharField(max_length=15, primary_key=True)
    id_tipo_documento = models.CharField(max_length=5)
    num_documento = models.CharField(max_length=20)
    fch_documento = models.DateTimeField()
    id_tercero = models.CharField(max_length=15)
    id_vendedor = models.CharField(max_length=15)
    id_responsable = models.CharField(max_length=15)
    tot_documento = models.DecimalField(max_digits=15, decimal_places=2)
    obser = models.CharField(max_length=2000, null=True, blank=True)
    docto_alterno = models.CharField(max_length=20, null=True, blank=True)
    estado_doc = models.CharField(max_length=15)
    fch_registro = models.DateTimeField()
    terminal = models.CharField(max_length=50)
    id_sistema = models.CharField(max_length=2)
    id_ano = models.CharField(max_length=4, null=True, blank=True)
    id_moneda = models.CharField(max_length=5, null=True, blank=True)
    vlr_moneda = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    vlr_comision_vend = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='VLR_COMISION_VEND')
    cambios = models.DecimalField(max_digits=4, decimal_places=0, null=True, blank=True)
    id_doc_consecutivo = models.CharField(max_length=15, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'co_documentos'

    def __str__(self):
        return f"{self.id_tipo_documento} - {self.num_documento}"


class CtVenta(models.Model):
    id_documento = models.OneToOneField(
        CoDocumento, 
        on_delete=models.DO_NOTHING, 
        primary_key=True, 
        db_column='id_documento', 
        db_constraint=False
    )
    tot_mercancia = models.DecimalField(max_digits=15, decimal_places=2)
    tot_iva = models.DecimalField(max_digits=15, decimal_places=2)
    tot_retefuente = models.DecimalField(max_digits=15, decimal_places=2)
    vlr_retencion_iva = models.DecimalField(max_digits=15, decimal_places=2)
    tot_descuento = models.DecimalField(max_digits=15, decimal_places=2)
    vlr_venta = models.DecimalField(max_digits=15, decimal_places=2)
    plazo_pago = models.IntegerField(default=0)
    id_precio = models.CharField(max_length=2, null=True, blank=True, db_column='ID_PRECIO')
    id_cliente_contado = models.CharField(max_length=15, null=True, blank=True)
    fch_entrega_mcia = models.DateTimeField(db_column='FCH_ENTREGA_MCIA', null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'ct_ventas'

    def __str__(self):
        return f"Venta {self.id_documento_id}"


class InMovInventario(models.Model):
    id_item = models.CharField(max_length=8, primary_key=True, db_column='ID_ITEM')
    
    id_documento = models.ForeignKey(
        CoDocumento, 
        on_delete=models.DO_NOTHING, 
        db_column='id_documento', 
        db_constraint=False
    )
    id_articulo = models.CharField(max_length=15)
    fch_documento = models.DateTimeField(db_column='FCH_DOCUMENTO', null=True, blank=True)
    entra_sale = models.CharField(max_length=1)
    cantidad = models.DecimalField(max_digits=15, decimal_places=2)
    vlr_unitario = models.DecimalField(max_digits=15, decimal_places=2)
    porc_descuento_1 = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    vlr_iva = models.DecimalField(max_digits=15, decimal_places=2)
    impoconsumo = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    vlr_promedio_ini = models.DecimalField(max_digits=15, decimal_places=2)
    vlr_reposicion = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='VLR_REPOSICION')
    vlr_comision_vend = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='VLR_COMISION_VEND')
    existencia = models.DecimalField(max_digits=15, decimal_places=2)
    saldo = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='SALDO')
    obser = models.CharField(max_length=2000, null=True, blank=True)
    id_centro_costo = models.CharField(max_length=5)
    id_bodega = models.CharField(max_length=5, db_column='ID_BODEGA', null=True, blank=True)
    id_unidad_medida = models.CharField(max_length=5, db_column='ID_UNIDAD_MEDIDA', null=True, blank=True)
    id_sistema = models.CharField(max_length=1, db_column='ID_SISTEMA', null=True, blank=True)
    id_gravamen = models.CharField(max_length=8, db_column='ID_GRAVAMEN', null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'in_mov_inventarios'

    def __str__(self):
        return f"{self.id_articulo} ({self.entra_sale}) - {self.cantidad}"


class CoDocumentoItem(models.Model):
    id_documento = models.ForeignKey(
        CoDocumento, 
        on_delete=models.DO_NOTHING, 
        db_column='id_documento', 
        db_constraint=False
    )
    id_item = models.CharField(max_length=8, primary_key=True, db_column='ID_ITEM')
    id_tercero = models.CharField(max_length=15, db_column='ID_TERCERO', null=True, blank=True)
    fch_documento = models.DateTimeField(db_column='FCH_DOCUMENTO', null=True, blank=True)
    id_centro_costo = models.CharField(max_length=8, db_column='ID_CENTRO_COSTO')
    debe_haber = models.CharField(max_length=1, db_column='DEBE_HABER')
    id_cuenta = models.CharField(max_length=20, db_column='ID_CUENTA')
    siono_pendiente = models.CharField(max_length=1, db_column='SIONO_PENDIENTE', null=True, blank=True)
    campo = models.CharField(max_length=20, null=True, blank=True, db_column='CAMPO')
    valor = models.DecimalField(max_digits=15, decimal_places=2, db_column='VALOR')

    class Meta:
        managed = False
        db_table = 'co_documento_items'

    def __str__(self):
        return f"Item {self.id_cuenta}: {self.valor}"


# ==========================================
# MAESTROS BÁSICOS Y CONFIGURACIÓN
# ==========================================

class SgParametro(models.Model):
    id_parametro = models.CharField(max_length=35, primary_key=True, db_column='ID_PARAMETRO')
    nom_parametro = models.CharField(max_length=50, db_column='NOM_PARAMETRO')
    modulo = models.CharField(max_length=50, db_column='MODULO')
    fecha = models.DateTimeField(null=True, blank=True, db_column='FECHA')
    vlr_num = models.DecimalField(max_digits=32, decimal_places=7, null=True, blank=True, db_column='VLR_NUM')
    vlr_chr = models.CharField(max_length=1000, null=True, blank=True, db_column='VLR_CHR')
    vlr_fch = models.DateTimeField(null=True, blank=True, db_column='VLR_FCH')
    id_sistema = models.CharField(max_length=8, null=True, blank=True, db_column='ID_SISTEMA')
    obser = models.CharField(max_length=250, null=True, blank=True, db_column='OBSER')

    class Meta:
        managed = False
        db_table = 'sg_parametros'

class SgUsuarioCentroCosto(models.Model):
    id_usuario = models.CharField(max_length=8, primary_key=True, db_column='ID_USUARIO')
    id_centro_costo = models.CharField(max_length=8, db_column='ID_CENTRO_COSTO')

    class Meta:
        managed = False
        db_table = 'sg_usuario_centro_costos'

class CoTercero(models.Model):
    id_tercero = models.CharField(max_length=8, primary_key=True, db_column='ID_TERCERO')
    nom_tercero = models.CharField(max_length=200, db_column='NOM_TERCERO') # Asumido 200 por estándar, ajustar si es diferente
    id_regimen = models.CharField(max_length=3, null=True, blank=True, db_column='ID_REGIMEN')

    class Meta:
        managed = False
        db_table = 'co_terceros'

class InComisionGrupo(models.Model):
    id_comision = models.CharField(max_length=8, db_column='ID_COMISION', primary_key=True) # Django requiere un PK a nivel de modelo
    id_grupo = models.CharField(max_length=8, db_column='ID_GRUPO')
    id_sistema = models.CharField(max_length=8, db_column='ID_SISTEMA')
    porc_comision = models.DecimalField(max_digits=5, decimal_places=2, db_column='PORC_COMISION')

    class Meta:
        managed = False
        db_table = 'in_comision_grupos'
        unique_together = (('id_comision', 'id_grupo', 'id_sistema'),)

class CtVendedor(models.Model):
    id_vendedor = models.CharField(max_length=8, primary_key=True, db_column='ID_VENDEDOR')
    id_comision = models.CharField(max_length=8, null=True, blank=True, db_column='ID_COMISION')

    class Meta:
        managed = False
        db_table = 'ct_vendedores'
        verbose_name = "Maestro de Vendedores"

# ==========================================
# MAESTROS CONTABLES (PLANTILLAS)
# ==========================================
class CoPlantilla(models.Model):
    id_plantilla = models.CharField(max_length=8, primary_key=True, db_column='ID_PLANTILLA')
    obser = models.CharField(max_length=250, null=True, blank=True, db_column='OBSER')
    cod_plantilla = models.CharField(max_length=8, db_column='COD_PLANTILLA')
    id_tipo_documento = models.CharField(max_length=8, null=True, blank=True, db_column='ID_TIPO_DOCUMENTO')
    siono_automatica = models.CharField(max_length=1, db_column='SIONO_AUTOMATICA')
    nom_plantilla = models.CharField(max_length=50, db_column='NOM_PLANTILLA')
    id_doc_consecutivo = models.CharField(max_length=8, null=True, blank=True, db_column='ID_DOC_CONSECUTIVO')
    id_tercero = models.CharField(max_length=8, null=True, blank=True, db_column='ID_TERCERO')

    class Meta:
        managed = False
        db_table = 'co_plantillas'

class CoPlantillaItem(models.Model):
    id_plantilla = models.CharField(max_length=8, db_column='ID_PLANTILLA')
    id_item = models.CharField(max_length=8, primary_key=True, db_column='ID_ITEM')
    debe_haber = models.CharField(max_length=1, null=True, blank=True, db_column='DEBE_HABER')
    id_centro_costo = models.CharField(max_length=8, null=True, blank=True, db_column='ID_CENTRO_COSTO')
    id_tercero = models.CharField(max_length=8, null=True, blank=True, db_column='ID_TERCERO')
    detalle = models.CharField(max_length=150, null=True, blank=True, db_column='DETALLE')
    id_cuenta = models.CharField(max_length=8, null=True, blank=True, db_column='ID_CUENTA')
    campo = models.CharField(max_length=50, null=True, blank=True, db_column='CAMPO')
    valor = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, db_column='VALOR')
    orden = models.CharField(max_length=8, null=True, blank=True, db_column='ORDEN')

    class Meta:
        managed = False
        db_table = 'co_plantilla_items'

# ==========================================
# MAESTROS TRIBUTARIOS E INVENTARIO
# ==========================================

class InGravamen(models.Model):
    id_gravamen = models.CharField(max_length=8, primary_key=True, db_column='ID_GRAVAMEN')
    porc_gravamen = models.DecimalField(max_digits=5, decimal_places=2, db_column='PORC_GRAVAMEN')
    nom_gravamen = models.CharField(max_length=50, db_column='NOM_GRAVAMEN')
    obser = models.CharField(max_length=250, null=True, blank=True, db_column='OBSER')

    class Meta:
        managed = False
        db_table = 'in_gravamenes'

class CoRetencion(models.Model):
    id_retencion = models.CharField(max_length=8, primary_key=True, db_column='ID_RETENCION')
    porc_retencion = models.DecimalField(max_digits=5, decimal_places=2, db_column='PORC_RETENCION')
    vlr_base = models.DecimalField(max_digits=16, decimal_places=2, db_column='VLR_BASE')
    nom_retencion = models.CharField(max_length=100, db_column='NOM_RETENCION')
    obser = models.CharField(max_length=250, null=True, blank=True, db_column='OBSER')
    id_sistema = models.CharField(max_length=8, db_column='ID_SISTEMA')

    class Meta:
        managed = False
        db_table = 'co_retenciones'

class CoTipoDocumento(models.Model):
    id_sistema = models.CharField(max_length=8, db_column='ID_SISTEMA')
    id_ano = models.CharField(max_length=4, db_column='ID_ANO')
    id_tipo_documento = models.CharField(max_length=8, db_column='ID_TIPO_DOCUMENTO', primary_key=True)
    nom_tipo_documento = models.CharField(max_length=150, db_column='NOM_TIPO_DOCUMENTO')
    cod_tipo_documento = models.CharField(max_length=8, db_column='COD_TIPO_DOCUMENTO')
    
    class Meta:
        managed = False
        db_table = 'co_tipo_documentos'

class InArticulo(models.Model):
    id_articulo = models.CharField(max_length=8, primary_key=True, db_column='ID_ARTICULO')
    nom_articulo = models.CharField(max_length=150, db_column='NOM_ARTICULO')
    id_grupo = models.CharField(max_length=8, db_column='ID_GRUPO')
    id_linea = models.CharField(max_length=8, null=True, blank=True, db_column='ID_LINEA')
    referencia = models.CharField(max_length=15, db_column='REFERENCIA')
    codigo_barras = models.CharField(max_length=20, null=True, blank=True, db_column='CODIGO_BARRAS')
    referencia_alterna = models.CharField(max_length=15, null=True, blank=True, db_column='REFERENCIA_ALTERNA')
    id_gravamen = models.CharField(max_length=8, db_column='ID_GRAVAMEN')
    impoconsumo = models.DecimalField(max_digits=16, decimal_places=2, db_column='IMPOCONSUMO')
    vlr_reposicion = models.DecimalField(max_digits=16, decimal_places=2, db_column='VLR_REPOSICION')
    existencia_fisico = models.DecimalField(max_digits=10, decimal_places=2, db_column='EXISTENCIA_FISICO')
    siono_serie = models.CharField(max_length=1, db_column='SIONO_SERIE')
    siono_transformacion = models.CharField(max_length=1, db_column='SIONO_TRANSFORMACION')
    siono_producto_agricola = models.CharField(max_length=1, db_column='SIONO_PRODUCTO_AGRICOLA')
    fch_creacion = models.DateTimeField(db_column='FCH_CREACION')
    obser = models.CharField(max_length=1000, null=True, blank=True, db_column='OBSER')
    # FOTO omitida intencionalmente (LONG RAW)
    id_sistema = models.CharField(max_length=8, db_column='ID_SISTEMA')
    coldeportes = models.DecimalField(max_digits=16, decimal_places=2, db_column='COLDEPORTES')
    siono_ica = models.CharField(max_length=1, db_column='SIONO_ICA')
    vlr_promedio = models.DecimalField(max_digits=20, decimal_places=10, db_column='VLR_PROMEDIO')
    id_disponible = models.CharField(max_length=8, null=True, blank=True, db_column='ID_DISPONIBLE')
    activo_inactivo = models.CharField(max_length=1, db_column='ACTIVO_INACTIVO')
    nom_corto = models.CharField(max_length=50, null=True, blank=True, db_column='NOM_CORTO')
    siono_existencia_negativa = models.CharField(max_length=1, db_column='SIONO_EXISTENCIA_NEGATIVA')
    porc_rentabilidad = models.DecimalField(max_digits=5, decimal_places=2, db_column='PORC_RENTABILIDAD')
    siono_bloquea_rentabilidad = models.CharField(max_length=1, db_column='SIONO_BLOQUEA_RENTABILIDAD')
    existencia = models.DecimalField(max_digits=10, decimal_places=3, db_column='EXISTENCIA')

    class Meta:
        managed = False
        db_table = 'in_articulos'

class InArticuloUnidadMedida(models.Model):
    id_articulo = models.CharField(max_length=8, db_column='ID_ARTICULO', primary_key=True)
    id_unidad_medida = models.CharField(max_length=8, db_column='ID_UNIDAD_MEDIDA')
    tipo_unidad_medida = models.CharField(max_length=1, db_column='TIPO_UNIDAD_MEDIDA')
    
    class Meta:
        managed = False
        db_table = 'in_articulo_unidad_medidas'
        unique_together = (('id_articulo', 'id_unidad_medida'),)

# ==========================================
# TRANSACCIONAL: PEDIDOS NORTH
# ==========================================

class MvPedidosNorth(models.Model):
    num_pedido = models.CharField(max_length=15, primary_key=True, db_column='NUM_PEDIDO')
    id_vendedor = models.CharField(max_length=8, db_column='ID_VENDEDOR')
    id_sistema = models.CharField(max_length=15, db_column='ID_SISTEMA')
    id_tercero = models.ForeignKey(CoTercero, on_delete=models.DO_NOTHING, db_column='ID_TERCERO', db_constraint=False, related_name='pedidos')
    obser = models.CharField(max_length=160, null=True, blank=True, db_column='OBSER')
    latitud = models.CharField(max_length=20, null=True, blank=True, db_column='LATITUD')
    longitud = models.CharField(max_length=20, null=True, blank=True, db_column='LONGITUD')
    plazo_pago = models.IntegerField(null=True, blank=True, db_column='PLAZO_PAGO')
    valor_pedido = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, db_column='VALOR_PEDIDO')
    fch_pedido = models.DateTimeField(null=True, blank=True, db_column='FCH_PEDIDO')
    forma_pago = models.CharField(max_length=10, null=True, blank=True, db_column='FORMA_PAGO')
    estado_pedido = models.CharField(max_length=3, db_column='ESTADO_PEDIDO')
    ruta = models.CharField(max_length=10, null=True, blank=True, db_column='RUTA')
    procesado = models.CharField(max_length=1, null=True, blank=True, db_column='PROCESADO')

    class Meta:
        managed = False
        db_table = 'mv_pedidos_north'

class MvPedidoItemNorth(models.Model):
    id_vendedor = models.CharField(max_length=8, db_column='ID_VENDEDOR')
    id_sistema = models.CharField(max_length=15, db_column='ID_SISTEMA')
    num_pedido = models.ForeignKey(MvPedidosNorth, on_delete=models.DO_NOTHING, db_column='NUM_PEDIDO', db_constraint=False, related_name='items')
    id_articulo = models.ForeignKey(InArticulo, on_delete=models.DO_NOTHING, db_column='ID_ARTICULO', db_constraint=False, related_name='detalles_pedido', primary_key=True)
    cantidad = models.DecimalField(max_digits=16, decimal_places=2, db_column='CANTIDAD')
    descuento = models.DecimalField(max_digits=16, decimal_places=2, db_column='DESCUENTO')
    vlr_unitario = models.DecimalField(max_digits=16, decimal_places=2, db_column='VLR_UNITARIO')
    lista = models.CharField(max_length=2, null=True, blank=True, db_column='LISTA')
    cantidad_solicitada = models.DecimalField(max_digits=16, decimal_places=2, db_column='CANTIDAD_SOLICITADA')

    class Meta:
        managed = False
        db_table = 'mv_pedido_items_north'