from django.urls import path

from . import views, views_almacen

app_name = 'inventario'

urlpatterns = [
    path('olas/', views_almacen.olas, name='olas'),
    path('olas/nueva/', views_almacen.ola_nueva, name='ola_nueva'),
    path('olas/<int:pk>/', views_almacen.ola, name='ola'),
    path('ciclico/', views_almacen.ciclico, name='ciclico'),
    path('ciclico/<int:pk>/', views_almacen.conteo, name='conteo'),
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
    path('cierres/', views.cierres, name='cierres'),
    path('cierres/<int:pk>/', views.cierre_detalle, name='cierre'),
    path('cierres/<int:pk>/reabrir/', views.cierre_reabrir, name='cierre_reabrir'),
    path('tipos/', views.TipoLista.as_view(), name='tipos'),
    path('tipos/nuevo/', views.TipoNuevo.as_view(), name='tipo_nuevo'),
    path('tipos/<int:pk>/', views.TipoEditar.as_view(), name='tipo_editar'),
]
