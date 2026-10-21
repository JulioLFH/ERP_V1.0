"""Aprobación de órdenes de compra por niveles (motor único, core/aprobaciones.py): nivel 1 → 2 → 3 según el monto,
el centro de costo y el tipo de compra, con historial y rechazo con motivo. Sin reglas para órdenes de compra, la
aprueba quien tenga «Aprobar órdenes de compra» hasta su límite (comportamiento anterior)."""
from django.contrib.auth import get_user_model

from core import aprobaciones
from core.models import r2
from core.segregacion import creador, error_aprobacion

from .models import Compra, OrdenCompra


class ErrorAprobacionOC(Exception):
    pass


def _monto(oc):
    return r2(oc.total * (oc.tipo_cambio or 1)) if oc.moneda == 'USD' else oc.total


def solicitante(oc):
    return get_user_model().objects.filter(pk=creador(oc)).first()


def calcular_niveles(oc):
    tipo = Compra.clasificacion_de(oc.items.select_related('producto'))
    tipo = {'MATERIA_PRIMA': 'MATERIAL', 'SUMINISTRO': 'MATERIAL', 'ENVASE': 'MATERIAL',
            'ACTIVO_FIJO': 'ACTIVO'}.get(tipo, tipo)
    return aprobaciones.calcular_niveles('OC', solicitante(oc), _monto(oc), oc.centro_costo, tipo)


def por_niveles(oc):
    """¿La OC se aprueba por niveles? (hay reglas que le aplican). Calcula y guarda sus niveles la primera vez."""
    if oc.estado != 'PENDIENTE' or not aprobaciones.hay_reglas('OC'):
        return False
    if not oc.niveles:
        try:
            oc.niveles, oc.nivel_aprobado = calcular_niveles(oc), 0
        except aprobaciones.ErrorAprobacion as exc:
            raise ErrorAprobacionOC(str(exc)) from exc
        OrdenCompra.objects.filter(pk=oc.pk).update(niveles=oc.niveles, nivel_aprobado=0)
    return bool(oc.niveles)


def puede_aprobar(user, oc):
    if not por_niveles(oc):
        from core.permisos import puede_aprobar as por_tope
        return por_tope(user, _monto(oc))
    s = solicitante(oc)
    return aprobaciones.puede_aprobar(user, 'OC', oc.niveles, oc.nivel_aprobado, s.pk if s else None)


def aprobar(oc, user, comentario=''):
    """Aprueba el nivel que le toca al usuario (o la OC entera sin reglas). Devuelve el mensaje para el usuario."""
    from .abastecimiento import validar_orden
    errores = validar_orden(oc)
    if errores:
        raise ErrorAprobacionOC('No se aprueba: no cumple el contrato marco. ' + ' '.join(errores))
    from .registro_info import problemas_orden
    bloqueos, _ = problemas_orden(oc)
    if bloqueos:
        raise ErrorAprobacionOC('No se aprueba: ' + ' '.join(bloqueos) + ' Elija otro proveedor o levante el bloqueo '
                                'en Compras › Proveedores por producto.')
    error = error_aprobacion(user, oc)
    if error:
        raise ErrorAprobacionOC(error)
    if not por_niveles(oc):
        from core.permisos import perfil_de, puede_aprobar as por_tope
        monto = _monto(oc)
        if not por_tope(user, monto):
            perfil = perfil_de(user)
            raise ErrorAprobacionOC(
                f'La orden suma S/ {monto:,.2f} y su límite de aprobación es S/ {perfil.limite_aprobacion:,.2f}: debe '
                f'aprobarla un usuario con mayor límite.' if perfil and perfil.limite_aprobacion else
                'Su usuario no puede aprobar órdenes de compra.')
        oc.estado = 'APROBADO'
        oc.save(update_fields=['estado'])
        aprobaciones.registrar('OC', oc, user, 'APROBADA', comentario)
        return f'Orden {oc.numero}: Aprobado.'
    if not puede_aprobar(user, oc):
        n = aprobaciones.pendiente(oc.niveles, oc.nivel_aprobado)
        raise ErrorAprobacionOC(f'La orden la aprueba ahora el nivel {n["nivel"]}: {n["nombre"]}.')
    oc.nivel_aprobado = aprobaciones.pendiente(oc.niveles, oc.nivel_aprobado)['nivel']
    aprobaciones.registrar('OC', oc, user, 'APROBADA', comentario, oc.nivel_aprobado)
    siguiente = aprobaciones.pendiente(oc.niveles, oc.nivel_aprobado)
    if siguiente is not None:
        aprobaciones.avanzar(oc.niveles, oc.nivel_aprobado)
        oc.save(update_fields=['niveles', 'nivel_aprobado'])
        return (f'Orden {oc.numero}: nivel {oc.nivel_aprobado} aprobado; pasa a {siguiente["nombre"]} '
                f'(nivel {siguiente["nivel"]}).')
    oc.estado, oc.observacion_aprobacion = 'APROBADO', ''
    oc.save(update_fields=['estado', 'niveles', 'nivel_aprobado', 'observacion_aprobacion'])
    return f'Orden {oc.numero}: aprobada en todos sus niveles.'


def rechazar(oc, user, motivo):
    """Vuelve al comprador con el motivo; la aprobación empieza de nuevo."""
    if oc.estado != 'PENDIENTE':
        raise ErrorAprobacionOC('Solo se rechaza una orden por aprobar.')
    if len((motivo or '').strip()) < 10:
        raise ErrorAprobacionOC('Indique el motivo del rechazo (mínimo 10 caracteres).')
    if not puede_aprobar(user, oc):
        raise ErrorAprobacionOC('No le corresponde revisar esta orden.')
    aprobaciones.registrar('OC', oc, user, 'RECHAZADA', motivo,
                           (aprobaciones.pendiente(oc.niveles, oc.nivel_aprobado) or {}).get('nivel'))
    oc.niveles, oc.nivel_aprobado, oc.observacion_aprobacion = [], 0, motivo.strip()[:300]
    oc.save(update_fields=['niveles', 'nivel_aprobado', 'observacion_aprobacion'])
    return f'Orden {oc.numero} rechazada: vuelve al comprador para corregirla.'
