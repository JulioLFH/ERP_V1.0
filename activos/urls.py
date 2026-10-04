from django.urls import path

from . import views

app_name = 'activos'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('nuevo/', views.nuevo, name='nuevo'),
    path('<int:pk>/', views.detalle, name='detalle'),
    path('<int:pk>/editar/', views.editar, name='editar'),
    path('<int:pk>/baja/', views.baja, name='baja'),
    path('<int:pk>/anular/', views.anular, name='anular'),
    path('depreciacion/', views.depreciacion, name='depreciacion'),
    path('depreciacion/<int:pk>/', views.proceso, name='proceso'),
    path('depreciacion/<int:pk>/revertir/', views.proceso_revertir, name='proceso_revertir'),
    path('registro/', views.registro, name='registro'),
    path('cuadre/', views.cuadre, name='cuadre'),
    path('categorias/', views.CategoriaLista.as_view(), name='categorias'),
    path('categorias/nueva/', views.CategoriaNueva.as_view(), name='categoria_nueva'),
    path('categorias/<int:pk>/editar/', views.CategoriaEditar.as_view(), name='categoria_editar'),
]
