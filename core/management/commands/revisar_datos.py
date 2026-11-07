"""Lista los datos que hoy no pasarían las validaciones del sistema (auditoría v1.102: MAN-05, COM-04, SEG-02, DAT-01,
DAT-02). Sin opciones solo lee. Con --corregir (v1.106) arregla lo que no necesita una decisión, con registro en la
auditoría: tarifas por hora negativas a 0 (MAN-05), el código HTML fuera del nombre del almacén (SEG-02) y la fecha y el
motivo de las órdenes anuladas sin ellos (DAT-02). Lo demás (planillas, sueldos, DNI, áreas, kardex y periodos futuros)
lo depura la empresa en sus pantallas o lo documenta como caso de prueba negativo.

DAT-01: lo migrado del sistema anterior (es_historico) NO se corrige: es la copia de lo que se registró allí y se
conserva tal cual para consulta; no mueve stock, no genera asientos ni va al registro / PLE, así que no afecta saldos.
La lista sirve para explicar las diferencias en los reportes históricos.

  manage.py revisar_datos --settings=erp.settings_postgres
  manage.py revisar_datos --database empresa2 --corregir --settings=erp.settings_postgres"""
import re
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

D0 = Decimal('0')


def revisiones():
    """[(código, título, [texto de cada registro])]."""
    from compras.models import Compra
    from core.models import Almacen, Kardex
    from contabilidad.models import PeriodoContable
    from obras.models import Obra
    from planillas.models import Parametro, Planilla, Trabajador
    from produccion.models import CentroTrabajo, OrdenProduccion
    hoy = timezone.localdate()
    salida = []

    def agregar(codigo, titulo, filas):
        salida.append((codigo, titulo, [str(f) for f in filas]))

    agregar('MAN-05', 'Puestos de trabajo con tarifa por hora negativa',
            [f'{c.codigo} {c.nombre}: MO {c.costo_hora_mo}, máquina {c.costo_hora_maquina}, CIF {c.costo_hora_cif}'
             for c in CentroTrabajo.objects.filter(Q(costo_hora_mo__lt=0) | Q(costo_hora_maquina__lt=0) |
                                                   Q(costo_hora_cif__lt=0))])
    agregar('COM-04', 'Facturas de compra que vencen antes de emitirse',
            [f'id {c.pk} {c}: emitida {c.fecha_emision:%d/%m/%Y}, vence {c.fecha_vencimiento:%d/%m/%Y}'
             for c in Compra.objects.exclude(estado='ANULADO').filter(fecha_vencimiento__lt=F('fecha_emision'))])
    agregar('SEG-02', 'Almacenes con código HTML en el nombre',
            [f'id {a.pk} {a.codigo}: {a.nombre!r}' for a in Almacen.objects.filter(Q(nombre__contains='<') |
                                                                                  Q(nombre__contains='>'))])
    # DAT-02: datos de prueba antiguos
    imposibles = []
    for p in Planilla.objects.exclude(estado='ANULADA'):
        anio, mes = (int(p.periodo[:4]), int(p.periodo[4:6])) if p.periodo[:6].isdigit() else (0, 0)
        if not (2000 <= anio <= hoy.year + 1 and 1 <= mes <= 12):
            imposibles.append(f'planilla id {p.pk}: periodo {p.periodo}')
    agregar('DAT-02', 'Planillas con periodo imposible', imposibles)
    rmv = (Parametro.objects.order_by('-anio').values_list('rmv', flat=True).first() or Decimal('1130'))
    agregar('DAT-02', f'Trabajadores activos con sueldo menor a la RMV (S/ {rmv})',
            [f'{t.numero_doc} {t}: S/ {t.sueldo}' for t in Trabajador.objects.filter(fecha_cese__isnull=True, sueldo__gt=0,
                                                                                      sueldo__lt=rmv)])
    agregar('DAT-02', 'Trabajadores con DNI que no tiene 8 dígitos',
            [f'id {t.pk} {t}: {t.numero_doc!r}' for t in Trabajador.objects.filter(tipo_doc='01')
             if not (t.numero_doc.isdigit() and len(t.numero_doc) == 8)])
    agregar('DAT-02', 'Trabajadores activos sin centro de costo',
            [f'{t.numero_doc} {t}' for t in Trabajador.objects.filter(fecha_cese__isnull=True, centro_costo__isnull=True)])
    agregar('DAT-02', 'Obras con área negativa',
            [f'id {o.pk} {o}: {o.area_m2} m²' for o in Obra.objects.filter(area_m2__lt=0)])
    agregar('DAT-02', 'Movimientos de kardex con fecha futura',
            [f'id {k.pk} {k.fecha:%d/%m/%Y} {k.referencia}' for k in Kardex.objects.filter(fecha__gt=hoy)[:200]])
    agregar('DAT-02', 'Periodos contables de meses futuros',
            [p.periodo for p in PeriodoContable.objects.filter(periodo__gt=(hoy + timedelta(days=31)).strftime('%Y%m'))])
    agregar('DAT-02', 'Órdenes de producción anuladas sin número, fecha o motivo (regla MAN-R02)',
            [f'id {o.pk} {o.numero or "(sin número)"}' for o in OrdenProduccion.objects.filter(estado='ANULADA').filter(
                Q(numero='') | Q(anulado_en__isnull=True) | Q(motivo_anulacion=''))])
    # DAT-01: lo migrado del sistema anterior (es_historico) que no cuadra
    historicas = Compra.objects.filter(es_historico=True).exclude(estado='ANULADO')
    agregar('DAT-01', 'Compras migradas cuyo total no es la suma de sus componentes',
            [f'id {c.pk} {c}: total {c.total} ≠ {c.base_imponible + c.no_gravado + c.igv}'
             for c in historicas.exclude(total=F('base_imponible') + F('no_gravado') + F('igv'))[:500]])
    agregar('DAT-01', 'Comprobantes migrados con IGV mayor al 50 % de la base',
            [f'id {c.pk} {c}: IGV {c.igv} sobre base {c.base_imponible}'
             for c in historicas.filter(base_imponible__gt=0, igv__gt=F('base_imponible') / 2)[:500]])
    agregar('DAT-01', 'Órdenes de producción migradas que terminan antes de empezar',
            [f'id {o.pk} {o.numero}: {o.fecha_inicio} → {o.fecha_fin}' for o in OrdenProduccion.objects.filter(
                fecha_inicio__isnull=False, fecha_fin__lt=F('fecha_inicio'))[:500]])
    agregar('DAT-01', 'Órdenes terminadas con 0 unidades',
            [f'id {o.pk} {o.numero}' for o in OrdenProduccion.objects.filter(estado='TERMINADA',
                                                                              cantidad_producida__lte=0)[:500]])
    return salida


