from django.urls import path

from . import views

app_name = 'contabilidad'

urlpatterns = [
    path('periodos/', views.periodos, name='periodos'),
    path('asientos/', views.asientos, name='asientos'),
    path('asientos/nuevo/', views.asiento_nuevo, name='asiento_nuevo'),
    path('asientos/<int:pk>/', views.asiento_detalle, name='asiento_detalle'),
    path('asientos/<int:pk>/editar/', views.asiento_editar, name='asiento_editar'),
    path('asientos/<int:pk>/eliminar/', views.asiento_eliminar, name='asiento_eliminar'),
    path('asientos/<int:pk>/extornar/', views.asiento_extornar, name='asiento_extornar'),
    path('libro-diario/', views.libro_diario, name='diario'),
    path('libro-mayor/', views.libro_mayor, name='mayor'),
    path('balance-comprobacion/', views.balance, name='balance'),
    path('situacion-financiera/', views.estado_situacion, name='situacion'),
    path('estado-resultados/', views.estado_resultados, name='resultados'),
    path('centros-costo/reporte/', views.centros_costo_reporte, name='centros_reporte'),
    path('plan-cuentas/', views.plan_cuentas, name='plan'),
    path('plan-cuentas/nueva/', views.CuentaNueva.as_view(), name='cuenta_nueva'),
    path('plan-cuentas/<int:pk>/', views.CuentaEditar.as_view(), name='cuenta_editar'),
    path('configuracion/', views.configuracion, name='configuracion'),
    path('centros-costo/', views.CentroCostoLista.as_view(), name='centros'),
    path('centros-costo/nuevo/', views.CentroCostoNuevo.as_view(), name='cc_nuevo'),
    path('centros-costo/<int:pk>/', views.CentroCostoEditar.as_view(), name='cc_editar'),
]
