from django.urls import path
from reportes import views
from . import views_inventario
from . import views_cartera
from . import views_cxp

app_name = 'reportes'

urlpatterns = [
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('cartera/', views.cartera_view, name='cartera'),
    path('cierre-caja/', views.cierre_caja_view, name='cierre_caja'),

    # SPEC-001: Análisis Ventas - Clientes
    path('analisis-ventas/', views.analisis_ventas_clientes_view,
         name='analisis_ventas_clientes'),
    path('analisis-ventas/api/top-clientes-zona/',
         views.api_top_clientes_zona, name='api_top_clientes_zona'),

    # SPEC-002: Análisis Ventas - Productos
    path('analisis-productos/', views.analisis_ventas_productos_view,
         name='analisis_ventas_productos'),
    path('analisis-productos/api/top-productos-dinamico/',
         views.api_top_productos_dinamico, name='api_top_productos_dinamico'),

    # SPEC-003: Tablero Ejecutivo - Análisis Ventas Global y Rentabilidad
     path('analisis-ventas-global/', views.analisis_ventas_global_view,
         name='analisis_ventas_global'),

    # SPEC-010: Tablero Gerencial (Análisis Financiero)
    path('analisis-financiero/', views.tablero_gerencial_view, name='tablero_analisis_financiero'),

    # SPEC-012: Balance de Comprobación
    path('balance-comprobacion/', views.balance_comprobacion_view, name='balance_comprobacion'),
    path('api/exportar/balance/', views.exportar_balance_terceros, name='exportar_balance'),

    # SPEC-011: Módulo de Conciliación Electrónica DIAN vs ERP
    path('dian/conciliacion/', views.gestor_lotes_dian, name='gestor_lotes_dian'),
    path('dian/conciliacion/upload/', views.upload_excel_dian, name='upload_excel_dian'),
    path('dian/conciliacion/api/lotes/', views.api_dian_get_lotes, name='api_dian_get_lotes'),
    path('dian/conciliacion/api/lotes/<int:id_lote>/', views.api_dian_get_detalle, name='api_dian_get_detalle'),
    path('dian/conciliacion/api/lotes/<int:id_lote>/accion/', views.api_dian_accion_lote, name='api_dian_accion_lote'),

    # SPEC-004: Tablero de Control de Inventarios
    path('control-inventarios/', views_inventario.control_inventarios_view,
         name='control_inventarios'),

    # SPEC-005: Tablero de Control de Cartera y Recaudos
    path('control-cartera/', views_cartera.control_cartera_view,
         name='control_cartera'),

    # SPEC-006: Tablero de Cuentas por Pagar (CxP)
    path('control-cxp/', views_cxp.control_cxp_view,
         name='control_cxp'),

    # SPEC-007: Tablero de Compras Sugeridas (Smart Replenishment)
    path('compras-sugeridas/', views.tablero_compras_sugeridas,
         name='compras_sugeridas'),
    path('api/compras/guardar-orden/', views.api_guardar_orden_compra,
         name='api_guardar_orden_compra'),
     path('api/compras/crear-orden/', views.crear_orden_compra,
          name='crear_orden_compra'),
     path('compras/bandeja/', views.bandeja_ordenes_compra, name='bandeja_ordenes_compra'),
     path('api/compras/anular-orden/', views.api_anular_orden_compra, name='api_anular_orden_compra'),
]



