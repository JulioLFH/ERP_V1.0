from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('compras/', include('compras.urls')),
    path('ventas/', include('ventas.urls')),
    path('finanzas/', include('finanzas.urls')),
    path('logistica/', include('logistica.urls')),
    path('contabilidad/', include('contabilidad.urls')),
    path('inventario/operaciones/', include('inventario.urls')),
    path('', include('core.urls')),
]
