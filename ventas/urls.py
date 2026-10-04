from django.urls import path

from . import envio, views

app_name = 'ventas'

urlpatterns = views.ventas_views.urls() + [
    path('<int:pk>/enviar/', envio.enviar_correo, name='enviar_correo'),
    path('<int:pk>/whatsapp/', envio.whatsapp, name='whatsapp'),
    path('cotizaciones/', views.cot_lista, name='cot_lista'),
    path('cotizaciones/nueva/', views.cot_nuevo, name='cot_nuevo'),
    path('cotizaciones/<int:pk>/', views.cot_detalle, name='cot_detalle'),
    path('cotizaciones/<int:pk>/editar/', views.cot_editar, name='cot_editar'),
    path('cotizaciones/<int:pk>/estado/', views.cot_estado, name='cot_estado'),
]
