from django.urls import path

from . import views_barras as v

app_name = 'codigo_barras'

urlpatterns = [
    path('', v.escanear, name='escanear'),
    path('etiquetas/', v.etiquetas, name='etiquetas'),
    path('asignar/', v.asignar, name='asignar'),
]
