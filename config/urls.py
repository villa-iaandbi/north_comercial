"""
URL configuration for north_comercial project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from reportes import views as reportes_views

urlpatterns = [
    path('', RedirectView.as_view(url='impresion/'), name='home_redirect'),
    path('admin/', admin.site.urls),
    path('facturacion/', include('facturacion.urls')),
    path('recibos/', include('recibos.urls')),
    path('impresion/', include('impresion.urls')),
    path('logistica/', include('logistica.urls')),
    path('pos/', include('pos.urls')),
    path('reportes/', include('reportes.urls')),
    path('comercial/', include(('reportes.urls', 'comercial'), namespace='comercial')),
    path('api/exportar/balance/', reportes_views.exportar_balance_terceros),
    path('dian/conciliacion/', reportes_views.gestor_lotes_dian, name='gestor_lotes_dian'),
    path('dian/conciliacion/upload/', reportes_views.upload_excel_dian, name='upload_excel_dian'),
    path('dian/conciliacion/api/lotes/', reportes_views.api_dian_get_lotes, name='api_dian_get_lotes'),
    path('dian/conciliacion/api/lotes/<int:id_lote>/', reportes_views.api_dian_get_detalle, name='api_dian_get_detalle'),
    path('dian/conciliacion/api/lotes/<int:id_lote>/accion/', reportes_views.api_dian_accion_lote, name='api_dian_accion_lote'),
]
