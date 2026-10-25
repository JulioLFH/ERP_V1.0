from django.urls import path

from . import views_actividades as v

app_name = 'actividades'

urlpatterns = [
    path('', v.lista, name='lista'),
    path('nueva/', v.nueva, name='nueva'),
    path('<int:pk>/', v.editar, name='editar'),
    path('<int:pk>/hecha/', v.hecha, name='hecha'),
]
