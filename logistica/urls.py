from django.urls import path

from . import views

app_name = 'logistica'

urlpatterns = [
    path('guias/', views.lista, name='lista'),
    path('guias/nueva/', views.nueva, name='nueva'),
    path('guias/<int:pk>/', views.detalle, name='detalle'),
    path('guias/<int:pk>/editar/', views.editar, name='editar'),
    path('guias/<int:pk>/imprimir/', views.imprimir, name='imprimir'),
    path('guias/<int:pk>/anular/', views.anular, name='anular'),
    path('guias/<int:pk>/sunat/', views.enviar_sunat, name='enviar_sunat'),
    path('vehiculos/', views.VehiculoLista.as_view(), name='vehiculos'),
    path('vehiculos/nuevo/', views.VehiculoNuevo.as_view(), name='vehiculo_nuevo'),
    path('vehiculos/<int:pk>/', views.VehiculoEditar.as_view(), name='vehiculo_editar'),
    path('conductores/', views.ConductorLista.as_view(), name='conductores'),
    path('conductores/nuevo/', views.ConductorNuevo.as_view(), name='conductor_nuevo'),
    path('conductores/<int:pk>/', views.ConductorEditar.as_view(), name='conductor_editar'),
]
