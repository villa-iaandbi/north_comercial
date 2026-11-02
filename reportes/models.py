# -*- coding: utf-8 -*-
from django.db import models


class ParametroExogena(models.Model):
    """
    SPEC-013: Parametrización de Información Exógena DIAN.

    La tabla CO_PARAM_EXOGENA ya existe físicamente en Oracle y su ID_PARAMETRO
    es poblado por un Trigger/Secuencia, por lo que el modelo es managed = False
    y el campo PK se omite en el INSERT (Django lo recupera con RETURNING).
    """

    ORIGEN_SALDO = 'SALDO'
    ORIGEN_DIFERENCIA = 'DIFERENCIA'
    ORIGEN_DEBITO = 'DEBITO'
    ORIGEN_CREDITO = 'CREDITO'
    ORIGEN_CUBO_CHOICES = [
        (ORIGEN_SALDO, 'Saldo'),
        (ORIGEN_DIFERENCIA, 'Diferencia'),
        (ORIGEN_DEBITO, 'Débito'),
        (ORIGEN_CREDITO, 'Crédito'),
    ]

    ESTADO_ACTIVO = 'A'
    ESTADO_INACTIVO = 'I'
    ESTADO_CHOICES = [
        (ESTADO_ACTIVO, 'Activo'),
        (ESTADO_INACTIVO, 'Inactivo'),
    ]

    id_parametro = models.AutoField(primary_key=True, db_column='ID_PARAMETRO')
    formato_dian = models.CharField('Formato DIAN', max_length=10, db_column='FORMATO_DIAN')
    concepto_dian = models.CharField('Concepto DIAN', max_length=10, db_column='CONCEPTO_DIAN')
    cuenta_contable = models.CharField('Cuenta contable', max_length=20, db_column='CUENTA_CONTABLE')
    origen_cubo = models.CharField(
        'Origen del cubo', max_length=15, choices=ORIGEN_CUBO_CHOICES,
        db_column='ORIGEN_CUBO', default=ORIGEN_SALDO,
    )
    columna_xml = models.CharField('Columna XML', max_length=50, db_column='COLUMNA_XML')
    tope_cuantia_menor = models.DecimalField(
        'Tope cuantía menor', max_digits=18, decimal_places=2,
        db_column='TOPE_CUANTIA_MENOR', default=0,
    )
    estado = models.CharField(
        'Estado', max_length=1, choices=ESTADO_CHOICES,
        db_column='ESTADO', default=ESTADO_ACTIVO,
    )
    observaciones = models.CharField(
        'Observaciones', max_length=250, null=True, blank=True, db_column='OBSERVACIONES',
    )
    ano_fiscal = models.CharField('Año fiscal', max_length=4, db_column='ANO_FISCAL')

    class Meta:
        managed = False
        db_table = 'CO_PARAM_EXOGENA'
        verbose_name = 'Parámetro de Exógena'
        verbose_name_plural = 'Parámetros de Exógena'

    def __str__(self):
        return f'{self.ano_fiscal} · {self.formato_dian}/{self.concepto_dian} · {self.cuenta_contable}'
