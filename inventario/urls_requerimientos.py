from django.urls import path

from . import views_requerimientos as views

app_name = 'requerimientos'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('nuevo/', views.nuevo, name='nuevo'),
    path('<int:pk>/', views.detalle, name='detalle'),
    path('<int:pk>/editar/', views.editar, name='editar'),
    path('<int:pk>/enviar/', views.accion, {'que': 'enviar'}, name='enviar'),
    path('<int:pk>/aprobar/', views.accion, {'que': 'aprobar'}, name='aprobar'),
    path('<int:pk>/rechazar/', views.accion, {'que': 'rechazar'}, name='rechazar'),
    path('<int:pk>/anular/', views.accion, {'que': 'anular'}, name='anular'),
    path('<int:pk>/atender/', views.accion, {'que': 'atender'}, name='atender'),
    path('<int:pk>/comprar/', views.accion, {'que': 'comprar'}, name='comprar'),
]
