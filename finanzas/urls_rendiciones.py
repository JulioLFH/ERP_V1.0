from django.urls import path

from . import views_rendicion as v, views_rendicion_extra as x, views_rendicion_movil as m

app_name = 'rendiciones'

urlpatterns = [
    path('', v.lista, name='lista'),
    path('nueva/', v.nueva, name='nueva'),
    path('<int:pk>/', v.detalle, name='detalle'),
    path('<int:pk>/imprimir/', v.imprimir, name='imprimir'),
    path('<int:pk>/movilidad/', x.movilidad, name='movilidad'),
    path('pagos/', v.pagos, name='pagos'),
    path('cajas-chicas/', v.cajas, name='cajas'),
    path('tablero/', v.tablero, name='tablero'),
    path('antiguedad/', v.antiguedad, name='antiguedad'),
    path('configuracion/', v.configuracion, name='configuracion'),
    path('configuracion/integraciones/', x.integraciones, name='integraciones'),
    path('configuracion/<str:tipo>/nueva/', v.config_editar, name='config_nueva'),
    path('configuracion/<str:tipo>/<int:pk>/', v.config_editar, name='config_editar'),
    # tarjetas corporativas
    path('tarjetas/', x.tarjetas, name='tarjetas'),
    path('tarjetas/nueva/', x.tarjeta_editar, name='tarjeta_nueva'),
    path('tarjetas/<int:pk>/', x.tarjeta, name='tarjeta'),
    path('tarjetas/<int:pk>/editar/', x.tarjeta_editar, name='tarjeta_editar'),
    path('tarjetas/sin-comprobante/', x.sin_comprobante, name='sin_comprobante'),
    # proyectos y sitios
    path('proyectos/', x.proyectos, name='proyectos'),
    path('proyectos/nuevo/', x.proyecto_editar, name='proyecto_nuevo'),
    path('proyectos/<int:pk>/', x.proyecto_editar, name='proyecto_editar'),
    path('sitios/nuevo/', x.sitio_editar, name='sitio_nuevo'),
    path('sitios/<int:pk>/', x.sitio_editar, name='sitio_editar'),
    # app del celular y lecturas
    path('movil/', m.movil, name='movil'),
    path('movil/gasto/', m.movil_gasto, name='movil_gasto'),
    path('movil/rendicion/', m.movil_nueva, name='movil_nueva'),
    path('movil/aprobar/', m.movil_aprobar, name='movil_aprobar'),
    path('movil/aprobar/<int:pk>/', m.movil_aprobar, name='movil_aprobar_detalle'),
    path('duplicado/', m.duplicado, name='duplicado'),
    path('trazabilidad/', x.trazabilidad, name='trazabilidad'),
    path('errores/', x.errores, name='errores'),
    path('integracion-erp/', x.integracion, name='integracion'),
    path('manifest.json', m.manifest, name='manifest'),
    path('sw.js', m.service_worker, name='sw'),
    path('ruc/<str:numero>/', m.ruc, name='ruc'),
    path('qr/', m.qr, name='qr'),
    path('leer-ia/', m.leer_ia, name='leer_ia'),
    path('whatsapp/', m.whatsapp, name='whatsapp'),
]
