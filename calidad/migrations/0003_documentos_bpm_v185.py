"""CAL-12: un documento BPM que quedó vigente con la validación genérica (sin revisión ni aprobación con firma) vuelve
a elaboración para seguir el flujo elaboró → revisó → aprobó."""
from django.db import migrations


def reabrir(apps, schema_editor):
    Documento = apps.get_model('calidad', 'DocumentoControlado')
    for d in Documento.objects.filter(norma='BPM_DIGEMID', estado_validacion__in=['POR_VALIDAR', 'VALIDADO'],
                                      aprobado_por__isnull=True):
        d.estado_validacion = 'BORRADOR'
        d.observacion = ('Documento BPM: vuelve a elaboración para su revisión y aprobación con firma electrónica '
                         f'(antes «{d.validador_nombre or "validado"}» sin firmas, v1.85.0).')[:250]
        d.save(update_fields=['estado_validacion', 'observacion'])


class Migration(migrations.Migration):
    dependencies = [('calidad', '0002_calidad_farma_v184')]
    operations = [migrations.RunPython(reabrir, migrations.RunPython.noop)]