def corregir():
    """[texto de cada corrección] de lo que no necesita una decisión (ver el docstring del módulo)."""
    from django.db import transaction

    from core.auditoria import registrar
    from core.models import Almacen
    from produccion.models import CentroTrabajo, OrdenProduccion
    hechas = []
    with transaction.atomic():
        for c in CentroTrabajo.objects.filter(Q(costo_hora_mo__lt=0) | Q(costo_hora_maquina__lt=0) |
                                              Q(costo_hora_cif__lt=0)):
            cambios = {}
            for campo in ('costo_hora_mo', 'costo_hora_maquina', 'costo_hora_cif'):
                if getattr(c, campo) < 0:
                    cambios[campo] = [str(getattr(c, campo)), '0']
                    setattr(c, campo, D0)
            c.save(update_fields=list(cambios))
            registrar('MODIFICAR', c, cambios, 'MAN-05: tarifa por hora negativa llevada a 0 (revisar_datos)')
            hechas.append(f'MAN-05 puesto {c.codigo}: ' + ', '.join(f'{k} {v[0]} → 0' for k, v in cambios.items()))
        for a in Almacen.objects.filter(Q(nombre__contains='<') | Q(nombre__contains='>')):
            nuevo = re.sub(r'\s+', ' ', re.sub(r'<[^>]*>|[<>]', ' ', a.nombre)).strip()[:100] or f'Almacén {a.codigo}'
            anterior, a.nombre = a.nombre, nuevo
            a.save(update_fields=['nombre'])
            registrar('MODIFICAR', a, {'nombre': [anterior, nuevo]}, 'SEG-02: código HTML quitado del nombre')
            hechas.append(f'SEG-02 almacén id {a.pk}: {anterior!r} → {nuevo!r}')
        for o in OrdenProduccion.objects.filter(estado='ANULADA').filter(Q(anulado_en__isnull=True) |
                                                                         Q(motivo_anulacion='')):
            campos = []
            if o.anulado_en is None:
                from django.utils import timezone as tz
                o.anulado_en = tz.now()
                campos.append('anulado_en')
            if not o.motivo_anulacion:
                o.motivo_anulacion = 'Anulada antes de que el sistema exigiera el motivo (depuración DAT-02)'
                campos.append('motivo_anulacion')
            o.save(update_fields=campos)
            registrar('MODIFICAR', o, {c: ['', 'completado'] for c in campos}, 'DAT-02: anulación sin fecha o motivo')
            hechas.append(f'DAT-02 orden id {o.pk} {o.numero or "(sin número)"}: {", ".join(campos)} completado')
    return hechas


class Command(BaseCommand):
    help = 'Lista (sin corregir) los datos que no pasan las validaciones actuales: tarifas negativas, vencimientos, ' \
           'nombres con HTML, datos de prueba antiguos y lo migrado que no cuadra.'

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default', help='Alias de la base (default o empresa2)')
        parser.add_argument('--corregir', action='store_true',
                            help='Arregla tarifas negativas, HTML en nombres de almacén y anulaciones sin fecha o motivo')

    def handle(self, *args, **opciones):
        from erp.empresas import activar, restaurar
        token = activar(opciones['database'])
        try:
            if opciones['corregir']:
                hechas = corregir()
                for h in hechas:
                    self.stdout.write(f'  corregido: {h}')
                self.stdout.write(self.style.SUCCESS(f'{len(hechas)} corrección(es); queda por revisar:'))
            total = 0
            for codigo, titulo, filas in revisiones():
                total += len(filas)
                self.stdout.write(f'[{codigo}] {titulo}: {len(filas)}')
                for f in filas[:50]:
                    self.stdout.write(f'    {f}')
                if len(filas) > 50:
                    self.stdout.write(f'    … y {len(filas) - 50} más')
            self.stdout.write(self.style.SUCCESS(f'{total} registro(s) por revisar.') if total else
                              self.style.SUCCESS('Sin observaciones.'))
        finally:
            restaurar(token)
