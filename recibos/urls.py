from django.urls import path
from . import views

app_name = 'recibos'

urlpatterns = [
    path('procesar/', views.lote_recibos_view, name='lote_recibos'),
    path('api/procesar_lote/', views.procesar_recibos_api, name='procesar_recibos_api'),
]
