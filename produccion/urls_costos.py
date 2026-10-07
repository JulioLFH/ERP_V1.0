from django.urls import path

from . import views_avanzado as va, views_costos as views

app_name = 'costos'

urlpatterns = [
    path('actividades/', va.costeo_abc, name='abc'),
    path('actividades/configurar/', va.actividades, name='actividades'),
    path('actividades/nueva/', va.actividad, name='actividad_nueva'),
    path('actividades/<int:pk>/', va.actividad, name='actividad'),
    path('', views.estandar, name='estandar'),
    path('hoja/<int:pk>/', views.hoja, name='hoja'),
    path('real-vs-estandar/', views.real_vs_estandar, name='real_vs_estandar'),
    path('absorcion/', views.absorcion, name='absorcion'),
    path('rentabilidad/', views.rentabilidad, name='rentabilidad'),
    path('liquidacion/', views.liquidacion, name='liquidacion'),
    path('liquidacion/gastos-de-planta/', views.comportamiento, name='comportamiento'),
    path('valor-neto-realizable/', views.vnr, name='vnr'),
    path('valor-neto-realizable/<int:pk>/', views.vnr_detalle, name='vnr_detalle'),
]
