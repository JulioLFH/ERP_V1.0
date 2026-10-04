from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.acceso import LoginSeguroView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', LoginSeguroView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('compras/', include('compras.urls')),
    path('ventas/', include('ventas.urls')),
    path('finanzas/', include('finanzas.urls')),
    path('logistica/', include('logistica.urls')),
    path('contabilidad/', include('contabilidad.urls')),
    path('inventario/operaciones/', include('inventario.urls')),
    path('portal/', include('proveedores.urls')),
    path('manufactura/', include('produccion.urls')),
    path('costos/', include('produccion.urls_costos')),
    path('activos/', include('activos.urls')),
    path('', include('core.urls')),
]
