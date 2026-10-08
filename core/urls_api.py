from django.urls import path

from . import api, api_datos as datos, views_api

app_name = 'api'

urlpatterns = [
    path('kardex/', datos.kardex, name='kardex'),
    path('cuentas-por-pagar/', datos.cuentas_por_pagar, name='cxp'),
    path('tesoreria/movimientos/', datos.movimientos_tesoreria, name='tesoreria'),
    path('produccion/ordenes/', datos.ordenes_produccion, name='ordenes_produccion'),
    path('contabilidad/cuentas/', datos.cuentas_contables, name='cuentas'),
    path('contabilidad/asientos/', datos.asientos, name='asientos'),
    path('contabilidad/asientos/<int:pk>/', datos.asiento, name='asiento'),
    path('contabilidad/libro-diario/', datos.libro_diario, name='libro_diario'),
    path('contabilidad/balance-comprobacion/', datos.balance_comprobacion, name='balance'),
    path('contabilidad/estado-resultados/', datos.estado_resultados, name='resultados'),
    path('contabilidad/situacion-financiera/', datos.situacion_financiera, name='situacion'),
    path('', api.indice, name='indice'),
    path('docs/', views_api.docs, name='docs'),
    path('openapi.json', api.openapi, name='openapi'),
    path('productos/', api.productos, name='productos'),
    path('productos/<int:pk>/', api.producto, name='producto'),
    path('productos/<int:pk>/imagen/', api.producto_imagen, name='producto_imagen'),
    path('stock/', api.stock, name='stock'),
    path('terceros/', api.terceros, name='terceros'),
    path('ventas/', api.ventas, name='ventas'),
    path('ventas/<int:pk>/', api.venta, name='venta'),
    path('cuentas-por-cobrar/', api.cuentas_por_cobrar, name='cxc'),
    path('compras/', api.compras, name='compras'),
    path('compras/<int:pk>/', api.compra, name='compra'),
]
