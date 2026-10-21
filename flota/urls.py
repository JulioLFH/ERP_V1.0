from django.urls import path

from . import views

app_name = 'flota'

urlpatterns = [
    path('equipos/', views.equipos, name='equipos'),
    path('equipos/nuevo/', views.equipo, name='equipo_nuevo'),
    path('equipos/<int:pk>/', views.equipo, name='equipo'),
    path('documentos/', views.documentos, name='documentos'),
    path('documentos/<int:pk>/archivo/', views.documento_archivo, name='documento_archivo'),
    path('lecturas/parte/', views.parte, name='parte'),
    path('lecturas/', views.lecturas, name='lecturas'),
]
