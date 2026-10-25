from django.urls import path

from . import toma_pedido as v

app_name = 'toma_pedido'

urlpatterns = [
    path('', v.lista, name='lista'),
    path('nuevo/', v.nuevo, name='nuevo'),
    path('buscar.json', v.buscar, name='buscar'),
    path('catalogo/', v.catalogo, name='catalogo'),
    path('<int:pk>/', v.pedido, name='pedido'),
]
