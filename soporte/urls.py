from django.urls import path

from . import views

app_name = 'soporte'
urlpatterns = [
    path('', views.lista, name='lista'),
    path('nuevo/', views.nuevo, name='nuevo'),
    path('<int:pk>/', views.detalle, name='detalle'),
    path('adjunto/<int:pk>/', views.adjunto, name='adjunto'),
    path('reportar/', views.reportar, name='reportar'),
]
