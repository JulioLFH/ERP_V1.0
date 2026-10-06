from django.urls import path

from . import views

# sin namespace: el acceso por módulo se define en core/modulos.py (RUTAS_CORE)
urlpatterns = [
    path('kardex/', views.kardex, name='hist_kardex'),
    path('asientos/', views.asientos, name='hist_asientos'),
    path('balance/', views.balance, name='hist_balance'),
    path('fabricacion/', views.fabricacion, name='hist_fabricacion'),
    path('fabricacion/<int:pk>/', views.orden, name='hist_orden'),
    path('posiciones-presupuestarias/', views.posiciones, name='hist_posiciones'),
]
