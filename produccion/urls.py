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
    path('listas/', views.listas, name='listas'),
    path('listas/nueva/', views.lista_nueva, name='lista_nueva'),
    path('listas/<int:pk>/', views.lista_detalle, name='lista'),
    path('listas/<int:pk>/editar/', views.lista_editar, name='lista_editar'),
    path('listas/<int:pk>/version/', views.lista_version, name='lista_version'),
    path('centros/', views.CentroLista.as_view(), name='centros'),
    path('centros/nuevo/', views.CentroNuevo.as_view(), name='centro_nuevo'),
    path('centros/<int:pk>/editar/', views.CentroEditar.as_view(), name='centro_editar'),
]
