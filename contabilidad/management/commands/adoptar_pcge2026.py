"""Cambia una empresa al Plan Contable General Empresarial 2026.

    python manage.py adoptar_pcge2026 --empresa empresa2              # simulación: muestra qué haría
    python manage.py adoptar_pcge2026 --empresa empresa2 --confirmar

- Crea el catálogo PCGE 2026 completo (contabilidad/pcge2026.py) más las divisionarias del ERP (6399 y 911-931) y
  pone a cada cuenta el nombre oficial del 2026.
- Pasa todo lo que apunta a una cuenta del ERP (configuración contable, productos, categorías de activos, conceptos
  de planilla, tipos de operación, destinos…) a su equivalente del 2026 (pcge.MAPA_2026). Las categorías de activos
  toman la depreciación y amortización acumulada y el gasto de su clase de activo (pcge.DEPRECIACION_2026).
- Borra las cuentas del ERP que no existen en el 2026 y quedaron sin uso, y marca la empresa con el PCGE 2026.
- Los asientos automáticos se borran y se regeneran al centralizar. No se puede usar si la empresa ya tiene asientos
  manuales, de apertura o del sistema anterior, o periodos cerrados: esos asientos usan las cuentas actuales.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Case, ProtectedError, Value, When

from contabilidad import pcge
from erp.empresas import activar, empresas, restaurar


class Simulacion(Exception):
    pass


class Command(BaseCommand):
    help = 'Adopta el PCGE 2026 en una empresa (cuentas, configuración y referencias)'

    def add_arguments(self, parser):
        parser.add_argument('--empresa', default='default', help='Alias de la empresa (base de datos)')
        parser.add_argument('--confirmar', action='store_true', help='Graba los cambios (sin esto solo simula)')

    def handle(self, *args, **opts):
        alias = opts['empresa']
        if alias not in empresas():
            raise CommandError(f'No existe la empresa {alias!r}. Empresas: {", ".join(empresas())}')
        token = activar(alias)
        try:
            with transaction.atomic(using=alias):
                resumen = adoptar()
                for linea in resumen:
                    self.stdout.write(f'  {linea}')
                if not opts['confirmar']:
                    raise Simulacion
        except Simulacion:
            self.stdout.write(self.style.WARNING('Simulación: no se grabó nada. Use --confirmar para aplicarlo.'))
            return
        finally:
            restaurar(token)
        self.stdout.write(self.style.SUCCESS(f'Empresa {alias}: PCGE 2026 adoptado.'))


def _referencias(CuentaContable):
    """[(modelo, campo)] de todas las llaves foráneas hacia el plan de cuentas (también las de related_name='+')."""
    from django.apps import apps
    return [(modelo, f.name) for modelo in apps.get_models() if not modelo._meta.proxy and modelo._meta.managed
            for f in modelo._meta.concrete_fields if f.is_relation and f.related_model is CuentaContable]


def adoptar():
    from core.models import Empresa

    from contabilidad.models import ORIGENES_FIJOS, Asiento, AsientoLinea, CuentaContable, PeriodoContable

    empresa = Empresa.actual()
    if empresa.plan_contable == pcge.PCGE2026:
        raise CommandError('La empresa ya usa el PCGE 2026.')
    if Asiento.objects.filter(origen__in=ORIGENES_FIJOS).exists():
        raise CommandError('La empresa tiene asientos manuales, de apertura o del sistema anterior: usan las cuentas '
                           'actuales y no se pueden traducir automáticamente.')
    if PeriodoContable.objects.filter(cerrado=True).exists():
        raise CommandError('La empresa tiene periodos contables cerrados.')
    resumen = []
    try:
        borrados = Asiento.objects.all().delete()[1].get(Asiento._meta.label, 0)
    except ProtectedError as e:
        raise CommandError(f'No se pueden borrar los asientos automáticos: {e}')
    resumen.append(f'Asientos automáticos borrados (se regeneran al centralizar): {borrados}')

    # 1. catálogo 2026 + divisionarias del ERP, con su nombre y naturaleza oficiales
    lista = pcge.catalogo(pcge.PCGE2026)
    oficiales = dict(lista)
    cuentas = {c.codigo: c for c in CuentaContable.objects.all()}
    nuevas = [CuentaContable(codigo=cod, nombre=nom[:200], naturaleza=pcge.naturaleza(cod))
              for cod, nom in lista if cod not in cuentas]
    CuentaContable.objects.bulk_create(nuevas)
    renombradas = 0
    for cod, c in cuentas.items():
        if cod in oficiales and (c.nombre != oficiales[cod][:200] or c.naturaleza != pcge.naturaleza(cod)):
            c.nombre, c.naturaleza = oficiales[cod][:200], pcge.naturaleza(cod)
            c.save(update_fields=['nombre', 'naturaleza'])
            renombradas += 1
    cuentas = {c.codigo: c for c in CuentaContable.objects.all()}
    resumen.append(f'Cuentas creadas: {len(nuevas)}; con el nombre del PCGE 2026: {renombradas}')

    # 2. categorías de activos: depreciación / amortización acumulada y gasto según la clase del activo
    from activos.models import CategoriaActivo
    genericas = {'3913', '3921', '6814', '6821'}
    for cat in CategoriaActivo.objects.select_related('cuenta_activo', 'cuenta_depreciacion', 'cuenta_gasto'):
        activo = pcge.MAPA_2026.get(cat.cuenta_activo.codigo, cat.cuenta_activo.codigo) if cat.cuenta_activo_id else ''
        par = next((v for k, v in pcge.DEPRECIACION_2026.items() if activo.startswith(k)), None)
        if not par:
            continue
        campos = []
        if cat.cuenta_depreciacion_id and cat.cuenta_depreciacion.codigo in genericas:
            cat.cuenta_depreciacion = cuentas[par[0]]
            campos.append('cuenta_depreciacion')
        if cat.cuenta_gasto_id and cat.cuenta_gasto.codigo in genericas:
            cat.cuenta_gasto = cuentas[par[1]]
            campos.append('cuenta_gasto')
        if campos:
            cat.save(update_fields=campos)

    # 3. todo lo que apunta a una cuenta del ERP pasa a su equivalente del 2026 (en un solo UPDATE por campo, así una
    #    cadena como 1231 -> 1232 -> 1234 no se encadena)
    pares = {cuentas[viejo].pk: cuentas[nuevo].pk for viejo, nuevo in pcge.MAPA_2026.items() if viejo in cuentas}
    movidas = 0
    for modelo, campo in _referencias(CuentaContable):
        qs = modelo._base_manager.filter(**{f'{campo}__in': list(pares)})
        if qs.exists():
            tipo = modelo._meta.get_field(campo).target_field
            movidas += qs.update(**{campo: Case(*[When(**{campo: v}, then=Value(n)) for v, n in pares.items()],
                                                output_field=tipo)})
    resumen.append(f'Referencias pasadas a la cuenta equivalente del 2026: {movidas}')

    # 3b. subcuentas propias de una cuenta que cambió de código (ej. caja 10111 bajo la 1011): pasan a colgar de la
    #     cuenta del 2026 (1011 bajo la 101) si el código nuevo está libre; la cuenta vieja ya no tiene uso
    recodificadas = 0
    for viejo, nuevo in pcge.MAPA_2026.items():
        hijas = [c for cod, c in cuentas.items() if cod != viejo and cod.startswith(viejo) and cod not in oficiales]
        if viejo not in cuentas or not hijas:
            continue
        destino = {c: nuevo + c.codigo[len(viejo):] for c in hijas}
        libres = set(cuentas) - {viejo} - {c.codigo for c in hijas}
        if any(cod in libres or len(cod) > 12 for cod in destino.values()):
            continue
        CuentaContable.objects.filter(pk=cuentas.pop(viejo).pk).delete()
        for c, cod in destino.items():
            cuentas.pop(c.codigo)
            c.codigo = cod
            c.save(update_fields=['codigo'])
            cuentas[cod] = c
            recodificadas += 1
    if recodificadas:
        resumen.append(f'Subcuentas propias que pasan a la cuenta del 2026: {recodificadas}')

    # 4. cuentas del ERP que no existen en el 2026 y quedaron sin uso (de la más larga a la más corta; se repite
    #    porque al borrar una cuenta su destino puede quedar libre)
    codigos = set(cuentas)
    eliminadas = []
    while True:
        usadas = set()
        for modelo, campo in _referencias(CuentaContable):
            usadas |= set(modelo._base_manager.exclude(**{campo: None}).values_list(f'{campo}_id', flat=True))
        borrar = [cod for cod in sorted(codigos - set(oficiales), key=lambda c: (-len(c), c))
                  if cuentas[cod].pk not in usadas and not any(o != cod and o.startswith(cod) for o in codigos)]
        if not borrar:
            break
        for cod in borrar:
            if not any(o != cod and o.startswith(cod) for o in codigos):
                CuentaContable.objects.filter(pk=cuentas[cod].pk).delete()
                codigos.discard(cod)
                eliminadas.append(cod)
    conservadas = sorted(codigos - set(oficiales))
    resumen.append(f'Cuentas del ERP eliminadas (no existen en el 2026): {len(eliminadas)}')
    if conservadas:
        resumen.append(f'Cuentas propias que se conservan (en uso o con subcuentas): {", ".join(conservadas)}')

    # 5. la empresa pasa al PCGE 2026; imputables = sin subcuentas (las de la configuración contable siguen
    #    aceptando movimientos aunque tengan subcuentas de caja o bancos)
    Empresa.objects.filter(pk=empresa.pk).update(plan_contable=pcge.PCGE2026)
    from contabilidad.models import CuentaDefecto
    de_configuracion = set(CuentaDefecto.objects.values_list('cuenta_id', flat=True))
    cuentas = {c.codigo: c for c in CuentaContable.objects.all()}
    for cod, c in cuentas.items():
        imputable = c.pk in de_configuracion or not any(o != cod and o.startswith(cod) for o in cuentas)
        if c.imputable != imputable:
            c.imputable = imputable
            c.save(update_fields=['imputable'])
    con_destino = 0
    for cod, c in cuentas.items():
        debe, haber = pcge.destinos(cod, pcge.PCGE2026)
        if debe and c.imputable and not c.destino_debe_id:
            c.destino_debe, c.destino_haber = cuentas[debe], cuentas[haber]
            c.save(update_fields=['destino_debe', 'destino_haber'])
            con_destino += 1
    pcge.cargar(CuentaContable, CuentaDefecto)  # cuentas por defecto que falten
    resumen.append(f'Cuentas con destino nuevo: {con_destino}. Total de cuentas: {len(cuentas)}')

    # 6. verificación: lo que el ERP usa para registrar debe aceptar movimientos
    malas = []
    for modelo, campo in _referencias(CuentaContable):
        if modelo is CuentaContable or modelo is AsientoLinea:
            continue
        for cod in (modelo._base_manager.filter(**{f'{campo}__imputable': False})
                    .values_list(f'{campo}__codigo', flat=True).distinct()):
            malas.append(f'{modelo._meta.verbose_name} ({campo}): {cod}')
    if malas:
        raise CommandError('Quedarían referencias a cuentas que no aceptan movimientos:\n  ' + '\n  '.join(malas))
    PeriodoContable.objects.filter(cerrado=False).update(pendiente=True)
    return resumen
