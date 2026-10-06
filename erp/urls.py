from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import doble_factor
from core.acceso import LoginSeguroView, cambiar_empresa

# el panel de Django solo se publica con ADMIN_URL (en producción no hay /admin/)
urlpatterns = [path(f'{settings.ADMIN_URL}/', admin.site.urls)] if settings.ADMIN_URL else []
urlpatterns += [
    path('login/', LoginSeguroView.as_view(), name='login'),
    path('login/verificar/', doble_factor.verificar_login, name='login_2fa'),
    path('recuperar/', doble_factor.recuperar, name='recuperar'),
    path('recuperar/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='registration/recuperar_confirmar.html', success_url='/recuperar/listo/'),
        name='recuperar_confirmar'),
    path('recuperar/listo/', auth_views.PasswordResetCompleteView.as_view(
        template_name='registration/recuperar_listo.html'), name='recuperar_listo'),
    path('cuenta/seguridad/', doble_factor.seguridad, name='seguridad'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('cambiar-empresa/', cambiar_empresa, name='cambiar_empresa'),
    path('api/v1/', include('core.urls_api')),
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
    path('planillas/', include('planillas.urls')),
    path('historial/', include('historial.urls')),
    path('requerimientos/', include('inventario.urls_requerimientos')),
    path('', include('core.urls')),
]
