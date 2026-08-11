from django.db import models
from django.utils import timezone

class CoEntregas(models.Model):
    id_entrega = models.IntegerField(primary_key=True, db_column='ID_ENTREGA')
    id_sistema = models.CharField(max_length=8, db_column='ID_SISTEMA')
    num_entrega = models.IntegerField(db_column='NUM_ENTREGA')
    fch_salida = models.DateTimeField(default=timezone.now, db_column='FCH_SALIDA')
    fch_cierre = models.DateTimeField(null=True, blank=True, db_column='FCH_CIERRE')
    id_transportador = models.CharField(max_length=8, db_column='ID_TRANSPORTADOR')
    placa_vehiculo = models.CharField(max_length=10, null=True, blank=True, db_column='PLACA_VEHICULO')
    estado = models.IntegerField(default=1, db_column='ESTADO') # 1:Borrador, 2:En Ruta, 3:Cerrada
    vlr_total_carga = models.DecimalField(max_digits=15, decimal_places=2, default=0, db_column='VLR_TOTAL_CARGA')
    total_peso = models.DecimalField(max_digits=10, decimal_places=2, default=0, db_column='TOTAL_PESO')
    cod_ruta = models.CharField(max_length=10, null=True, blank=True, db_column='COD_RUTA')
    observaciones = models.CharField(max_length=200, null=True, blank=True, db_column='OBSER')

    class Meta:
        managed = False
        db_table = 'CO_ENTREGAS'



class CoDocumentos(models.Model):
    id_documento = models.CharField(max_length=15, primary_key=True, db_column='ID_DOCUMENTO')
    tot_documento = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='TOT_DOCUMENTO')
    id_entrega = models.ForeignKey('CoEntregas', models.DO_NOTHING, db_column='ID_ENTREGA', null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'CO_DOCUMENTOS'
