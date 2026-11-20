from django.urls import path
from pos import views
from pos import views_arqueo
from pos import views_auth
from pos import views_cajas
from pos import views_promociones
from pos import views_turno

app_name = 'pos'

urlpatterns = [
    # Maestro de Cajas (TS_CAJAS)
    path('cajas/', views_cajas.CajaListView.as_view(), name='cajas_lista'),
    path('cajas/nueva/', views_cajas.CajaCreateView.as_view(), name='cajas_nueva'),
    path('cajas/editar/<str:pk>/', views_cajas.CajaUpdateView.as_view(), name='cajas_editar'),
    path('cajas/api/consecutivos/', views_cajas.api_consecutivos, name='cajas_api_consecutivos'),

    # Login de cajeros (Supabase) y apertura de turno
    path('login/', views_auth.PosLoginView.as_view(), name='login'),
    path('logout/', views_auth.pos_logout, name='logout'),
    path('apertura/', views_turno.AperturaTurnoView.as_view(), name='apertura_turno'),

    # Arqueo en Caliente (tickets no consolidados)
    path('arqueo/', views_arqueo.ArqueoCalienteView.as_view(), name='arqueo_caliente'),

    # Descuentos y Promociones
    path('promociones/', views_promociones.PromocionListView.as_view(), name='promociones_lista'),

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
