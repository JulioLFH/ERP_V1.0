from django.urls import path

from . import views

app_name = 'asistente'

urlpatterns = [
    path('', views.chat, name='chat'),
    path('casos/', views.casos, name='casos'),
    path('casos/<int:pk>/', views.caso, name='caso'),
    path('fichas/', views.fichas, name='fichas'),
    path('fichas/nueva/', views.ficha_nueva, name='ficha_nueva'),
    path('fichas/importar/', views.importar, name='importar'),
    path('fichas/<int:pk>/', views.ficha, name='ficha'),
    path('fichas/<int:pk>/editar/', views.ficha_editar, name='ficha_editar'),
    path('conocimiento/', views.manuales, name='manuales'),
    path('conocimiento/nuevo/', views.manual_nuevo, name='manual_nuevo'),
    path('conocimiento/<int:pk>/', views.manual, name='manual'),
    path('conocimiento/<int:pk>/editar/', views.manual_editar, name='manual_editar'),
    path('procesos/', views.procesos, name='procesos'),
    path('procesos/<int:pk>/', views.proceso, name='proceso'),
    path('configuracion/', views.configuracion, name='configuracion'),
    path('uso-inadecuado/', views.uso, name='uso'),
]
