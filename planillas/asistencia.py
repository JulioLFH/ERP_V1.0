"""Asistencia y turnos: marcaciones (reloj, Excel o manual), resumen del mes por trabajador (faltas, tardanzas y horas
extra) y traslado a la planilla mensual.

- Falta: día laborable de su turno sin marcación ni vacaciones (salvo que esté justificada).
- Tardanza: minutos de entrada después de la hora del turno más la tolerancia.
- Horas extra: lo trabajado sobre la jornada del día; las dos primeras al 25 % y las siguientes al 35 % (D. S.
  007-2002-TR)."""
import calendar
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction

from .models import Marcacion, Planilla, Trabajador, Vacacion

D0 = Decimal('0')
D2 = Decimal('2')


class ErrorAsistencia(Exception):
    pass


def _rango(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    return date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])


def _horas(entrada, salida, refrigerio_min):
    inicio, fin = datetime.combine(date.today(), entrada), datetime.combine(date.today(), salida)
    if fin <= inicio:
        fin += timedelta(days=1)
    return max(Decimal((fin - inicio).seconds - refrigerio_min * 60) / 3600, D0)


def resumen(periodo, hasta_hoy=True):
    """[{t, turno, laborables, asistidos, faltas, tardanza_min, tardanzas, he25, he35, vacaciones, sin_turno}]"""
    desde, hasta = _rango(periodo)
    if hasta_hoy:
        hasta = min(hasta, date.today())
    marcas = defaultdict(dict)
    for m in Marcacion.objects.filter(fecha__range=[desde, hasta]):
        marcas[m.trabajador_id][m.fecha] = m
    vac = defaultdict(set)
    for v in Vacacion.objects.filter(tipo='GOCE', fecha_inicio__lte=hasta, fecha_fin__gte=desde):
        d = max(v.fecha_inicio, desde)
        while d <= min(v.fecha_fin, hasta):
            vac[v.trabajador_id].add(d)
            d += timedelta(days=1)
    filas = []
    for t in Trabajador.objects.select_related('turno').order_by('apellido_paterno', 'nombres'):
        if not t.activo_en(desde, hasta):
            continue
        turno = t.turno
        f = {'t': t, 'turno': turno, 'laborables': 0, 'asistidos': 0, 'faltas': 0, 'tardanza_min': 0,
             'tardanzas': 0, 'he25': D0, 'he35': D0, 'vacaciones': 0, 'sin_turno': turno is None}
        d = max(desde, t.fecha_ingreso)
        fin = min(hasta, t.fecha_cese or hasta)
        while d <= fin:
            m = marcas[t.pk].get(d)
            laborable = turno.laborable(d) if turno else d.isoweekday() <= 6
            if d in vac[t.pk]:
                f['vacaciones'] += 1
            elif laborable:
                f['laborables'] += 1
                if m and m.entrada:
                    f['asistidos'] += 1
                elif not (m and m.justificada):
                    f['faltas'] += 1
            if m and m.entrada and turno and not m.justificada and laborable:
                limite = (datetime.combine(d, turno.hora_entrada) + timedelta(minutes=turno.tolerancia_min)).time()
                if m.entrada > limite:
                    f['tardanzas'] += 1
                    f['tardanza_min'] += int((datetime.combine(d, m.entrada) -
                                              datetime.combine(d, turno.hora_entrada)).seconds / 60)
            if m and m.entrada and m.salida and turno:
                trabajadas = _horas(m.entrada, m.salida, turno.refrigerio_min)
                extra = trabajadas - (turno.horas_jornada if laborable else D0)
                if extra > Decimal('0.25'):  # menos de 15 minutos no es sobretiempo
                    f['he25'] += min(extra, D2)
                    f['he35'] += max(extra - D2, D0)
            d += timedelta(days=1)
        f['he25'], f['he35'] = f['he25'].quantize(Decimal('0.01')), f['he35'].quantize(Decimal('0.01'))
        filas.append(f)
    return filas


def _hora(valor):
    if valor in (None, ''):
        return None
    if isinstance(valor, time):
        return valor
    if isinstance(valor, datetime):
        return valor.time()
    texto = str(valor).strip()
    for fmt in ('%H:%M', '%H:%M:%S', '%I:%M %p'):
        try:
            return datetime.strptime(texto, fmt).time()
        except ValueError:
            pass
    raise ValueError(f'Hora inválida: {valor}')


@transaction.atomic
def importar(filas):
    """Filas de Excel con columnas dni, fecha, entrada, salida (y opcional observacion). Reemplaza la marcación del día.
    Devuelve (importadas, errores)."""
    from core.utils import a_fecha
    trabajadores = {t.numero_doc: t for t in Trabajador.objects.all()}
    ok, errores = 0, []
    for n, f in enumerate(filas, 2):
        dni = str(f.get('dni') or f.get('documento') or '').strip()
        if dni.endswith('.0'):
            dni = dni[:-2]
        t = trabajadores.get(dni) or trabajadores.get(dni.zfill(8))
        if t is None:
            errores.append(f'Fila {n}: no existe el trabajador {dni}.')
            continue
        try:
            fecha = a_fecha(f.get('fecha'))
            entrada, salida = _hora(f.get('entrada')), _hora(f.get('salida'))
        except ValueError as exc:
            errores.append(f'Fila {n}: {exc}')
            continue
        Marcacion.objects.update_or_create(trabajador=t, fecha=fecha, defaults={
            'entrada': entrada, 'salida': salida, 'origen': 'RELOJ',
            'observacion': str(f.get('observacion') or '')[:120]})
        ok += 1
    return ok, errores


@transaction.atomic
def pasar_a_planilla(periodo):
    """Lleva faltas y horas extra del mes a la planilla mensual (en borrador o calculada: vuelve a borrador)."""
    planilla = Planilla.objects.filter(periodo=periodo, tipo='MENSUAL').first()
    if planilla is None:
        raise ErrorAsistencia('Primero genere la planilla mensual del periodo.')
    if planilla.estado not in ('BORRADOR', 'CALCULADA'):
        raise ErrorAsistencia('La planilla del mes ya está cerrada.')
    datos = {f['t'].pk: f for f in resumen(periodo, hasta_hoy=False)}
    n = 0
    for fila in planilla.filas.all():
        f = datos.get(fila.trabajador_id)
        if f is None or f['sin_turno']:
            continue
        fila.dias_falta, fila.horas_extra_25, fila.horas_extra_35 = Decimal(f['faltas']), f['he25'], f['he35']
        fila.save(update_fields=['dias_falta', 'horas_extra_25', 'horas_extra_35'])
        n += 1
    planilla.estado = 'BORRADOR'
    planilla.save(update_fields=['estado'])
    return planilla, n
