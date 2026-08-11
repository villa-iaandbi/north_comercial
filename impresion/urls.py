from django.urls import path
from . import views

app_name = 'impresion'

urlpatterns = [
    path('portal/', views.portal_impresion, name='portal_impresion'),
    path('resultados/', views.impresion_table_results, name='impresion_table_results'),
    path('descargar/<int:id_documento>/', views.descargar_factura_pdf, name='descargar_factura_pdf'),
    path('descargar-docx/<str:id_documento>/', views.descargar_factura_docx, name='descargar_factura_docx'),
    path('generar-masivos/', views.generar_pdfs_seleccionados, name='generar_pdfs_masivos'),
]
