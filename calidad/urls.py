from django.urls import path

from . import views

app_name = 'calidad'

urlpatterns = [
    path('', views.tablero, name='tablero'),
    path('registros/', views.registros_lista, name='registros'),
    path('registros/nuevo/', views.registro_nuevo, name='registro_nuevo'),
    path('registros/<slug:tipo>/<int:pk>/', views.registro, name='registro'),
    path('acciones/', views.acciones, name='acciones'),
    path('acciones/nueva/<int:ct_id>/<int:obj_id>/', views.accion_nueva, name='accion_nueva'),
    path('acciones/<int:pk>/', views.accion, name='accion'),
]
