from django.urls import path

from . import views, views_extractos, views_tesoreria as vt

app_name = 'finanzas'

urlpatterns = [
    path('anticipos/', vt.anticipos, name='anticipos'),
    path('anticipos/<int:pk>/aplicar/', vt.anticipo_aplicar, name='anticipo_aplicar'),
    path('aplicaciones/<int:pk>/anular/', vt.aplicacion_anular, name='aplicacion_anular'),
    path('letras/', vt.letras, name='letras'),
    path('letras/<int:pk>/', vt.letra, name='letra'),
    path('letras/canje/', vt.canje_nuevo, name='canje_nuevo'),
    path('letras/canje/<int:pk>/', vt.canje, name='canje'),
    path('cheques/', vt.cheques, name='cheques'),
    path('cheques/<int:pk>/estado/', vt.cheque_estado, name='cheque_estado'),
    path('entregas/', vt.entregas, name='entregas'),
    path('entregas/nueva/', vt.entrega_nueva, name='entrega_nueva'),
    path('entregas/<int:pk>/', vt.entrega, name='entrega'),
    path('pagos-masivos/', vt.pagos_masivos, name='pagos_masivos'),
    path('pagos-masivos/nuevo/', vt.pago_masivo_nuevo, name='pago_masivo_nuevo'),
    path('pagos-masivos/<int:pk>/', vt.pago_masivo, name='pago_masivo'),
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
