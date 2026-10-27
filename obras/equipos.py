"""Equipos en la obra (OBR-19): parte diario por equipo y partida (horómetro, horas trabajadas y en espera,
combustible, operador y tarifa), su costo frente a la meta de equipos del análisis de precios unitarios y el cruce de
los alquileres con las facturas del proveedor.

- El horómetro del parte pasa a Flota como lectura del equipo (por aprobar), sin aceptar lecturas menores.
- Equipo alquilado: su costo contable es la factura del proveedor con el centro de costo de la obra; el parte la
  sustenta (horas reportadas × tarifa frente a lo facturado en el mes).
- Equipo propio: su costo (depreciación, combustible, mantenimiento) está en el centro de costo del equipo; la regla
  de reparto con base «Horas de equipo» lo lleva a las obras por las horas de sus partes."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import ParteEquipo, RecursoAPU

D0 = Decimal('0')


class ErrorParte(Exception):
    pass


def tarifa_sugerida(obra, equipo):
    """La del último parte del equipo en la obra o el precio del recurso del análisis vinculado al equipo."""
    if equipo is None:
        return D0
    previo = ParteEquipo.objects.filter(obra=obra, equipo=equipo, tarifa__gt=0).order_by('-fecha', '-id').first()
    if previo:
        return previo.tarifa
    from .valorizaciones import presupuesto_meta
    meta = presupuesto_meta(obra)
    if meta is not None:
        l = RecursoAPU.objects.filter(partida__presupuesto=meta, recurso__equipo=equipo).select_related(
            'recurso').first()
        if l:
            from .calculos import precios_de
            return precios_de(meta).get(l.recurso_id, l.recurso.precio)
    return D0


@transaction.atomic
def registrar(parte, usuario):
    """Valida y graba el parte; el horómetro final va a Flota como lectura del equipo. OBR-37: partida obligatoria,
    números no negativos, horas mayores que cero y las horas trabajadas no más que las del horómetro (si son menos,
    el parte lleva el aviso en `parte.avisos`)."""
    parte.avisos = []
    if parte.equipo_id is None and not parte.descripcion.strip():
        raise ErrorParte('Elija el equipo o descríbalo si no está en Flota.')
    if parte.fecha > timezone.localdate():
        raise ErrorParte('El parte no puede ser de una fecha futura.')
    if parte.fecha.year < 2000:
        raise ErrorParte(f'Revise la fecha del parte ({parte.fecha:%d/%m/%Y}).')
    if parte.propiedad not in dict(ParteEquipo.PROPIEDADES):
        raise ErrorParte('Indique si el equipo es propio o alquilado.')
    if not parte.partida_id:
        raise ErrorParte('Elija la partida en la que trabajó el equipo.')
    if parte.partida.presupuesto.obra_id != parte.obra_id:
        raise ErrorParte('La partida no es de la obra.')
    for campo, nombre in (('combustible', 'El combustible'), ('tarifa', 'La tarifa'),
                          ('tarifa_espera', 'La tarifa en espera')):
        if (getattr(parte, campo) or 0) < 0:
            raise ErrorParte(f'{nombre} no puede ser negativo(a).')
    if parte.horometro_inicio is not None and parte.horometro_fin is not None:
        if parte.horometro_fin < parte.horometro_inicio:
            raise ErrorParte('El horómetro final es menor al inicial.')
        diferencia = parte.horometro_fin - parte.horometro_inicio
        if not parte.horas:
            parte.horas = diferencia
        elif parte.horas > diferencia + Decimal('0.1'):
            raise ErrorParte(f'Las horas trabajadas ({parte.horas:g}) superan las del horómetro '
                             f'({parte.horometro_inicio:g} → {parte.horometro_fin:g} = {diferencia:g} h).')
        elif parte.horas < diferencia - Decimal('0.1'):
            parte.avisos.append(f'El horómetro marca {diferencia:g} h y se reportan {parte.horas:g} h trabajadas: la '
                                f'diferencia debería estar en las horas en espera.')
    if parte.horas < 0 or parte.horas_espera < 0 or parte.horas + parte.horas_espera > 24:
        raise ErrorParte('Las horas trabajadas y en espera van de 0 a 24 en el día.')
    if not parte.horas and not parte.horas_espera:
        raise ErrorParte('El parte no tiene horas trabajadas ni en espera.')
    if parte.propiedad == 'ALQUILADO' and parte.proveedor_id is None:
        raise ErrorParte('Indique el proveedor del alquiler.')
    if not parte.tarifa:
        parte.tarifa = tarifa_sugerida(parte.obra, parte.equipo)
    parte.registrado_por = usuario if usuario and usuario.is_authenticated else None
    parte.save()
    if not parte.numero:
        parte.numero = f'PE-{parte.pk:06d}'
        parte.save(update_fields=['numero'])
    if parte.equipo_id and parte.horometro_fin is not None and parte.equipo.medidor == 'HORAS':
        from flota.lecturas import ErrorLectura, registrar as registrar_lectura
        try:
            registrar_lectura(parte.equipo, parte.horometro_fin, parte.fecha, parte.registrado_por, origen='PARTE',
                              parte=parte.numero, observacion=f'Parte de la obra {parte.obra.codigo}')
        except ErrorLectura as exc:
            raise ErrorParte(str(exc))
    return parte


@transaction.atomic
def anular(parte, usuario, motivo):
    """OBR-37: el parte mal registrado se anula con su motivo; deja de contar en el costo de la obra y en el reparto
    por horas de equipo."""
    if parte.anulado:
        raise ErrorParte('El parte ya está anulado.')
    if len((motivo or '').strip()) < 5:
        raise ErrorParte('Indique el motivo de la anulación.')
    from contabilidad.models import PeriodoContable
    if PeriodoContable.esta_cerrado(parte.fecha.strftime('%Y%m')):
        raise ErrorParte(f'El periodo {parte.fecha:%m/%Y} está cerrado.')
    parte.anulado, parte.motivo_anulacion = True, motivo.strip()[:250]
    parte.anulado_por = usuario if usuario and usuario.is_authenticated else None
    parte.anulado_en = timezone.now()
    parte.save(update_fields=['anulado', 'motivo_anulacion', 'anulado_por', 'anulado_en'])
    return parte


def horas_por_centro(periodo, centros):
    """{centro de costo: horas trabajadas} de los equipos propios en los partes del mes (base de reparto)."""
    salida = {}
    for cc, horas in ParteEquipo.objects.filter(
            propiedad='PROPIO', fecha__year=int(periodo[:4]), fecha__month=int(periodo[4:]),
            obra__centro_costo_id__in=list(centros)).values('obra__centro_costo_id').annotate(
            h=Sum('horas')).values_list('obra__centro_costo_id', 'h'):
        salida[cc] = Decimal(horas or 0)
    return salida


def resumen(obra):
    """Por equipo: horas, espera, combustible y costo de los partes; meta de equipos (hm × precio del análisis) y el
    cruce de los alquileres por proveedor y mes con sus facturas de la obra."""
    from compras.models import Compra

    from .calculos import explosion
    from .valorizaciones import presupuesto_meta
    partes = list(ParteEquipo.objects.filter(obra=obra).select_related('equipo', 'proveedor', 'partida'))
    por_equipo = defaultdict(lambda: {'horas': D0, 'espera': D0, 'combustible': D0, 'costo': D0, 'partes': 0})
    for p in partes:
        f = por_equipo[p.nombre_equipo]
        f['horas'] += p.horas
        f['espera'] += p.horas_espera
        f['combustible'] += p.combustible
        f['costo'] += p.costo
        f['partes'] += 1
    meta = presupuesto_meta(obra)
    meta_eq = D0
    if meta is not None:
        meta_eq = explosion(meta)['por_tipo'].get('EQ', D0)
    alquileres = defaultdict(lambda: {'reportado': D0, 'horas': D0})
    for p in partes:
        if p.propiedad == 'ALQUILADO' and p.proveedor_id:
            f = alquileres[(p.proveedor, p.fecha.strftime('%Y%m'))]
            f['reportado'] += p.costo
            f['horas'] += p.horas
    cruce = []
    for (prov, periodo), f in sorted(alquileres.items(), key=lambda x: (x[0][1], x[0][0].nombre)):
        facturado = Compra.objects.filter(tercero=prov, periodo=periodo, estado='REGISTRADO',
                                          centro_costo=obra.centro_costo).aggregate(s=Sum('base_pen'))['s'] or D0
        cruce.append({'proveedor': prov, 'periodo': periodo, 'horas': f['horas'], 'reportado': f['reportado'],
                      'facturado': facturado, 'diferencia': facturado - f['reportado']})
    total = sum((f['costo'] for f in por_equipo.values()), D0)
    return {'equipos': sorted(por_equipo.items()), 'total': total, 'meta': meta_eq,
            'desvio': total - meta_eq, 'cruce': cruce}
