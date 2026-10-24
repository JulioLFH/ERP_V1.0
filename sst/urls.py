from django.urls import path

from . import views

app_name = 'sst'

urlpatterns = [
    path('', views.tablero, name='tablero'),
    path('registros/', views.registros_lista, name='registros'),
    path('registros/nuevo/', views.registro_nuevo, name='registro_nuevo'),
    path('registros/<slug:tipo>/<int:pk>/', views.registro, name='registro'),
    path('indices/', views.indices, name='indices'),
]
