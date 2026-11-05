from django.urls import path

from . import views

app_name = 'agentes'

urlpatterns = [
    path('ventas/', views.ventas, name='ventas'),
    path('configuracion/', views.configuracion, name='configuracion'),
    path('conversaciones/', views.conversaciones, name='conversaciones'),
    path('conversaciones/<int:pk>/', views.conversacion, name='conversacion'),
]
