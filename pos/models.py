from django.db import models
from decimal import Decimal

class PosTurno(models.Model):
    id_turno = models.BigAutoField(primary_key=True)
    caja_id = models.CharField(max_length=20, default='CAJA-01')
    usuario = models.CharField(max_length=50, default='cajero')
    base_economica = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    fch_apertura = models.DateTimeField(auto_now_add=True)
    fch_cierre = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=15, default='ABIERTO') # 'ABIERTO', 'CERRADO'
    tot_ventas_efectivo = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    tot_ventas_tarjeta = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    tot_ventas_transferencia = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    id_documento_cierre = models.CharField(max_length=20, null=True, blank=True)

    def __str__(self):
        return f"Turno #{self.id_turno} ({self.caja_id}) - {self.estado}"


class PosTicketHeader(models.Model):
    ticket_id = models.CharField(max_length=60, primary_key=True)
    turno = models.ForeignKey(PosTurno, on_delete=models.CASCADE, related_name='tickets')
    id_tercero = models.CharField(max_length=20, default='222222222222')
    nom_tercero = models.CharField(max_length=200, default='VENTAS MASIVAS (CONSUMIDOR FINAL)')
    fch_ticket = models.DateTimeField()
    tot_mercancia = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    tot_iva = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    tot_ticket = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    descuento_promocion = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    pago_efectivo = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    pago_tarjeta = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    pago_transferencia = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    pago_puntos = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    puntos_ganados = models.IntegerField(default=0)
    puntos_redimidos_ticket = models.IntegerField(default=0)
    cambio = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    sync_status = models.BooleanField(default=True)
    consolidado_cierre = models.BooleanField(default=False)

    def __str__(self):
        return f"Ticket {self.ticket_id} - ${self.tot_ticket}"


class PosTicketDetail(models.Model):
    ticket = models.ForeignKey(PosTicketHeader, on_delete=models.CASCADE, related_name='items')
    id_articulo = models.CharField(max_length=15)
    referencia = models.CharField(max_length=30, blank=True, default='')
    nom_articulo = models.CharField(max_length=150)
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)
    vlr_unitario = models.DecimalField(max_digits=15, decimal_places=2)
    porc_descuento = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    porc_iva = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    vlr_iva = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    tot_linea = models.DecimalField(max_digits=15, decimal_places=2)

    def __str__(self):
        return f"{self.id_articulo} x {self.cantidad}"


# ==========================================
# PROMOCIONES Y FIDELIZACIÓN (CAPÍTULO 7)
# ==========================================

class PrPromocion(models.Model):
    id_promocion = models.BigAutoField(primary_key=True)
    nom_promocion = models.CharField(max_length=150)
    fch_inicio = models.DateTimeField()
    fch_fin = models.DateTimeField()
    activo = models.BooleanField(default=True)
    prioridad = models.IntegerField(default=1)

    def __str__(self):
        return f"Promoción: {self.nom_promocion}"


class PrCondicion(models.Model):
    TIPO_CONDICION_CHOICES = (
        ('MONTO_MINIMO', 'Monto Mínimo de Compra'),
        ('PRODUCTO', 'Producto Específico'),
        ('CLIENTE', 'Cliente Específico'),
    )
    id_condicion = models.BigAutoField(primary_key=True)
    promocion = models.ForeignKey(PrPromocion, on_delete=models.CASCADE, related_name='condiciones')
    tipo_condicion = models.CharField(max_length=30, choices=TIPO_CONDICION_CHOICES)
    valor_condicion = models.CharField(max_length=100) # ej: '50000', 'ART-101', '222222222222'
    cantidad_minima = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1'))

    def __str__(self):
        return f"Condición {self.tipo_condicion}: {self.valor_condicion}"


class PrAccion(models.Model):
    TIPO_ACCION_CHOICES = (
        ('DESCUENTO_PORC', 'Porcentaje de Descuento (%)'),
        ('DESCUENTO_VALOR', 'Valor Fijo de Descuento ($ COP)'),
        ('REGALO', 'Artículo de Regalo ($0 COP)'),
    )
    id_accion = models.BigAutoField(primary_key=True)
    promocion = models.ForeignKey(PrPromocion, on_delete=models.CASCADE, related_name='acciones')
    tipo_accion = models.CharField(max_length=30, choices=TIPO_ACCION_CHOICES)
    valor_accion = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0'))
    id_articulo_regalo = models.CharField(max_length=20, null=True, blank=True)

    def __str__(self):
        return f"Acción {self.tipo_accion}: {self.valor_accion}"


class PosPuntosCliente(models.Model):
    id_tercero = models.CharField(max_length=20, primary_key=True)
    puntos_saldo = models.IntegerField(default=0)
    puntos_acumulados = models.IntegerField(default=0)
    puntos_redimidos = models.IntegerField(default=0)
    fch_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Puntos Cliente {self.id_tercero}: {self.puntos_saldo} pts"


class TsCaja(models.Model):
    ESTADOS = (('A', 'Activa'), ('I', 'Inactiva'))

    id_caja = models.CharField(max_length=8, primary_key=True, db_column='ID_CAJA')
    cod_caja = models.CharField(max_length=20, db_column='COD_CAJA')
    descripcion = models.CharField(max_length=100, db_column='DESCRIPCION')
    id_bodega = models.CharField(max_length=10, db_column='ID_BODEGA')
    id_centro_costo = models.CharField(max_length=10, db_column='ID_CENTRO_COSTO')
    id_tipo_documento = models.CharField(max_length=10, db_column='ID_TIPO_DOCUMENTO')
    id_doc_consecutivo = models.CharField(max_length=20, db_column='ID_DOC_CONSECUTIVO')
    estado = models.CharField(max_length=1, default='A', choices=ESTADOS, db_column='ESTADO')

    class Meta:
        managed = False
        db_table = 'TS_CAJAS'
        ordering = ['id_caja']

    def save(self, *args, **kwargs):
        if self._state.adding:
            kwargs.setdefault('force_insert', True)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.cod_caja} - {self.descripcion}"
