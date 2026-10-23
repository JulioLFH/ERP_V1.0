from django.urls import path

from . import views

app_name = 'obras'

urlpatterns = [
    path('', views.obras_lista, name='lista'),
    path('nueva/', views.ObraNueva.as_view(), name='obra_nueva'),
    path('<int:pk>/', views.obra, name='obra'),
    path('<int:pk>/editar/', views.ObraEditar.as_view(), name='obra_editar'),
    path('<int:pk>/presupuestos/nuevo/', views.presupuesto_nuevo, name='presupuesto_nuevo'),
    path('<int:pk>/importar/', views.importar, name='importar'),
    path('presupuestos/<int:pk>/', views.presupuesto, name='presupuesto'),
    path('presupuestos/<int:pk>/precios/', views.precios, name='precios'),
    path('presupuestos/<int:pk>/insumos/', views.insumos, name='insumos'),
    path('presupuestos/<int:pk>/analisis/', views.apu_reporte, name='apu_reporte'),
    path('partidas/<int:pk>/analisis/', views.apu, name='apu'),
    path('recursos/', views.RecursoLista.as_view(), name='recursos'),
    path('recursos/nuevo/', views.RecursoNuevo.as_view(), name='recurso_nuevo'),
    path('recursos/<int:pk>/editar/', views.RecursoEditar.as_view(), name='recurso_editar'),
    path('biblioteca/', views.biblioteca, name='biblioteca'),
    path('plantilla/', views.plantilla, name='plantilla'),
]
