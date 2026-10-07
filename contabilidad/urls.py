from django.urls import path

from . import views, views_cierre, views_libro3, views_presupuesto

app_name = 'contabilidad'

urlpatterns = [
    path('cierre/', views_cierre.cierre_mes, name='cierre'),
    path('conciliacion-niif-tributaria/', views_cierre.conciliacion_normas, name='conciliacion_normas'),
    path('consolidacion/', views_cierre.consolidacion, name='consolidacion'),
    path('conciliacion-migracion/', views_cierre.conciliacion_migracion, name='conciliacion_migracion'),
    path('libro-inventarios-balances/', views_libro3.libro_inventarios, name='libro_inventarios'),
    path('presupuestos/', views_presupuesto.presupuestos, name='presupuestos'),
    path('presupuestos/nuevo/', views_presupuesto.presupuesto_nuevo, name='presupuesto_nuevo'),
    path('presupuestos/<int:pk>/', views_presupuesto.presupuesto_ejecucion, name='presupuesto_ejecucion'),
    path('presupuestos/<int:pk>/editar/', views_presupuesto.presupuesto_editar, name='presupuesto_editar'),
    path('presupuestos/<int:pk>/generar/', views_presupuesto.presupuesto_generar, name='presupuesto_generar'),
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
    path('resultados-por-linea/', views.resultados_por_linea, name='resultados_linea'),
    path('centros-beneficio/', views.CentroBeneficioLista.as_view(), name='beneficios'),
    path('centros-beneficio/nuevo/', views.CentroBeneficioNuevo.as_view(), name='cb_nuevo'),
    path('centros-beneficio/<int:pk>/', views.CentroBeneficioEditar.as_view(), name='cb_editar'),
    path('centros-costo/', views.CentroCostoLista.as_view(), name='centros'),
    path('centros-costo/nuevo/', views.CentroCostoNuevo.as_view(), name='cc_nuevo'),
    path('centros-costo/<int:pk>/', views.CentroCostoEditar.as_view(), name='cc_editar'),
]
