from django.urls import path

from . import inventario, views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
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
    path('series/', views.SerieLista.as_view(), name='series'),
    path('series/nueva/', views.SerieNueva.as_view(), name='serie_nueva'),
    path('series/<int:pk>/', views.SerieEditar.as_view(), name='serie_editar'),
    # inventario
    path('inventario/stock/', inventario.stock, name='inv_stock'),
    path('inventario/kardex/', inventario.kardex, name='inv_kardex'),
    path('inventario/ajustes/', inventario.ajuste, name='inv_ajuste'),
    path('inventario/valorizacion/', inventario.valorizacion, name='inv_valorizacion'),
    path('inventario/almacenes/', inventario.AlmacenLista.as_view(), name='almacenes'),
    path('inventario/almacenes/nuevo/', inventario.AlmacenNuevo.as_view(), name='almacen_nuevo'),
    path('inventario/almacenes/<int:pk>/', inventario.AlmacenEditar.as_view(), name='almacen_editar'),
]
