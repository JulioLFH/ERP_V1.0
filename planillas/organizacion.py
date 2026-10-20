"""Estructura organizativa y personal: jefe por jerarquía (ocupante del puesto superior), usuario del trabajador,
cese (desactiva el usuario y reasigna sus aprobaciones), movimientos de personal con fecha efectiva y aprobación,
valores vigentes por periodo para la planilla, vacaciones y permisos solicitados por el trabajador y alertas de
contratos y documentos del legajo."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Contrato, DocumentoLegajo, MovimientoPersonal, Puesto, Trabajador, Vacacion


class ErrorPersonal(Exception):
    pass


# ---------------------------------------------------------------- jerarquía
def trabajador_de_usuario(usuario):
    if usuario is None or not getattr(usuario, 'pk', None):
        return None
    return Trabajador.objects.filter(usuario=usuario).select_related('puesto__superior').first()


def ocupante(puesto, fecha=None):
    return puesto.ocupantes(fecha).order_by('fecha_ingreso', 'id').first() if puesto else None


def usuario_del_puesto(puesto, fecha=None):
    t = ocupante(puesto, fecha)
    return t.usuario if t and t.usuario_id and t.usuario.is_active else None


def jefe_de_trabajador(t, fecha=None):
    """Ocupante del puesto superior (sube si el puesto superior está vacante)."""
    p, n = (t.puesto.superior if t and t.puesto_id else None), 0
    while p is not None and n < 20:
        jefe = ocupante(p, fecha)
        if jefe is not None:
            return jefe
        p, n = p.superior, n + 1
    return None


def jefe_de_usuario(usuario):
    """Usuario del jefe según la estructura organizativa (None si el usuario no tiene trabajador con puesto)."""
    t = trabajador_de_usuario(usuario)
    jefe = jefe_de_trabajador(t) if t else None
    return jefe.usuario if jefe and jefe.usuario_id and jefe.usuario.is_active else None


def arbol():
    """Unidades con sus puestos y ocupantes, anidadas (organigrama)."""
    from .models import UnidadOrganizativa
    unidades = list(UnidadOrganizativa.objects.filter(activo=True).select_related('centro_costo'))
    puestos = list(Puesto.objects.filter(activo=True).select_related('superior', 'centro_costo', 'unidad'))
    hoy = timezone.localdate()
    ocupados = {}
    for t in Trabajador.objects.filter(puesto__isnull=False, fecha_ingreso__isnull=False).exclude(fecha_cese__lt=hoy) \
            .select_related('usuario'):
        ocupados.setdefault(t.puesto_id, []).append(t)
    for p in puestos:
        p.lista_ocupantes = ocupados.get(p.pk, [])
    por_unidad = {}
    for p in puestos:
        por_unidad.setdefault(p.unidad_id, []).append(p)

    def nodo(u):
        return {'u': u, 'puestos': por_unidad.get(u.pk, []), 'hijas': [nodo(h) for h in unidades
                                                                         if h.padre_id == u.pk]}
    return [nodo(u) for u in unidades if u.padre_id is None]


# ---------------------------------------------------------------- cese
@transaction.atomic
def aplicar_cese(t, usuario=None):
    """Cese vigente: desactiva el usuario, le quita módulos y permisos y pasa sus aprobaciones pendientes a su jefe
    (o al primer superior con usuario). Devuelve el número de aprobaciones reasignadas."""
    if not (t.fecha_cese and t.fecha_cese <= timezone.localdate() and t.usuario_id):
        return 0
    u = t.usuario
    if u.is_active:
        u.is_active = False
        u.save(update_fields=['is_active'])
        u.groups.clear()
        from core.models import PerfilUsuario
        PerfilUsuario.objects.filter(usuario=u).update(acciones=[])
    return reasignar_aprobaciones(u, jefe_de_trabajador(t))


def reasignar_aprobaciones(usuario, jefe_trabajador):
    """Los niveles pendientes cuyo titular es el usuario pasan a su jefe (si tiene usuario)."""
    nuevo = jefe_trabajador.usuario if jefe_trabajador and jefe_trabajador.usuario_id else None
    if nuevo is None:
        return 0
    from compras.models import OrdenCompra, SolicitudPedido
    from core.aprobaciones import pendiente, registrar
    from inventario.models import RequerimientoInterno
    n = 0
    for doc, qs in (('OC', OrdenCompra.objects.filter(estado='PENDIENTE')),
                    ('SOLPED', SolicitudPedido.objects.filter(estado='ENVIADA')),
                    ('REQUERIMIENTO', RequerimientoInterno.objects.filter(estado='ENVIADO')),
                    ('VACACION', Vacacion.objects.filter(estado='SOLICITADA')),
                    ('MOVIMIENTO', MovimientoPersonal.objects.filter(estado='ENVIADO'))):
        for obj in qs.exclude(niveles=[]):
            nivel = pendiente(obj.niveles, obj.nivel_aprobado)
            if nivel and nivel.get('usuario') == usuario.pk:
                nivel['usuario'], nivel['nombre'] = nuevo.pk, nuevo.get_full_name() or nuevo.username
                type(obj).objects.filter(pk=obj.pk).update(niveles=obj.niveles)
                registrar(doc, obj, None, 'REASIGNADA', f'Cese de {usuario}: pasa a {nuevo}', nivel['nivel'])
                n += 1
    return n


# ---------------------------------------------------------------- movimientos de personal
CAMPOS = {'AUMENTO': ('sueldo',), 'CAMBIO_PUESTO': ('puesto', 'cargo', 'centro_costo', 'sueldo'),
          'CAMBIO_CC': ('centro_costo',), 'ALTA': ('puesto', 'cargo', 'centro_costo', 'sueldo'),
          'CESE': ('motivo_cese',)}


def _numero():
    from core.models import Serie
    s, n = Serie.siguiente('MOV', 'MP')
    return f'{s}-{n}'


def validar(m):
    errores = []
    if m.tipo == 'AUMENTO' and not (m.sueldo and m.sueldo > 0):
        errores.append('Indique la nueva remuneración.')
    if m.tipo == 'CAMBIO_PUESTO' and not m.puesto_id:
        errores.append('Indique el nuevo puesto.')
    if m.tipo == 'CAMBIO_CC' and not m.centro_costo_id:
        errores.append('Indique el nuevo centro de costo.')
    if m.tipo == 'CESE' and not m.motivo_cese:
        errores.append('Indique el motivo de cese.')
    if m.trabajador.fecha_ingreso and m.fecha_efectiva < m.trabajador.fecha_ingreso:
        errores.append('La fecha efectiva es anterior al ingreso.')
    if len((m.motivo or '').strip()) < 5:
        errores.append('Indique el motivo del movimiento.')
    return errores


@transaction.atomic
def enviar(m, usuario):
    from core import aprobaciones
    if m.estado != 'BORRADOR':
        raise ErrorPersonal('Solo se envían movimientos en borrador.')
    errores = validar(m)
    if errores:
        raise ErrorPersonal(' '.join(errores))
    if not m.numero:
        m.numero = _numero()
    monto = (m.sueldo or Decimal('0')) * 12 if m.tipo in ('AUMENTO', 'CAMBIO_PUESTO', 'ALTA') else Decimal('0')
    try:
        m.niveles = aprobaciones.calcular_niveles('MOVIMIENTO', usuario, monto, m.centro_costo or
                                                  m.trabajador.centro_costo, m.tipo)
    except aprobaciones.ErrorAprobacion as exc:
        raise ErrorPersonal(str(exc)) from exc
    m.estado, m.nivel_aprobado, m.solicitante = 'ENVIADO', 0, usuario
    if not m.niveles:  # sin reglas: aprueba quien tenga «Cerrar planillas»
        m.niveles = [{'nivel': 1, 'usuario': None, 'nombre': 'Aprobador de planillas',
                      'desde': timezone.now().isoformat()}]
    m.save()
    aprobaciones.registrar('MOVIMIENTO', m, usuario, 'ENVIADO', m.motivo)
    return m


def puede_aprobar(user, m):
    from core import aprobaciones
    return m.estado == 'ENVIADO' and aprobaciones.puede_aprobar(user, 'MOVIMIENTO', m.niveles, m.nivel_aprobado,
                                                                m.solicitante_id)


@transaction.atomic
def aprobar(m, usuario, comentario=''):
    from core import aprobaciones
    if not puede_aprobar(usuario, m):
        raise ErrorPersonal('No le corresponde aprobar este movimiento.')
    m.nivel_aprobado = aprobaciones.pendiente(m.niveles, m.nivel_aprobado)['nivel']
    aprobaciones.registrar('MOVIMIENTO', m, usuario, 'APROBADO', comentario, m.nivel_aprobado)
    if aprobaciones.pendiente(m.niveles, m.nivel_aprobado) is None:
        m.estado = 'APROBADO'
    else:
        aprobaciones.avanzar(m.niveles, m.nivel_aprobado)
    m.comentario = (comentario or '')[:300]
    m.save()
    if m.estado == 'APROBADO' and m.fecha_efectiva <= timezone.localdate():
        aplicar(m)
    return m


@transaction.atomic
def rechazar(m, usuario, comentario):
    from core import aprobaciones
    if not puede_aprobar(usuario, m):
        raise ErrorPersonal('No le corresponde revisar este movimiento.')
    if len((comentario or '').strip()) < 10:
        raise ErrorPersonal('Indique el motivo del rechazo (mínimo 10 caracteres).')
    m.estado, m.comentario = 'RECHAZADO', comentario.strip()[:300]
    m.save()
    aprobaciones.registrar('MOVIMIENTO', m, usuario, 'RECHAZADO', comentario)
    return m


def _foto(t):
    return {'sueldo': str(t.sueldo), 'puesto': t.puesto_id, 'cargo': t.cargo, 'centro_costo': t.centro_costo_id,
            'fecha_cese': t.fecha_cese.isoformat() if t.fecha_cese else None, 'motivo_cese': t.motivo_cese}


@transaction.atomic
def aplicar(m):
    """Lleva el movimiento aprobado a la ficha del trabajador (guardando los valores anteriores)."""
    if m.estado != 'APROBADO':
        return False
    t = m.trabajador
    m.anterior = _foto(t)
    if m.sueldo is not None and 'sueldo' in CAMPOS[m.tipo]:
        t.sueldo = m.sueldo
    if m.puesto_id and 'puesto' in CAMPOS[m.tipo]:
        t.puesto = m.puesto
        t.centro_costo = m.centro_costo or m.puesto.centro or t.centro_costo
    if m.cargo and 'cargo' in CAMPOS[m.tipo]:
        t.cargo = m.cargo
    if m.centro_costo_id and 'centro_costo' in CAMPOS[m.tipo]:
        t.centro_costo = m.centro_costo
    if m.tipo == 'CESE':
        t.fecha_cese, t.motivo_cese = m.fecha_efectiva, m.motivo_cese
    if m.tipo == 'ALTA' and not t.fecha_ingreso:
        t.fecha_ingreso = m.fecha_efectiva
    t.save()
    m.estado, m.aplicado_en = 'APLICADO', timezone.now()
    m.save(update_fields=['anterior', 'estado', 'aplicado_en'])
    if m.tipo == 'CESE':
        aplicar_cese(t)
    return True


def aplicar_vigentes(hoy=None):
    """Aplica los movimientos aprobados cuya fecha efectiva ya llegó y los ceses vigentes (diario y al abrir la
    lista de trabajadores)."""
    hoy = hoy or timezone.localdate()
    n = 0
    for m in MovimientoPersonal.objects.filter(estado='APROBADO', fecha_efectiva__lte=hoy).select_related(
            'trabajador'):
        n += aplicar(m)
    for t in Trabajador.objects.filter(fecha_cese__lte=hoy, usuario__is_active=True).select_related('usuario'):
        aplicar_cese(t)
    return n


def vigente(t, campo, fecha):
    """Valor del campo (sueldo, centro_costo_id, puesto_id) vigente en la fecha según el historial de movimientos
    aplicados: el del último movimiento con fecha efectiva ≤ fecha; si la fecha es anterior al primero, el valor que
    tenía antes de él; sin movimientos, el de la ficha."""
    clave = {'centro_costo_id': 'centro_costo', 'puesto_id': 'puesto'}.get(campo, campo)
    movs = [m for m in t.movimientos.filter(estado='APLICADO').order_by('fecha_efectiva', 'id')
            if clave in CAMPOS.get(m.tipo, ()) and getattr(m, campo) is not None]
    previos = [m for m in movs if m.fecha_efectiva <= fecha]
    if previos:
        valor = getattr(previos[-1], campo)
    elif movs:
        valor = movs[0].anterior.get(clave)
        if campo == 'sueldo' and valor is not None:
            valor = Decimal(valor)
    else:
        valor = getattr(t, campo)
    return valor if valor is not None else getattr(t, campo)


# ---------------------------------------------------------------- vacaciones y permisos (autoservicio)
@transaction.atomic
def solicitar_vacaciones(t, usuario, tipo, desde, hasta, observaciones=''):
    from core import aprobaciones
    if hasta < desde:
        raise ErrorPersonal('La fecha final es anterior a la inicial.')
    if Vacacion.objects.filter(trabajador=t, estado__in=['SOLICITADA', 'APROBADA'], tipo__in=['GOCE', 'PERMISO'],
                               fecha_inicio__lte=hasta, fecha_fin__gte=desde).exists():
        raise ErrorPersonal('Ya tiene vacaciones o permisos en esas fechas.')
    v = Vacacion(trabajador=t, tipo=tipo, fecha_inicio=desde, fecha_fin=hasta, observaciones=observaciones[:200],
                 estado='SOLICITADA', solicitado_por=usuario)
    try:
        v.niveles = aprobaciones.calcular_niveles('VACACION', usuario, Decimal(v.dias), t.centro_costo, tipo)
    except aprobaciones.ErrorAprobacion as exc:
        raise ErrorPersonal(str(exc)) from exc
    if not v.niveles:  # sin reglas: el jefe según la jerarquía (o el perfil) o, si no tiene, Planillas
        jefe = jefe_de_trabajador(t)
        jefe_u = jefe.usuario if jefe and jefe.usuario_id else aprobaciones._jefe_de(usuario)
        v.niveles = [{'nivel': 1, 'usuario': jefe_u.pk if jefe_u else None,
                      'nombre': (jefe_u.get_full_name() or jefe_u.username) if jefe_u else 'Planillas',
                      'desde': timezone.now().isoformat()}]
    v.save()
    aprobaciones.registrar('VACACION', v, usuario, 'SOLICITADA', observaciones)
    return v


def puede_aprobar_vacacion(user, v):
    from core import aprobaciones
    return v.estado == 'SOLICITADA' and aprobaciones.puede_aprobar(user, 'VACACION', v.niveles, v.nivel_aprobado,
                                                                    v.solicitado_por_id)


@transaction.atomic
def resolver_vacacion(v, usuario, aprobar_, comentario=''):
    from core import aprobaciones
    if not puede_aprobar_vacacion(usuario, v):
        raise ErrorPersonal('No le corresponde aprobar esta solicitud.')
    if not aprobar_:
        v.estado = 'RECHAZADA'
        v.save(update_fields=['estado'])
        aprobaciones.registrar('VACACION', v, usuario, 'RECHAZADA', comentario)
        return v
    v.nivel_aprobado = aprobaciones.pendiente(v.niveles, v.nivel_aprobado)['nivel']
    aprobaciones.registrar('VACACION', v, usuario, 'APROBADA', comentario, v.nivel_aprobado)
    if aprobaciones.pendiente(v.niveles, v.nivel_aprobado) is None:
        v.estado = 'APROBADA'
    else:
        aprobaciones.avanzar(v.niveles, v.nivel_aprobado)
    v.save(update_fields=['estado', 'niveles', 'nivel_aprobado'])
    return v


# ---------------------------------------------------------------- alertas
def por_vencer(dias=30, hoy=None):
    """(contratos, documentos del legajo) que vencen en los próximos días (o ya vencieron) de trabajadores
    vigentes; solo el último contrato de cada trabajador."""
    hoy = hoy or timezone.localdate()
    limite = hoy + timedelta(days=dias)
    vigentes = Trabajador.objects.exclude(fecha_cese__lt=hoy)
    contratos = [c for c in Contrato.objects.filter(trabajador__in=vigentes, fin__isnull=False, fin__lte=limite,
                                                    renovaciones__isnull=True).select_related('trabajador')]
    documentos = list(DocumentoLegajo.objects.filter(trabajador__in=vigentes, vence__isnull=False,
                                                     vence__lte=limite).select_related('trabajador'))
    return contratos, documentos
