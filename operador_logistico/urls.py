from django.urls import path

from . import views, views_movil

app_name = 'operador_logistico'

urlpatterns = [
    path('', views.tablero, name='tablero'),
    path('solicitudes/', views.solicitudes, name='solicitudes'),
    path('solicitudes/nueva/', views.solicitud_nueva, name='solicitud_nueva'),
    path('solicitudes/<int:pk>/', views.solicitud, name='solicitud'),
    path('rutas/', views.rutas, name='rutas'),
    path('rutas/<int:pk>/hoja/', views.ruta_hoja, name='ruta_hoja'),
    path('guias/', views.guias, name='guias'),
    path('guias/<int:pk>/', views.guia, name='guia'),
    path('planificacion/', views.planificacion, name='planificacion'),
    path('consolidados/', views.consolidados, name='consolidados'),
    path('consolidados/<int:pk>/', views.consolidado, name='consolidado'),
    path('consolidados/<int:pk>/hoja/', views.hoja_despacho, name='hoja_despacho'),
    path('bitacora/', views.bitacora, name='bitacora'),
    path('recibido-ceibago/', views.recibido_ceibago, name='recibido_ceibago'),
    path('costos/', views.costos, name='costos'),
    path('clientes/', views.clientes, name='clientes'),
    path('clientes/<int:pk>/', views.cliente, name='cliente'),
    path('estados/', views.estados, name='estados'),
    path('configuracion/', views.configuracion, name='configuracion'),
    # app del operador (celular)
    path('celular/', views_movil.movil, name='movil'),
    path('celular/pendientes/', views_movil.pendientes, name='movil_pendientes'),
    path('celular/verificar/', views_movil.verificar, name='movil_verificar'),
    path('celular/registrar/', views_movil.registrar, name='movil_registrar'),
]
