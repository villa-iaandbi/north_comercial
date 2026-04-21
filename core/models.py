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
    id_cliente_contado = models.CharField(max_length=15, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'ct_ventas'

    def __str__(self):
        return f"Venta {self.id_documento_id}"


class InMovInventario(models.Model):
    # Campo ficticio para cumplir con el ORM de Django (PK es requerida)
    id_registro = models.CharField(max_length=15, primary_key=True)
    
    id_documento = models.ForeignKey(
        CoDocumento, 
        on_delete=models.DO_NOTHING, 
        db_column='id_documento', 
        db_constraint=False
    )
    id_articulo = models.CharField(max_length=15)
    entra_sale = models.CharField(max_length=1)
    cantidad = models.DecimalField(max_digits=15, decimal_places=2)
    vlr_unitario = models.DecimalField(max_digits=15, decimal_places=2)
    porc_descuento_1 = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    vlr_iva = models.DecimalField(max_digits=15, decimal_places=2)
    impoconsumo = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    vlr_promedio_ini = models.DecimalField(max_digits=15, decimal_places=2)
    existencia = models.DecimalField(max_digits=15, decimal_places=2)
    obser = models.CharField(max_length=2000, null=True, blank=True)
    id_centro_costo = models.CharField(max_length=5)

    class Meta:
        managed = False
        db_table = 'in_mov_inventarios'

    def __str__(self):
        return f"{self.id_articulo} ({self.entra_sale}) - {self.cantidad}"


class CoDocumentoItem(models.Model):
    # Campo ficticio para cumplir con el ORM de Django (PK es requerida)
    id_registro = models.CharField(max_length=15, primary_key=True)
    
    id_documento = models.ForeignKey(
        CoDocumento, 
        on_delete=models.DO_NOTHING, 
        db_column='id_documento', 
        db_constraint=False
    )
    id_cuenta = models.CharField(max_length=20)
    debe_haber = models.CharField(max_length=1)
    campo = models.CharField(max_length=20, null=True, blank=True)
    valor = models.DecimalField(max_digits=15, decimal_places=2)
    id_centro_costo = models.CharField(max_length=5)

    class Meta:
        managed = False
        db_table = 'co_documento_items'

    def __str__(self):
        return f"Item {self.id_cuenta}: {self.valor}"

class CoTercero(models.Model):
    id_tercero = models.CharField(max_length=15, primary_key=True, db_column='ID_TERCERO')
    nom_tercero = models.CharField(max_length=200, db_column='NOM_TERCERO')

    class Meta:
        managed = False
        db_table = 'co_terceros'

class MvPedidosNorth(models.Model):
    # Usamos NUM_PEDIDO como Primary Key para Django
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
    estado_pedido = models.CharField(max_length=3, db_column='ESTADO_PEDIDO') # NOT NULL
    ruta = models.CharField(max_length=10, null=True, blank=True, db_column='RUTA')
    procesado = models.CharField(max_length=1, null=True, blank=True, db_column='PROCESADO')

    class Meta:
        managed = False
        db_table = 'mv_pedidos_north'
        verbose_name = "Vista Pedidos North"