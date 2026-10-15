from django.urls import path

from . import views_rendicion as v

app_name = 'rendiciones'

urlpatterns = [
    path('', v.lista, name='lista'),
    path('nueva/', v.nueva, name='nueva'),
    path('<int:pk>/', v.detalle, name='detalle'),
    path('<int:pk>/imprimir/', v.imprimir, name='imprimir'),
    path('pagos/', v.pagos, name='pagos'),
    path('cajas-chicas/', v.cajas, name='cajas'),
    path('tablero/', v.tablero, name='tablero'),
    path('antiguedad/', v.antiguedad, name='antiguedad'),
    path('configuracion/', v.configuracion, name='configuracion'),
    path('configuracion/<str:tipo>/nueva/', v.config_editar, name='config_nueva'),
    path('configuracion/<str:tipo>/<int:pk>/', v.config_editar, name='config_editar'),
]
