from django.urls import path

from proveedores import views_erp as portal

from . import views

app_name = 'compras'

urlpatterns = views.compras_views.urls() + [
    path('ordenes/', views.oc_lista, name='oc_lista'),
    path('ordenes/nueva/', views.oc_nuevo, name='oc_nuevo'),
    path('ordenes/<int:pk>/', views.oc_detalle, name='oc_detalle'),
    path('ordenes/<int:pk>/editar/', views.oc_editar, name='oc_editar'),
    path('ordenes/<int:pk>/estado/', views.oc_estado, name='oc_estado'),
    path('ordenes/<int:pk>/enviar/', portal.oc_enviar, name='oc_enviar'),
    # portal de proveedores (lado del ERP)
    path('portal/facturas/', portal.facturas, name='portal_facturas'),
    path('portal/facturas/<int:pk>/', portal.factura_detalle, name='portal_factura'),
    path('portal/facturas/<int:pk>/sunat/', portal.factura_sunat, name='portal_factura_sunat'),
    path('portal/facturas/<int:pk>/archivo/<str:tipo>/', portal.factura_archivo, name='portal_factura_archivo'),
    path('portal/facturas/<int:pk>/aprobar/', portal.factura_aprobar, name='portal_factura_aprobar'),
    path('portal/facturas/<int:pk>/rechazar/', portal.factura_rechazar, name='portal_factura_rechazar'),
    path('portal/accesos/', portal.accesos, name='portal_accesos'),
    path('portal/accesos/nuevo/', portal.acceso_nuevo, name='portal_acceso_nuevo'),
    path('portal/accesos/<int:pk>/', portal.acceso_editar, name='portal_acceso_editar'),
]
