"""Datos de la v1.4: importes en soles, subcuentas por caja/banco, conceptos de ajustes y recentralización."""
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

D0 = Decimal('0')


def r2(v):
    return Decimal(v or 0).quantize(Decimal('0.01'), ROUND_HALF_UP)


def importes_soles(apps, schema_editor):
    for app, modelo in (('compras', 'Compra'), ('ventas', 'Venta')):
        for d in apps.get_model(app, modelo).objects.all():
            tc = d.tipo_cambio if d.moneda == 'USD' else Decimal('1')
            d.total_pen, d.igv_pen = r2(d.total * tc), r2(d.igv * tc)
            d.icbper_pen, d.nograv_pen = r2(d.icbper * tc), r2(d.no_gravado * tc)
            d.base_pen = d.total_pen - d.igv_pen - d.icbper_pen - d.nograv_pen
            d.ret_pen, d.perc_pen = r2(d.retencion_monto * tc), r2(d.percepcion_monto * tc)
            d.detr_pen = r2(d.detraccion_monto * tc)
            d.save(update_fields=['total_pen', 'igv_pen', 'icbper_pen', 'nograv_pen', 'base_pen', 'ret_pen',
                                  'perc_pen', 'detr_pen'])
    for m in apps.get_model('finanzas', 'Movimiento').objects.select_related('venta', 'compra'):
        doc = m.venta or m.compra
        tc = doc.tipo_cambio if doc and doc.moneda == 'USD' else Decimal('1')
        m.monto_doc_pen = r2(m.monto * tc) if doc else D0
        m.save(update_fields=['monto_doc_pen'])


def cuentas_y_subcuentas(apps, schema_editor):
    from contabilidad import pcge
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    pcge.cargar(CuentaContable, apps.get_model('contabilidad', 'CuentaDefecto'))
    # la compra de mercadería queda "por recibir" (2811) hasta que el kardex registra el ingreso
    c6011 = CuentaContable.objects.filter(codigo='6011').first()
    c2811 = CuentaContable.objects.filter(codigo='2811').first()
    if c6011 and c2811 and (c6011.destino_debe_id is None or c6011.destino_debe.codigo == '20111'):
        c6011.destino_debe = c2811
        c6011.save(update_fields=['destino_debe'])
    for cuenta in apps.get_model('finanzas', 'Cuenta').objects.filter(cuenta_contable__isnull=True):
        padre = '1011' if cuenta.tipo == 'CAJA' else ('1042' if cuenta.es_detracciones else '1041')
        usados = set(CuentaContable.objects.filter(codigo__startswith=padre).values_list('codigo', flat=True))
        n = 1
        while f'{padre}{n}' in usados:
            n += 1
        sub = CuentaContable.objects.create(codigo=f'{padre}{n}', nombre=f'{cuenta.nombre} ({cuenta.moneda})'[:200],
                                            naturaleza='DEUDORA', imputable=True)
        cuenta.cuenta_contable = sub
        cuenta.save(update_fields=['cuenta_contable'])


def conceptos_ajustes(apps, schema_editor):
    for k in apps.get_model('core', 'Kardex').objects.filter(origen='AJUSTE', concepto=''):
        if 'inicial' in k.referencia.lower():
            k.concepto = 'INICIAL'
        else:
            k.concepto = 'SOBRANTE' if k.tipo == 'ENTRADA' else 'MERMA'
        k.save(update_fields=['concepto'])


def recentralizar(apps, schema_editor):
    """Todos los periodos con datos quedan pendientes: se recentralizan con las nuevas reglas."""
    Periodo = apps.get_model('contabilidad', 'PeriodoContable')
    periodos = set()
    for app, modelo in (('compras', 'Compra'), ('ventas', 'Venta')):
        periodos |= set(apps.get_model(app, modelo).objects.values_list('periodo', flat=True))
    for app, modelo in (('finanzas', 'Movimiento'), ('core', 'Kardex')):
        periodos |= {f.strftime('%Y%m') for f in apps.get_model(app, modelo).objects.dates('fecha', 'month')}
    for p in filter(None, periodos):
        Periodo.objects.get_or_create(periodo=p)
    Periodo.objects.filter(cerrado=False).update(pendiente=True)


class Migration(migrations.Migration):
    dependencies = [
        ('contabilidad', '0003_periodocontable_pendiente_alter_asiento_origen'),
        ('compras', '0004_compra_anulado_en_compra_anulado_por_compra_base_pen_and_more'),
        ('ventas', '0004_venta_anulado_en_venta_anulado_por_venta_base_pen_and_more'),
        ('finanzas', '0003_cuenta_permite_sobregiro_movimiento_monto_doc_pen'),
        ('core', '0006_empresa_permitir_stock_negativo_kardex_concepto'),
    ]
    operations = [
        migrations.RunPython(importes_soles, migrations.RunPython.noop),
        migrations.RunPython(cuentas_y_subcuentas, migrations.RunPython.noop),
        migrations.RunPython(conceptos_ajustes, migrations.RunPython.noop),
        migrations.RunPython(recentralizar, migrations.RunPython.noop),
    ]
