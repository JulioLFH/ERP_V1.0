from django.urls import path

from . import views_proyectos as v

app_name = 'proyectos'

urlpatterns = [
    path('', v.lista, name='lista'),
    path('nuevo/', v.nuevo, name='nuevo'),
    path('mis-tareas/', v.mis_tareas, name='mis_tareas'),
    path('<int:pk>/', v.proyecto, name='proyecto'),
    path('<int:pk>/editar/', v.editar, name='editar'),
    path('tareas/<int:pk>/', v.tarea, name='tarea'),
    path('tareas/<int:pk>/mover/', v.tarea_mover, name='tarea_mover'),
]
