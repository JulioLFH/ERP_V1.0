from django.urls import path

from . import views

app_name = 'portal'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('ordenes/<int:pk>/', views.oc_detalle, name='oc_detalle'),
    path('ordenes/<int:pk>/responder/', views.oc_responder, name='oc_responder'),
    path('ordenes/<int:pk>/facturar/', views.factura_nueva, name='factura_nueva'),
    path('facturas/<int:pk>/', views.factura, name='factura'),
    path('aceptar/<str:token>/', views.oc_aceptacion, name='oc_aceptacion'),
]
