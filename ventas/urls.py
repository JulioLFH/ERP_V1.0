from django.urls import path, re_path
from django.views.generic import RedirectView

from . import envio, pos, views

app_name = 'ventas'

urlpatterns = views.ventas_views.urls() + [
    path('pos/', pos.pos, name='pos'),
    path('pos/productos/', pos.pos_productos, name='pos_productos'),
    # el CRM es un módulo propio (/crm/): los enlaces guardados de /ventas/crm/… llevan allí
    re_path(r'^crm/(?P<resto>.*)$', RedirectView.as_view(url='/crm/%(resto)s', query_string=True), name='crm_antiguo'),
    path('<int:pk>/enviar/', envio.enviar_correo, name='enviar_correo'),
    path('<int:pk>/whatsapp/', envio.whatsapp, name='whatsapp'),
    path('cotizaciones/', views.cot_lista, name='cot_lista'),
    path('cotizaciones/nueva/', views.cot_nuevo, name='cot_nuevo'),
    path('cotizaciones/<int:pk>/', views.cot_detalle, name='cot_detalle'),
    path('cotizaciones/<int:pk>/editar/', views.cot_editar, name='cot_editar'),
    path('cotizaciones/<int:pk>/estado/', views.cot_estado, name='cot_estado'),
    path('precio/', views.precio, name='precio'),
    path('listas-precios/', views.listas_precios, name='listas_precios'),
    path('listas-precios/nueva/', views.lista_precios_nueva, name='lista_precios_nueva'),
    path('listas-precios/<int:pk>/', views.lista_precios_editar, name='lista_precios_editar'),
]
