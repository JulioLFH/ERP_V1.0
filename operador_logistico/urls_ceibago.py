from django.urls import path

from . import api_ceibago as api

app_name = 'api_ceibago'

urlpatterns = [
    path('cliente/', api.cliente, name='cliente'),
    path('solicitud/', api.solicitud, name='solicitud'),
    path('solicitudes/', api.solicitudes, name='solicitudes'),
    path('comprobantes/', api.comprobantes, name='comprobantes'),
    path('<str:tipo>/', api.cola, name='cola'),
]
