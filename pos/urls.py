from django.urls import path
from pos import views

app_name = 'pos'

urlpatterns = [
    path('', views.pos_home, name='pos_home'),
    path('api/catalog/', views.api_catalog, name='api_catalog'),
    path('api/terceros/', views.api_terceros, name='api_terceros'),
    path('api/shift/open/', views.api_shift_open, name='api_shift_open'),
    path('api/shift/status/', views.api_shift_status, name='api_shift_status'),
    path('api/sync-tickets/', views.api_sync_tickets, name='api_sync_tickets'),
    path('api/cierre-z/', views.api_cierre_z, name='api_cierre_z'),
    
    # Cap 7: Promociones y Fidelización
    path('api/promociones/', views.api_promociones, name='api_promociones'),
    path('api/puntos/saldo/', views.api_puntos_saldo, name='api_puntos_saldo'),
    path('api/puntos/redimir/', views.api_puntos_redimir, name='api_puntos_redimir'),
]
