from django.urls import path

from . import views, views_extractos

app_name = 'finanzas'

urlpatterns = [
    path('extractos/', views_extractos.extractos, name='extractos'),
    path('extractos/<int:pk>/', views_extractos.extracto, name='extracto'),
    path('extractos/<int:pk>/accion/', views_extractos.extracto_accion, name='extracto_accion'),
    path('cuentas/', views.cuentas, name='cuentas'),
    path('cuentas/nueva/', views.CuentaNueva.as_view(), name='cuenta_nueva'),
    path('cuentas/<int:pk>/', views.CuentaEditar.as_view(), name='cuenta_editar'),
    path('cuentas/<int:pk>/saldo-inicial/', views.saldo_inicial, name='saldo_inicial'),
    path('saldo-inicial/sustento/<int:pk>/', views.saldo_inicial_sustento, name='saldo_inicial_sustento'),
    path('movimientos/', views.movimientos, name='movimientos'),
    path('movimientos/nuevo/', views.movimiento_nuevo, name='movimiento_nuevo'),
    path('movimientos/<int:pk>/eliminar/', views.movimiento_eliminar, name='movimiento_eliminar'),
    path('movimientos/<int:pk>/voucher/', views.voucher, name='voucher'),
    path('cobranzas/', views.cobrar_pagar, {'modo': 'cobranza'}, name='cobranza'),
    path('pagos/', views.cobrar_pagar, {'modo': 'pago'}, name='pago'),
    path('transferencia/', views.transferencia, name='transferencia'),
    path('conciliacion/', views.conciliacion, name='conciliacion'),
    path('importar/', views.importar_extracto, name='importar'),
    path('flujo/', views.flujo_caja, name='flujo'),
]
