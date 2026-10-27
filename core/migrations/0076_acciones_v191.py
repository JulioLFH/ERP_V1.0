"""v1.91: acciones nuevas que separan funciones. Quien ya las tenía juntas las conserva:
- COM-21: «Anular compras y órdenes de compra» se separa en «Anular facturas de compra» y «Anular órdenes de compra
  y cerrar su saldo» (anular_oc).
- SEG-12: el crédito de los contactos pasa a «finanzas.credito» (quien hace la gestión de cobranza).
- TE-01: el alta y la edición de contactos exige «contactos.registrar» (quien tiene el módulo Contactos)."""
from django.db import migrations


def _agregar(acciones, nuevas):
    return sorted(set(acciones or []) | set(nuevas))


def separar(apps, schema_editor):
    from core.modulos import GRUPOS
    base = schema_editor.connection.alias
    PerfilUsuario = apps.get_model('core', 'PerfilUsuario')
    PerfilAcceso = apps.get_model('core', 'PerfilAcceso')
    grupo_contactos = GRUPOS.get('contactos')

    def nuevas(acciones, con_contactos):
        extra = []
        if 'compras.anular' in (acciones or []):
            extra.append('compras.anular_oc')
        if 'finanzas.cobranza' in (acciones or []):
            extra.append('finanzas.credito')
        if con_contactos:
            extra.append('contactos.registrar')
        return extra

    for p in PerfilUsuario.objects.using(base).select_related('usuario'):
        con = bool(grupo_contactos) and p.usuario.groups.filter(name=grupo_contactos).exists()
        extra = nuevas(p.acciones, con)
        if extra and not set(extra) <= set(p.acciones or []):
            p.acciones = _agregar(p.acciones, extra)
            p.save(update_fields=['acciones'])
    for p in PerfilAcceso.objects.using(base):
        extra = nuevas(p.acciones, 'contactos' in (p.modulos or []))
        if extra and not set(extra) <= set(p.acciones or []):
            p.acciones = _agregar(p.acciones, extra)
            p.save(update_fields=['acciones'])


class Migration(migrations.Migration):
    dependencies = [('core', '0075_v191'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [migrations.RunPython(separar, migrations.RunPython.noop)]
