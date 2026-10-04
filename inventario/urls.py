from django.urls import path

from . import views

app_name = 'inventario'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('nueva/', views.nueva, name='nueva'),
    path('<int:pk>/', views.detalle, name='detalle'),
    path('<int:pk>/editar/', views.editar, name='editar'),
    path('<int:pk>/pendientes/', views.pendientes_origen, name='pendientes'),
    path('<int:pk>/confirmar/', views.confirmar, name='confirmar'),
    path('<int:pk>/anular/', views.anular, name='anular'),
    path('<int:pk>/eliminar/', views.eliminar, name='eliminar'),
    path('<int:pk>/imprimir/', views.imprimir, name='imprimir'),
    path('<int:pk>/conformidad/', views.conformidad, name='conformidad'),
    path('tipos/', views.TipoLista.as_view(), name='tipos'),
    path('tipos/nuevo/', views.TipoNuevo.as_view(), name='tipo_nuevo'),
    path('tipos/<int:pk>/', views.TipoEditar.as_view(), name='tipo_editar'),
]
