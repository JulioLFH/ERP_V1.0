"""Contabilidad automática.

- Cualquier cambio en compras, ventas, caja/bancos o almacén marca su periodo (y los siguientes, por los
  saldos acumulados y la diferencia de cambio) como "pendiente de centralizar".
- Al abrir cualquier pantalla de Contabilidad se centralizan los periodos pendientes que estén abiertos, así
  los libros siempre coinciden con los auxiliares sin depender de un proceso manual.
- Cada caja o banco tiene su propia subcuenta contable (ej. 10411 BCP soles, 10412 BCP dólares).
- El asiento de apertura toma los saldos iniciales de caja y bancos.
"""
import logging
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Asiento, AsientoLinea, CuentaContable, CuentaDefecto, PeriodoContable

log = logging.getLogger(__name__)
D0 = Decimal('0')


def marcar_pendiente(periodo, posteriores=True):
    if not periodo or len(periodo) != 6:
        return
    PeriodoContable.objects.get_or_create(periodo=periodo, defaults={'pendiente': True})
    qs = PeriodoContable.objects.filter(periodo__gte=periodo) if posteriores else \
        PeriodoContable.objects.filter(periodo=periodo)
    qs.filter(pendiente=False, cerrado=False).update(pendiente=True)


def periodos_pendientes():
    return list(PeriodoContable.objects.filter(pendiente=True, cerrado=False).order_by('periodo')
                .values_list('periodo', flat=True))


def actualizar_pendientes():
    """Centraliza en orden los periodos abiertos con cambios. Devuelve los errores encontrados."""
    from .centralizar import ErrorContable, centralizar_periodo
    errores = []
    if not Asiento.objects.filter(origen='APERTURA').exists():
        generar_apertura()
    for periodo in periodos_pendientes():
        try:
            resumen = centralizar_periodo(periodo)
            errores += resumen['errores']
        except ErrorContable as exc:
            errores.append(str(exc))
    return errores


# ---------------------------------------------------------------- subcuentas de caja y bancos
def asegurar_subcuenta(cuenta_fin):
    """Crea y asigna una subcuenta contable propia para la caja o banco (si no tiene)."""
    if cuenta_fin.cuenta_contable_id:
        return cuenta_fin.cuenta_contable
    if cuenta_fin.tipo == 'CAJA':
        padre = '1011'
    elif cuenta_fin.es_detracciones:
        padre = '1042'
    else:
        padre = '1041'
    usados = set(CuentaContable.objects.filter(codigo__startswith=padre).values_list('codigo', flat=True))
    n = 1
    while f'{padre}{n}' in usados:
        n += 1
    nombre = f'{cuenta_fin.nombre} ({cuenta_fin.moneda})'[:200]
    sub = CuentaContable.objects.create(codigo=f'{padre}{n}', nombre=nombre, naturaleza='DEUDORA', imputable=True)
    type(cuenta_fin).objects.filter(pk=cuenta_fin.pk).update(cuenta_contable=sub)
    cuenta_fin.cuenta_contable = sub
    return sub


# ---------------------------------------------------------------- asiento de apertura
def fecha_inicio():
    """Primer día del primer mes con movimientos en el sistema."""
    from compras.models import Compra
    from core.models import Kardex
    from finanzas.models import Movimiento
    from ventas.models import Venta
    fechas = [qs.order_by(campo).values_list(campo, flat=True).first() for qs, campo in (
        (Compra.objects.all(), 'fecha_emision'), (Venta.objects.all(), 'fecha_emision'),
        (Movimiento.objects.all(), 'fecha'), (Kardex.objects.all(), 'fecha'))]
    fechas = [f for f in fechas if f]
    inicio = min(fechas) if fechas else date.today()
    return inicio.replace(day=1)


def generar_apertura():
    """Asiento de apertura con los saldos iniciales de caja y bancos (se regenera si cambian)."""
    from core.tipo_cambio import venta_del_dia
    from finanzas.models import Cuenta
    Asiento.objects.filter(origen='APERTURA').delete()
    cuentas = [c for c in Cuenta.objects.all() if c.saldo_inicial]
    if not cuentas:
        return None
    fecha = fecha_inicio()
    contra = CuentaDefecto.mapa()['apertura_patrimonio']
    a = Asiento(fecha=fecha, libro='05', origen='APERTURA',
                glosa='Asiento de apertura: saldos iniciales de caja y bancos')
    lineas, total = [], D0
    for c in cuentas:
        tc = venta_del_dia(fecha) if c.moneda == 'USD' else Decimal('1')
        importe = (c.saldo_inicial * tc).quantize(Decimal('0.01'))
        linea = AsientoLinea(cuenta=asegurar_subcuenta(c), glosa=f'Saldo inicial {c}',
                             debe=max(importe, D0), haber=max(-importe, D0))
        if c.moneda == 'USD':
            linea.debe_me, linea.haber_me = max(c.saldo_inicial, D0), max(-c.saldo_inicial, D0)
        lineas.append(linea)
        total += importe
    lineas.append(AsientoLinea(cuenta=contra, glosa='Patrimonio inicial', debe=max(-total, D0), haber=max(total, D0)))
    with transaction.atomic():
        a.save()
        for linea in lineas:
            linea.asiento = a
        AsientoLinea.objects.bulk_create(lineas)
    PeriodoContable.objects.filter(periodo__gte=fecha.strftime('%Y%m'), cerrado=False).update(pendiente=True)
    return a


# ---------------------------------------------------------------- señales
def _conectar():
    from compras.models import Compra
    from core.models import Kardex
    from finanzas.models import Cuenta, Movimiento
    from ventas.models import Venta

    @receiver([post_save, post_delete], sender=Compra, weak=False)
    @receiver([post_save, post_delete], sender=Venta, weak=False)
    def comprobante_cambio(sender, instance, **kwargs):
        marcar_pendiente(instance.periodo)

    @receiver([post_save, post_delete], sender=Movimiento, weak=False)
    def movimiento_cambio(sender, instance, **kwargs):
        marcar_pendiente(instance.fecha.strftime('%Y%m'))

    @receiver([post_save, post_delete], sender=Kardex, weak=False)
    def kardex_cambio(sender, instance, **kwargs):
        marcar_pendiente(instance.fecha.strftime('%Y%m'))

    @receiver(post_save, sender=Cuenta, weak=False)
    def cuenta_cambio(sender, instance, created, **kwargs):
        if not instance.cuenta_contable_id:
            asegurar_subcuenta(instance)
        # los saldos iniciales cambian la apertura y todos los periodos
        Asiento.objects.filter(origen='APERTURA').delete()
        PeriodoContable.objects.filter(cerrado=False).update(pendiente=True)
