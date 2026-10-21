from django.urls import path

from proveedores import views_erp as portal

from . import (views, views_abastecimiento, views_hes, views_importacion, views_registro_info, views_sire,
               views_solped, views_xml)

app_name = 'compras'

urlpatterns = views.compras_views.urls() + [
    path('<int:pk>/distribuir/', views.distribuir, name='distribuir'),
    path('<int:pk>/liberar-pago/', views.liberar_pago, name='liberar_pago'),
    path('solicitudes/bandeja/', views_solped.bandeja, name='solped_bandeja'),
    path('solicitudes/reglas/', views_solped.reglas, name='solped_reglas'),
    path('solicitudes/reportes/', views_solped.reportes, name='solped_reportes'),
    path('ordenes/<int:oc_pk>/conformidad/', views_hes.conformidad, name='conformidad'),
    path('conformidades/', views_hes.hes_lista, name='hes_lista'),
    path('conformidades/<int:pk>/', views_hes.hes, name='hes'),
    path('conformidades/adjunto/<int:pk>/', views_hes.hes_adjunto, name='hes_adjunto'),
    path('licitaciones/', views_abastecimiento.licitaciones, name='licitaciones'),
    path('licitaciones/nueva/', views_abastecimiento.licitacion_nueva, name='licitacion_nueva'),
    path('licitaciones/<int:pk>/', views_abastecimiento.licitacion, name='licitacion'),
    path('proveedores-por-producto/', views_registro_info.lista, name='registros_info'),
    path('historial-de-precios/', views_registro_info.historial_precios, name='historial_precios'),
    path('proveedores-por-producto/nuevo/', views_registro_info.editar, name='registro_info_nuevo'),
    path('proveedores-por-producto/<int:pk>/', views_registro_info.editar, name='registro_info'),
    path('contratos/', views_abastecimiento.contratos, name='contratos'),
    path('contratos/nuevo/', views_abastecimiento.contrato_nuevo, name='contrato_nuevo'),
    path('contratos/<int:pk>/', views_abastecimiento.contrato, name='contrato'),
    path('contratos/<int:pk>/editar/', views_abastecimiento.contrato_editar, name='contrato_editar'),
    path('importaciones/', views_importacion.importaciones, name='importaciones'),
    path('importaciones/nueva/', views_importacion.importacion_nueva, name='importacion_nueva'),
    path('importaciones/<int:pk>/', views_importacion.importacion, name='importacion'),
    path('importar-xml/', views_xml.importar, name='importar_xml'),
    path('importar-xml/revisar/', views_xml.revisar, name='importar_xml_revisar'),
    path('sire/', views_sire.comparar, name='sire'),
    path('ordenes/', views.oc_lista, name='oc_lista'),
    path('ordenes/nueva/', views.oc_nuevo, name='oc_nuevo'),
    path('ordenes/<int:pk>/', views.oc_detalle, name='oc_detalle'),
    path('ordenes/<int:pk>/editar/', views.oc_editar, name='oc_editar'),
    path('ordenes/<int:pk>/estado/', views.oc_estado, name='oc_estado'),
    path('ordenes/<int:pk>/rechazar/', views.oc_rechazar, name='oc_rechazar'),
    path('ordenes/<int:pk>/enviar/', portal.oc_enviar, name='oc_enviar'),
    # portal de proveedores (lado del ERP)
    path('portal/', portal.tablero, name='portal_tablero'),
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
