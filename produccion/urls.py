from django.urls import path

from . import views

app_name = 'manufactura'

urlpatterns = [
    path('', views.ordenes, name='ordenes'),
    path('ordenes/nueva/', views.orden_nueva, name='orden_nueva'),
    path('ordenes/<int:pk>/', views.orden_detalle, name='orden'),
    path('ordenes/<int:pk>/editar/', views.orden_editar, name='orden_editar'),
    path('ordenes/<int:pk>/confirmar/', views.orden_confirmar, name='orden_confirmar'),
    path('ordenes/<int:pk>/iniciar/', views.orden_iniciar, name='orden_iniciar'),
    path('ordenes/<int:pk>/terminar/', views.orden_terminar, name='orden_terminar'),
    path('ordenes/<int:pk>/anular/', views.orden_anular, name='orden_anular'),
    path('ordenes/<int:pk>/eliminar/', views.orden_eliminar, name='orden_eliminar'),
    path('requerimiento/', views.requerimiento, name='requerimiento'),
    path('planificacion/', views.mrp, name='mrp'),
    path('capacidad/', views.capacidad, name='capacidad'),
    path('listas/', views.listas, name='listas'),
    path('listas/nueva/', views.lista_nueva, name='lista_nueva'),
    path('listas/<int:pk>/', views.lista_detalle, name='lista'),
    path('listas/<int:pk>/editar/', views.lista_editar, name='lista_editar'),
    path('listas/<int:pk>/version/', views.lista_version, name='lista_version'),
    path('hojas-ruta/', views.hojas, name='hojas'),
    path('hojas-ruta/nueva/', views.hoja_nueva, name='hoja_nueva'),
    path('hojas-ruta/<int:pk>/editar/', views.hoja_editar, name='hoja_editar'),
    path('hojas-ruta/<int:pk>/obsoleta/', views.obsoleta, {'tipo': 'hoja'}, name='hoja_obsoleta'),
    path('listas/<int:pk>/obsoleta/', views.obsoleta, {'tipo': 'lista'}, name='lista_obsoleta'),
    path('versiones/', views.VersionLista.as_view(), name='versiones'),
    path('versiones/nueva/', views.VersionNueva.as_view(), name='version_nueva'),
    path('versiones/<int:pk>/editar/', views.VersionEditar.as_view(), name='version_editar'),
    path('puestos/', views.CentroLista.as_view(), name='centros'),
    path('puestos/nuevo/', views.CentroNuevo.as_view(), name='centro_nuevo'),
    path('puestos/<int:pk>/editar/', views.CentroEditar.as_view(), name='centro_editar'),
]
