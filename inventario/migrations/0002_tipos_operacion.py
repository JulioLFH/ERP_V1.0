"""Tipos de operación de inventario iniciales (se pueden editar en Inventario > Configuración)."""
from django.db import migrations

# (código, nombre, clase, documento de origen, pide costo, cuenta contrapartida, SUNAT, SUNAT ingreso, icono)
TIPOS = [
    ('SALDO_INI', 'Saldo inicial', 'INGRESO', '', True, '5911', '16', '', 'bi-flag'),
    ('REC_COMPRA', 'Recepción de compras', 'INGRESO', 'ORDEN_COMPRA', False, '2811', '02', '', 'bi-box-arrow-in-down'),
    ('DEV_CLI', 'Devolución de clientes', 'INGRESO', 'VENTA', False, '69111', '05', '', 'bi-arrow-counterclockwise'),
    ('AJ_ING', 'Ajuste ingreso', 'INGRESO', '', True, '7599', '28', '', 'bi-plus-square'),
    ('SAL_VENTA', 'Salida por ventas', 'SALIDA', 'VENTA', False, '69111', '01', '', 'bi-cart-check'),
    ('DEV_PROV', 'Devolución a proveedor', 'SALIDA', 'COMPRA', False, '2811', '06', '', 'bi-arrow-return-left'),
    ('AJ_SAL', 'Ajuste salida', 'SALIDA', '', False, '6599', '28', '', 'bi-dash-square'),
    ('CONS_INT', 'Consumo interno', 'SALIDA', '', False, '6561', '99', '', 'bi-cup-hot'),
    ('CONS_MANT', 'Consumo mantenimiento', 'SALIDA', '', False, '6343', '99', '', 'bi-tools'),
    ('SAL_DESTR', 'Salida a destrucción', 'SALIDA', '', False, '6599', '15', '', 'bi-trash3'),
    ('TRAS_DESTR', 'Traslado a destrucción', 'TRASLADO', '', False, None, '11', '21', 'bi-sign-stop'),
    ('TRAS_TRANS', 'Traslado a tránsito', 'TRANSITO_ENVIO', '', False, None, '11', '21', 'bi-truck'),
    ('REC_TRANS', 'Recepción de tránsito', 'TRANSITO_RECEPCION', 'TRANSITO', False, None, '11', '21',
     'bi-box-seam'),
    ('MANUF', 'Manufactura', 'MANUFACTURA', '', False, None, '10', '19', 'bi-gear-wide-connected'),
]


def crear_tipos(apps, schema_editor):
    from contabilidad import pcge
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    pcge.cargar(CuentaContable, apps.get_model('contabilidad', 'CuentaDefecto'))
    Tipo = apps.get_model('inventario', 'TipoOperacion')
    cuentas = {c.codigo: c for c in CuentaContable.objects.all()}
    for orden, (codigo, nombre, clase, origen, costo, cuenta, sunat, sunat_ing, icono) in enumerate(TIPOS, 1):
        Tipo.objects.get_or_create(codigo=codigo, defaults={
            'nombre': nombre, 'clase': clase, 'origen': origen, 'requiere_costo': costo,
            'cuenta_contable': cuentas.get(cuenta) if cuenta else None, 'codigo_sunat': sunat,
            'codigo_sunat_ingreso': sunat_ing, 'icono': icono, 'orden': orden * 10})


class Migration(migrations.Migration):
    dependencies = [
        ('inventario', '0001_initial'),
        ('contabilidad', '0004_datos_v14'),
    ]
    operations = [migrations.RunPython(crear_tipos, migrations.RunPython.noop)]
