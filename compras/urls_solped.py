"""Solicitudes de pedido: las registra cualquier área (no exige el módulo Compras); cada acción valida su permiso."""
from django.urls import path

from . import views_solped as v

app_name = 'solicitudes'

urlpatterns = [
    path('', v.lista, name='lista'),
    path('nueva/', v.nueva, name='nueva'),
    path('liberar/', v.movil, name='movil'),
    path('<int:pk>/', v.detalle, name='detalle'),
    path('<int:pk>/editar/', v.editar, name='editar'),
] + [path(f'<int:pk>/{que}/', v.accion, {'que': que}, name=que)
     for que in ('enviar', 'liberar', 'observar', 'rechazar', 'anular', 'cerrar')]
