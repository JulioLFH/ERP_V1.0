"""Lecturas de odómetro u horómetro (FLO-02): parte diario por lote con aprobación, sin lecturas menores a la
anterior, lectura actual del equipo y uso proyectado para planificar los mantenimientos por uso."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import LecturaEquipo

D0 = Decimal('0')
DIAS_USO = 90  # ventana para calcular el uso diario


class ErrorLectura(Exception):
    pass


def ultima(equipo, incluir_pendientes=True):
    qs = equipo.lecturas.exclude(estado='RECHAZADA')
    if not incluir_pendientes:
        qs = qs.filter(estado='APROBADA')
    return qs.aggregate(m=Max('valor'))['m']


def validar(equipo, valor, fecha):
    if not equipo.medidor:
        raise ErrorLectura(f'{equipo} no tiene odómetro ni horómetro (indíquelo en su ficha).')
    if valor is None or valor < 0:
        raise ErrorLectura(f'{equipo}: indique una lectura válida.')
    anterior = ultima(equipo)
    if anterior is not None and valor < anterior:
        raise ErrorLectura(f'{equipo}: la lectura {valor:,.1f} es menor a la anterior ({anterior:,.1f} '
                           f'{equipo.unidad_medidor}).')
    if fecha > timezone.localdate():
        raise ErrorLectura('La fecha de la lectura no puede ser futura.')


@transaction.atomic
def registrar(equipo, valor, fecha, usuario, origen='PARTE', parte='', observacion=''):
    """Registra la lectura. El parte diario queda por aprobar; las de otro documento (vale, viaje, OT) se aprueban."""
    from produccion.models import Equipo
    equipo = Equipo.objects.select_for_update().get(pk=equipo.pk)
    validar(equipo, valor, fecha)
    lectura = LecturaEquipo.objects.create(equipo=equipo, fecha=fecha, valor=valor, origen=origen, parte=parte,
                                           observacion=observacion[:200], registrado_por=usuario,
                                           estado='PENDIENTE' if origen in ('PARTE', 'CARGA') else 'APROBADA')
    if lectura.estado == 'APROBADA':
        lectura.aprobado_por, lectura.aprobado_en = usuario, timezone.now()
        lectura.save(update_fields=['aprobado_por', 'aprobado_en'])
        actualizar_equipo(equipo)
    return lectura


@transaction.atomic
def registrar_parte(filas, fecha, usuario):
    """Parte diario por lote: filas [(equipo, valor)]. Todo o nada: si una lectura no vale, no se graba ninguna."""
    from core.models import Serie
    errores = []
    for equipo, valor in filas:
        try:
            validar(equipo, valor, fecha)
        except ErrorLectura as exc:
            errores.append(str(exc))
    if errores:
        raise ErrorLectura(' '.join(errores))
    if not filas:
        raise ErrorLectura('Ingrese al menos una lectura.')
    s, n = Serie.siguiente('PDL', 'PD')
    parte = f'{s}-{n}'
    return parte, [registrar(e, v, fecha, usuario, 'PARTE', parte) for e, v in filas]


@transaction.atomic
def resolver(lecturas, usuario, aprobar=True, motivo=''):
    from core.segregacion import _otro_usuario
    lecturas = list(lecturas)
    propias = [l for l in lecturas if l.estado == 'PENDIENTE' and l.registrado_por_id == usuario.pk]
    if aprobar and propias and _otro_usuario(usuario):  # FIN-10: regla fija, también para el administrador
        partes = sorted({l.parte or str(l.equipo) for l in propias})
        raise ErrorLectura(f'Segregación de funciones: usted registró {", ".join(partes)} y no puede aprobarlo; '
                           f'debe hacerlo otro usuario.')
    equipos = set()
    for l in lecturas:
        if l.estado != 'PENDIENTE':
            continue
        l.estado = 'APROBADA' if aprobar else 'RECHAZADA'
        l.aprobado_por, l.aprobado_en = usuario, timezone.now()
        if motivo:
            l.observacion = motivo[:200]
        l.save()
        equipos.add(l.equipo)
    for e in equipos:
        actualizar_equipo(e)
    return len(equipos)


def actualizar_equipo(equipo):
    """Lectura actual = la mayor aprobada."""
    from produccion.models import Equipo
    fila = equipo.lecturas.filter(estado='APROBADA').order_by('-valor', '-fecha').values('valor', 'fecha').first()
    if fila:
        Equipo.objects.filter(pk=equipo.pk).update(lectura_actual=fila['valor'], lectura_fecha=fila['fecha'])
        equipo.lectura_actual, equipo.lectura_fecha = fila['valor'], fila['fecha']


def uso_diario(equipo):
    """km u horas por día según las lecturas aprobadas de los últimos 90 días (None si no alcanza)."""
    desde = timezone.localdate() - timedelta(days=DIAS_USO)
    lecturas = list(equipo.lecturas.filter(estado='APROBADA', fecha__gte=desde).order_by('fecha', 'valor')
                    .values_list('fecha', 'valor'))
    if len(lecturas) < 2:
        return None
    (f0, v0), (f1, v1) = lecturas[0], lecturas[-1]
    dias = (f1 - f0).days
    if dias <= 0 or v1 <= v0:
        return None
    return (v1 - v0) / dias


def fecha_proyectada(equipo, objetivo):
    """Fecha en que se proyecta llegar a la lectura objetivo (hoy si ya se pasó; None sin uso conocido)."""
    if objetivo is None:
        return None
    if equipo.lectura_actual >= objetivo:
        return timezone.localdate()
    uso = uso_diario(equipo)
    if not uso:
        return None
    base = equipo.lectura_fecha or timezone.localdate()
    return base + timedelta(days=int((objetivo - equipo.lectura_actual) / uso) + 1)


def proyectada(equipo, fecha):
    """Lectura proyectada a una fecha."""
    uso = uso_diario(equipo)
    if not uso or not equipo.lectura_fecha:
        return equipo.lectura_actual
    return equipo.lectura_actual + uso * max((fecha - equipo.lectura_fecha).days, 0)
