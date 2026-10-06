"""v1.20: conceptos de la liquidación de beneficios sociales (códigos PLAME referenciales: revíselos en
Planillas › Configuración)."""
from django.db import migrations

CONCEPTOS = [
    # clave, nombre, tipo, código PLAME (tabla 22), cuenta
    ('VAC_TRUNCAS', 'Vacaciones truncas', 'INGRESO', '0114', '6215'),
    ('GRATIF_TRUNCA', 'Gratificación trunca (Ley 29351)', 'INGRESO', '0407', '6214'),
    ('BONIF_TRUNCA', 'Bonificación extraordinaria proporcional (Ley 30334)', 'INGRESO', '0312', '6214'),
    ('INDEMN_VACACIONAL', 'Indemnización vacacional (vacaciones vencidas)', 'INGRESO', '', '6215'),
    ('INDEMNIZACION', 'Indemnización por despido arbitrario', 'INGRESO', '', '6293'),
]


def cargar(apps, schema_editor):
    Concepto = apps.get_model('planillas', 'ConceptoPlanilla')
    Cuenta = apps.get_model('contabilidad', 'CuentaContable')
    # los descuentos y aportes se muestran al final de la boleta: los nuevos ingresos van antes de ONP
    base = Concepto.objects.filter(clave='CTS').values_list('orden', flat=True).first() or 10
    Concepto.objects.filter(orden__gt=base).update(orden=models_F('orden', len(CONCEPTOS)))
    for n, (clave, nombre, tipo, plame, cuenta) in enumerate(CONCEPTOS, 1):
        Concepto.objects.update_or_create(clave=clave, defaults={
            'nombre': nombre, 'tipo': tipo, 'codigo_plame': plame, 'orden': base + n,
            'cuenta': Cuenta.objects.filter(codigo=cuenta).first()})


def models_F(campo, suma):
    from django.db.models import F
    return F(campo) + suma


class Migration(migrations.Migration):
    dependencies = [
        ('planillas', '0004_filaplanilla_despido_arbitrario_and_more'),
        ('contabilidad', '0014_cuentas_letras_rendiciones'),
    ]
    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]
