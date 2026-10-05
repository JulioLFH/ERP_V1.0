from django.urls import path

from . import api, views_api

app_name = 'api'

urlpatterns = [
    path('', api.indice, name='indice'),
    path('docs/', views_api.docs, name='docs'),
    path('openapi.json', api.openapi, name='openapi'),
    path('productos/', api.productos, name='productos'),
    path('productos/<int:pk>/', api.producto, name='producto'),
    path('stock/', api.stock, name='stock'),
    path('terceros/', api.terceros, name='terceros'),
    path('ventas/', api.ventas, name='ventas'),
    path('ventas/<int:pk>/', api.venta, name='venta'),
    path('cuentas-por-cobrar/', api.cuentas_por_cobrar, name='cxc'),
    path('compras/', api.compras, name='compras'),
    path('compras/<int:pk>/', api.compra, name='compra'),
]
