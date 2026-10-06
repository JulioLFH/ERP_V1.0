from django.contrib.auth import views as auth_views
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import path, reverse_lazy

from . import (alertas, inventario, libros_inventario, lotes, manuales, opciones, sustentos, usuarios, views,
               views_api)


class CambiarClave(SuccessMessageMixin, auth_views.PasswordChangeView):
    template_name = 'core/cambiar_clave.html'
    success_url = reverse_lazy('home')
    success_message = 'Su contraseña fue actualizada.'


urlpatterns = [
    path('', views.home, name='home'),
    path('tablero/', views.dashboard, name='dashboard'),
    path('cuenta/clave/', CambiarClave.as_view(), name='cambiar_clave'),
    path('cuenta/api/', views_api.claves, name='api_claves'),
    path('opciones/<str:fuente>/', opciones.opciones, name='opciones'),
    path('ajustes/usuarios/', usuarios.lista, name='usuarios'),
    path('ajustes/usuarios/nuevo/', usuarios.nuevo, name='usuario_nuevo'),
    path('ajustes/usuarios/<int:pk>/', usuarios.editar, name='usuario_editar'),
    path('empresa/', views.empresa_config, name='empresa'),
    path('facturacion-electronica/', views.facturacion_config, name='facturacion'),
    path('tipo-cambio/', views.tipos_cambio, name='tipos_cambio'),
    path('tipo-cambio/api/', views.tipo_cambio_api, name='tipo_cambio_api'),
    path('terceros/', views.TerceroLista.as_view(), name='terceros'),
    path('terceros/nuevo/', views.TerceroNuevo.as_view(), name='tercero_nuevo'),
    path('terceros/<int:pk>/', views.TerceroEditar.as_view(), name='tercero_editar'),
    path('productos/', views.ProductoLista.as_view(), name='productos'),
    path('productos/nuevo/', views.ProductoNuevo.as_view(), name='producto_nuevo'),
    path('productos/<int:pk>/', views.ProductoEditar.as_view(), name='producto_editar'),
    path('productos/<int:pk>/kardex/', inventario.kardex_producto, name='kardex'),
    path('productos/<int:pk>/imagen/', views.producto_imagen, name='producto_imagen'),
    path('productos/<int:pk>/variantes/', views.producto_variantes, name='producto_variantes'),
    path('ubigeos.json', views.ubigeos_json, name='ubigeos_json'),
    path('correo/', views.correo_config, name='correo'),
    path('carga-masiva/', views.carga_masiva, name='carga_masiva'),
    path('respaldo/', views.respaldo, name='respaldo'),
    path('manuales/', manuales.manuales, name='manuales'),
    path('manuales/<str:archivo>', manuales.manual_pdf, name='manual_pdf'),
    path('auditoria/', views.auditoria, name='auditoria'),
    path('sustentos/<int:ct_id>/<int:obj_id>/subir/', sustentos.subir, name='sustento_subir'),
    path('sustentos/<int:pk>/', sustentos.ver, name='sustento_ver'),
    path('series/', views.SerieLista.as_view(), name='series'),
    path('series/nueva/', views.SerieNueva.as_view(), name='serie_nueva'),
    path('series/<int:pk>/', views.SerieEditar.as_view(), name='serie_editar'),
    # inventario
    path('inventario/stock/', inventario.stock, name='inv_stock'),
    path('inventario/kardex/', inventario.kardex, name='inv_kardex'),
    path('inventario/libro-sunat/', libros_inventario.libro, name='inv_libro_sunat'),
    path('inventario/lotes/', lotes.lista, name='inv_lotes'),
    path('inventario/lotes/<int:pk>/', lotes.detalle, name='inv_lote'),
    path('alertas/', alertas.pagina, name='alertas'),
    path('alertas.json', alertas.resumen_json, name='alertas_json'),
    path('inventario/ajustes/', inventario.ajuste, name='inv_ajuste'),
    path('inventario/valorizacion/', inventario.valorizacion, name='inv_valorizacion'),
    path('inventario/reposicion/', inventario.reposicion, name='inv_reposicion'),
    path('inventario/almacenes/', inventario.AlmacenLista.as_view(), name='almacenes'),
    path('inventario/ubicaciones/', inventario.ubicaciones, name='ubicaciones'),
    path('inventario/almacenes/nuevo/', inventario.AlmacenNuevo.as_view(), name='almacen_nuevo'),
    path('inventario/almacenes/<int:pk>/', inventario.AlmacenEditar.as_view(), name='almacen_editar'),
]
