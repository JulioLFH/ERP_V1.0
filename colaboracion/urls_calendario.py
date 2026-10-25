from django.urls import path

from . import views_calendario as v

app_name = 'calendario'

urlpatterns = [
    path('', v.mes, name='mes'),
    path('agenda/', v.agenda, name='agenda'),
    path('eventos/nuevo/', v.evento_nuevo, name='evento_nuevo'),
    path('eventos/<int:pk>/', v.evento, name='evento'),
]
