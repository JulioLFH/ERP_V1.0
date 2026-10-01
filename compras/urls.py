from django.urls import path

from . import views

app_name = 'compras'

urlpatterns = views.compras_views.urls() + [
    path('ordenes/', views.oc_lista, name='oc_lista'),
    path('ordenes/nueva/', views.oc_nuevo, name='oc_nuevo'),
    path('ordenes/<int:pk>/', views.oc_detalle, name='oc_detalle'),
    path('ordenes/<int:pk>/editar/', views.oc_editar, name='oc_editar'),
    path('ordenes/<int:pk>/estado/', views.oc_estado, name='oc_estado'),
]
