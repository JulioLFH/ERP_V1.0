from django.urls import path

from . import views_costos as views

app_name = 'costos'

urlpatterns = [
    path('', views.estandar, name='estandar'),
    path('hoja/<int:pk>/', views.hoja, name='hoja'),
    path('real-vs-estandar/', views.real_vs_estandar, name='real_vs_estandar'),
    path('rentabilidad/', views.rentabilidad, name='rentabilidad'),
]
