"""v1.83: módulos nuevos y separados. Quien tenía el módulo de origen recibe el nuevo (no pierde nada), los permisos que
cambiaron de módulo pasan al nuevo y la empresa que ocultaba el módulo de origen también oculta los que salen de él.
Conversaciones, Calendario, Actividades pendientes y Consulta RUC no se asignan: los tiene todo usuario interno."""
from django.db import migrations

# módulo de origen (nombre del grupo, clave) -> módulos nuevos (nombre del grupo, clave)
ORIGEN = {
    ('Ventas', 'ventas'): [('TomaPedido', 'toma_pedido')],
    ('Compras', 'compras'): [('Portal de proveedores', 'portal_proveedores')],
    ('Inventario', 'inventario'): [('Código de barras', 'codigo_barras')],
    ('Contabilidad', 'contabilidad'): [('Presupuestos', 'presupuestos'), ('SUNAT', 'sunat')],
    ('Obras', 'obras'): [('Proyectos', 'proyectos')],
    ('Planillas', 'planillas'): [('Empleados', 'empleados'), ('Tiempo personal', 'tiempo_personal')],
    ('Logística', 'logistica'): [('Distribución', 'distribucion'), ('Flota', 'flota')],
}
MOVIDAS = {'compras.portal': 'portal_proveedores.aprobar', 'contabilidad.presupuesto': 'presupuestos.elaborar',
           'logistica.flota': 'flota.registrar', 'logistica.aprobar_lecturas': 'flota.aprobar_lecturas',
           'planillas.aprobar_movimientos': 'empleados.aprobar_movimientos'}
AGREGADAS = {'planillas.calcular': ['empleados.registrar', 'tiempo_personal.registrar'],
             'planillas.configurar': ['empleados.configurar'],
             'ventas.emitir': ['toma_pedido.registrar'],
             'inventario.operar': ['codigo_barras.asignar'],
             'logistica.emitir': ['distribucion.programar', 'distribucion.entregar'],
             'obras.presupuestar': ['proyectos.gestionar']}


def _acciones(lista):
    nuevas = set(lista or [])
    for antigua, nueva in MOVIDAS.items():
        if antigua in nuevas:
            nuevas.discard(antigua)
            nuevas.add(nueva)
    for base, extra in AGREGADAS.items():
        if base in nuevas:
            nuevas.update(extra)
    return sorted(nuevas)


def separar(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    for (nombre_origen, _), nuevos in ORIGEN.items():
        origen = Group.objects.using(alias).filter(name=nombre_origen).first()
        for nombre, _ in nuevos:
            g, _ = Group.objects.using(alias).get_or_create(name=nombre)
            if origen is not None:
                g.user_set.add(*origen.user_set.all())
    por_clave = {clave: [c for _, c in nuevos] for (_, clave), nuevos in ORIGEN.items()}
    for modelo in ('PerfilUsuario', 'PerfilAcceso'):
        for perfil in apps.get_model('core', modelo).objects.using(alias).all():
            campos = []
            acciones = _acciones(perfil.acciones)
            if acciones != sorted(perfil.acciones or []):
                perfil.acciones = acciones
                campos.append('acciones')
            modulos = getattr(perfil, 'modulos', None)
            if modulos is not None:
                faltan = [n for m in modulos for n in por_clave.get(m, []) if n not in modulos]
                if faltan:
                    perfil.modulos = list(modulos) + list(dict.fromkeys(faltan))
                    campos.append('modulos')
            if campos:
                perfil.save(update_fields=campos)
    for empresa in apps.get_model('core', 'Empresa').objects.using(alias).all():
        ocultos = list(empresa.modulos_ocultos or [])
        faltan = [n for m in ocultos for n in por_clave.get(m, []) if n not in ocultos]
        if faltan:
            empresa.modulos_ocultos = ocultos + list(dict.fromkeys(faltan))
            empresa.save(update_fields=['modulos_ocultos'])


class Migration(migrations.Migration):
    # después de las migraciones que dieron los permisos que aquí cambian de módulo
    dependencies = [('core', '0065_consultaruc'), ('auth', '0012_alter_user_first_name_max_length'),
                    ('planillas', '0010_permiso_aprobar_movimientos'), ('ventas', '0020_permisos_crm'),
                    ('obras', '0007_permiso_ejecutar'), ('produccion', '0005_permisos_calidad_mantenimiento')]
    operations = [migrations.RunPython(separar, migrations.RunPython.noop)]
