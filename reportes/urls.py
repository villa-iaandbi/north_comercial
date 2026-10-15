from django.urls import path
from reportes import views

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
]
