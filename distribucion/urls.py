from django.urls import path

from . import views

app_name = 'distribucion'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('nueva/', views.nueva, name='nueva'),
    path('<int:pk>/', views.hoja, name='hoja'),
    path('<int:pk>/imprimir/', views.imprimir, name='imprimir'),
    path('celular/', views.movil, name='movil'),
    path('reporte/', views.reporte, name='reporte'),
]
