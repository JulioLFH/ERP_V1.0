"""Avisos de aprobación por Conversaciones (como en Odoo): cuando alguien pide una aprobación, quien la registra le
escribe por chat directo a los usuarios que pueden aprobarla, con el enlace al documento; cuando se aprueba (o pasa
al siguiente nivel), observa o rechaza, se avisa al siguiente aprobador o a quien la pidió.

Lo llaman core.aprobaciones.registrar (todos los documentos del motor), el alta de la orden de compra y el cambio de
estado de las rendiciones. Un aviso que falla nunca detiene el documento."""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import NoReverseMatch, reverse

log = logging.getLogger(__name__)

NOMBRES = {'OC': 'la orden de compra', 'SOLPED': 'la solicitud de pedido', 'REQUERIMIENTO': 'el requerimiento',
           'RENDICION': 'la rendición de gastos', 'VACACION': 'la solicitud de vacaciones o permiso',
           'MOVIMIENTO': 'el movimiento de personal', 'PEDIDO': 'el pedido de venta',
           'HES': 'la conformidad de servicio (HES)'}
RUTAS = {'OC': 'compras:oc_detalle', 'SOLPED': 'solicitudes:detalle', 'REQUERIMIENTO': 'requerimientos:detalle',
         'RENDICION': 'rendiciones:detalle', 'MOVIMIENTO': 'planillas:movimiento', 'PEDIDO': 'ventas:cot_detalle',
         'HES': 'compras:hes', 'VACACION': 'planillas:solicitudes_personal'}
PIDE = {'ENVIADA', 'ENVIADO', 'SOLICITADA'}
APRUEBA = {'APROBADA', 'APROBADO', 'LIBERADA', 'EN_REVISION'}  # EN_REVISION: rendición aprobada en un nivel
DEVUELVE = {'RECHAZADA': 'rechazó', 'RECHAZADO': 'rechazó', 'OBSERVADA': 'observó'}
MAXIMO = 10  # sin un aprobador por nivel: a los primeros usuarios con el permiso del documento


def enlace(documento, objeto):
    try:
        return reverse(RUTAS[documento]) if documento == 'VACACION' else reverse(RUTAS[documento], args=[objeto.pk])
    except (KeyError, NoReverseMatch):
        return ''


def solicitante(objeto):
    for campo in ('solicitante', 'solicitado_por', 'conforme_por', 'rendidor'):
        u = getattr(objeto, campo, None)
        if u is not None and hasattr(u, 'is_active'):
            return u
    from core.segregacion import creador
    pk = creador(objeto)
    return get_user_model().objects.filter(pk=pk).first() if pk else None


def aprobadores_de(documento, objeto):
    """Quienes pueden aprobar hoy el nivel pendiente: el titular, sus suplentes y su jefe si escaló; sin niveles (o
    nivel sin usuario), los usuarios con el permiso del documento."""
    from core import aprobaciones as motor
    from core.permisos import puede

    from .servicios import usuarios_internos
    nivel = motor.pendiente(getattr(objeto, 'niveles', None) or [], getattr(objeto, 'nivel_aprobado', 0) or 0)
    if nivel and nivel.get('usuario'):
        return motor.aprobadores(nivel)
    if getattr(objeto, 'niveles', None) and nivel is None:
        return []  # ya no le falta ningún nivel
    permiso = motor.PERMISOS.get(documento)
    if not permiso:
        return []
    candidatos = [u for u in usuarios_internos() if not u.is_superuser and puede(u, permiso)]
    return candidatos[:MAXIMO] or [u for u in usuarios_internos() if u.is_superuser][:MAXIMO]


def _escribir(de, para, texto, url):
    from .servicios import directo
    from .models import Mensaje
    canal = directo(de, para)
    Mensaje.objects.create(canal=canal, autor=de, texto=texto[:4000], enlace=url[:300])


def _enviar(documento, objeto, usuario, accion, comentario):
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        return 0
    doc, url = NOMBRES.get(documento, 'el documento'), enlace(documento, objeto)
    nivel = None
    from core import aprobaciones as motor
    pendiente = motor.pendiente(getattr(objeto, 'niveles', None) or [], getattr(objeto, 'nivel_aprobado', 0) or 0)
    if accion in PIDE or (accion in APRUEBA and pendiente is not None):
        destinatarios = aprobadores_de(documento, objeto)
        nivel = pendiente.get('nivel') if pendiente else None
        texto = (f'Solicito su aprobación para {doc} {objeto}' + (f' (nivel {nivel})' if nivel and nivel > 1 else '')
                 + '.' + (f'\n{comentario}' if comentario and accion in PIDE else ''))
        if accion in APRUEBA:
            texto = f'Aprobé {doc} {objeto} en mi nivel. ' + texto
    elif accion in APRUEBA or accion in DEVUELVE:
        quien = solicitante(objeto)
        destinatarios = [quien] if quien is not None else []
        verbo = DEVUELVE.get(accion, 'aprobó')
        texto = f'{"Aprobé" if verbo == "aprobó" else verbo.capitalize()} {doc} {objeto}.' + (
            f'\nMotivo: {comentario}' if comentario and accion in DEVUELVE else '')
        if verbo == 'observó':
            texto = f'Observé {doc} {objeto}: corríjala y vuelva a enviarla.' + (
                f'\nMotivo: {comentario}' if comentario else '')
        elif verbo == 'rechazó':
            texto = f'Rechacé {doc} {objeto}.' + (f'\nMotivo: {comentario}' if comentario else '')
    else:
        return 0
    enviados = 0
    for para in {u.pk: u for u in destinatarios if u is not None and u.is_active}.values():
        if para.pk == usuario.pk:
            continue
        _escribir(usuario, para, texto, url)
        enviados += 1
    return enviados


def aviso_aprobacion(documento, objeto, usuario, accion, comentario=''):
    """Programa el aviso por chat para cuando el documento quede guardado (después del commit)."""
    def enviar():
        try:
            with transaction.atomic():
                _enviar(documento, objeto, usuario, accion, (comentario or '').strip())
        except Exception:  # el aviso nunca detiene la aprobación
            log.exception('No se pudo enviar el aviso de aprobación de %s %s', documento, getattr(objeto, 'pk', ''))
    transaction.on_commit(enviar)


def oc_registrada(sender, instance, created, **kwargs):
    """Señal: una orden de compra nueva queda pendiente de aprobación: avisa a quienes la aprueban."""
    if not created or instance.estado != 'PENDIENTE' or kwargs.get('raw'):
        return

    def enviar():
        try:
            from compras.aprobacion_oc import ErrorAprobacionOC, por_niveles
            oc = type(instance).objects.get(pk=instance.pk)
            try:
                por_niveles(oc)  # calcula y guarda sus niveles si hay reglas
            except ErrorAprobacionOC:
                pass
            if oc.estado != 'PENDIENTE':
                return
            with transaction.atomic():
                _enviar('OC', oc, solicitante(oc), 'ENVIADA', oc.glosa or '')
        except Exception:
            log.exception('No se pudo avisar la orden de compra %s', instance.pk)
    transaction.on_commit(enviar)
