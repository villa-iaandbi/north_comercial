from django.urls import path
from . import views

app_name = 'logistica'

urlpatterns = [
    path('entregas/', views.entregas_list_view, name='entregas_list'),
    path('entregas/table-partial/', views.entregas_table_partial, name='entregas_table_partial'),
    path('entregas/generar-action/', views.generar_entrega_action, name='generar_entrega_action'),
]
