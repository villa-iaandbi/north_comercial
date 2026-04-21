from django.urls import path
from .views import facturacion_masiva_view

app_name = 'facturacion'

urlpatterns = [
    path('', facturacion_masiva_view, name='facturacion_masiva'),
]
