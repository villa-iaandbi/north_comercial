from django.urls import path
from .views import facturacion_masiva_view, facturar_lote_api, fel_dashboard, fel_table_results, fel_document_detail, fel_bulk_retry

app_name = 'facturacion'

urlpatterns = [
    path('', facturacion_masiva_view, name='facturacion_masiva'),
    path('api/procesar_lote/', facturar_lote_api, name='facturar_lote_api'),
    path('monitoreo/', fel_dashboard, name='fel_dashboard'),
    path('monitoreo/htmx/rows/', fel_table_results, name='fel_table_results'),
    path('monitoreo/detalle/<str:id_documento>/', fel_document_detail, name='fel_document_detail'),
    path('monitoreo/bulk-retry/', fel_bulk_retry, name='fel_bulk_retry'),
]
