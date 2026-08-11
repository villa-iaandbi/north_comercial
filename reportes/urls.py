from django.urls import path
from reportes import views

app_name = 'reportes'

urlpatterns = [
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('cartera/', views.cartera_view, name='cartera'),
    path('cierre-caja/', views.cierre_caja_view, name='cierre_caja'),
]
