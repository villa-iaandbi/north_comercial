from django.urls import path
from .views import facturacion_masiva_view, facturar_lote_api

app_name = 'facturacion'

urlpatterns = [
    path('', facturacion_masiva_view, name='facturacion_masiva'),
    path('api/procesar_lote/', facturar_lote_api, name='facturar_lote_api'),
]
