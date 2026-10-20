"""Motor único de aprobación: resuelve quién aprueba cada nivel de un documento (orden de compra, solicitud de pedido,
requerimiento, rendición, vacaciones, movimiento de personal) según reglas configurables, con suplencias
(delegación temporal) y escalamiento automático al jefe del aprobador cuando un nivel pasa N días sin respuesta.

Una regla dice: para tal documento, en tal nivel, aprueba el jefe del solicitante, el responsable del centro de
costo, un usuario, el ocupante de un puesto o el jefe N niveles arriba en la jerarquía, desde tal monto, para tal
centro de costo o tipo. Por nivel aplica la regla más específica (mayor monto). Nadie aprueba lo suyo: su nivel se
salta. Sin reglas para el documento, aprueba cualquier usuario con el permiso del documento (comportamiento
anterior)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

DOCUMENTOS = [('OC', 'Órdenes de compra'), ('SOLPED', 'Solicitudes de pedido'),
              ('REQUERIMIENTO', 'Requerimientos de materiales'), ('RENDICION', 'Rendiciones de gastos'),
              ('VACACION', 'Vacaciones y permisos'), ('MOVIMIENTO', 'Movimientos de personal')]
# permiso que aprueba cada documento cuando no hay reglas (o en un nivel sin usuario)
PERMISOS = {'OC': 'compras.aprobar_oc', 'SOLPED': 'requerimientos.liberar_solped',
            'REQUERIMIENTO': 'requerimientos.aprobar', 'RENDICION': 'rendiciones.aprobar',
            'VACACION': 'planillas.calcular', 'MOVIMIENTO': 'planillas.cerrar'}


class ErrorAprobacion(Exception):
    pass


def _jefe_de(usuario):
    """Jefe del usuario: el de la estructura organizativa (puesto superior) o, si no, el jefe directo del perfil."""
    try:
        from planillas.organizacion import jefe_de_usuario
        jefe = jefe_de_usuario(usuario)
        if jefe is not None:
            return jefe
    except ImportError:
        pass
    from .models import PerfilUsuario
    perfil = PerfilUsuario.objects.filter(usuario=usuario).select_related('jefe').first()
    return perfil.jefe if perfil and perfil.jefe_id else None


def _responsable_centro(centro):
    c, n = centro, 0
    while c is not None and n < 20:
        if c.responsable_usuario_id:
            return c.responsable_usuario
        c, n = c.padre, n + 1
    return None


def _usuario_de_regla(regla, solicitante, centro):
    if regla.tipo in ('JEFE', 'JERARQUIA') and solicitante is None:
        raise ErrorAprobacion('No se sabe qué usuario registró el documento: la regla «jefe del solicitante» no se '
                              'puede aplicar. Use una regla por usuario, puesto o responsable del centro de costo.')
    if regla.tipo == 'USUARIO':
        return regla.aprobador
    if regla.tipo == 'RESPONSABLE_CC':
        usuario = _responsable_centro(centro) if centro is not None else None
        if usuario is None:
            raise ErrorAprobacion(f'El centro de costo {centro or "(sin centro)"} no tiene usuario responsable: '
                                  f'asígnelo en Contabilidad › Centros de costo.')
        return usuario
    if regla.tipo == 'PUESTO':
        from planillas.organizacion import usuario_del_puesto
        usuario = usuario_del_puesto(regla.puesto) if regla.puesto_id else None
        if usuario is None:
            raise ErrorAprobacion(f'El puesto {regla.puesto} no tiene ocupante con usuario del sistema.')
        return usuario
    if regla.tipo == 'JERARQUIA':
        usuario = solicitante
        for _ in range(max(regla.niveles_arriba or 1, 1)):
            usuario = _jefe_de(usuario) if usuario else None
        if usuario is None:
            raise ErrorAprobacion(f'{solicitante} no tiene jefe {regla.niveles_arriba} nivel(es) arriba en la '
                                  f'estructura organizativa.')
        return usuario
    usuario = _jefe_de(solicitante)  # JEFE
    if usuario is None:
        raise ErrorAprobacion(f'{solicitante.get_full_name() or solicitante} no tiene jefe directo: asígnelo en '
                              f'Ajustes › Usuarios o en su puesto de la estructura organizativa.')
    return usuario


def hay_reglas(documento):
    from .models import ReglaAprobacion
    return ReglaAprobacion.objects.filter(documento=documento, activo=True).exists()


def calcular_niveles(documento, solicitante, monto, centro_costo=None, tipo='', urgente=False):
    """[{nivel, usuario, nombre, desde}] según las reglas del documento. [] si ninguna regla aplica."""
    from .models import ReglaAprobacion
    por_nivel, centros = {}, {}
    for regla in ReglaAprobacion.objects.filter(documento=documento, activo=True).select_related(
            'aprobador', 'centro_costo', 'puesto'):
        if monto < regla.monto_desde:
            continue
        if regla.centro_costo_id:
            ids = centros.setdefault(regla.centro_costo_id, regla.centro_costo.descendientes_ids())
            if centro_costo is None or centro_costo.pk not in ids:
                continue
        if regla.aplica_a == 'URGENTE' and not urgente:
            continue
        if regla.aplica_a and regla.aplica_a != 'URGENTE' and regla.aplica_a != tipo:
            continue
        actual = por_nivel.get(regla.nivel)
        if actual is None or regla.monto_desde > actual.monto_desde:
            por_nivel[regla.nivel] = regla
    niveles = []
    ahora = timezone.now().isoformat()
    for nivel in sorted(por_nivel):
        usuario = _usuario_de_regla(por_nivel[nivel], solicitante, centro_costo)
        if solicitante is not None and usuario.pk == solicitante.pk:
            continue  # nadie aprueba lo suyo: su nivel se salta
        if niveles and niveles[-1]['usuario'] == usuario.pk:
            continue
        niveles.append({'nivel': len(niveles) + 1, 'usuario': usuario.pk,
                        'nombre': usuario.get_full_name() or usuario.username, 'desde': ahora if not niveles else None})
    return niveles


def pendiente(niveles, nivel_aprobado):
    for n in niveles or []:
        if n['nivel'] > nivel_aprobado:
            return n
    return None


def avanzar(niveles, nivel_aprobado):
    """Marca desde cuándo espera el siguiente nivel (para el escalamiento)."""
    sig = pendiente(niveles, nivel_aprobado)
    if sig is not None:
        sig['desde'] = timezone.now().isoformat()
    return niveles


# ---------------------------------------------------------------- suplencias y escalamiento
def suplentes_de(usuario, fecha=None):
    """Usuarios que hoy aprueban en lugar del titular (delegación vigente)."""
    from .models import Delegacion
    fecha = fecha or timezone.localdate()
    return [d.suplente for d in Delegacion.objects.filter(titular=usuario, activo=True, desde__lte=fecha,
                                                         hasta__gte=fecha).select_related('suplente')]


def escalado(nivel):
    """¿El nivel lleva más días sin respuesta que el plazo de la empresa? Entonces también aprueba el jefe del
    titular."""
    from .models import Empresa
    dias = Empresa.actual().dias_escalamiento
    if not dias or not nivel.get('desde'):
        return False
    from datetime import datetime
    desde = datetime.fromisoformat(nivel['desde'])
    return timezone.now() - desde >= timedelta(days=dias)


def aprobadores(nivel):
    """Usuarios que pueden aprobar el nivel: titular, sus suplentes vigentes y, si está escalado, su jefe."""
    if not nivel or not nivel.get('usuario'):
        return []
    titular = get_user_model().objects.filter(pk=nivel['usuario']).first()
    if titular is None:
        return []
    usuarios = [titular] + suplentes_de(titular)
    if escalado(nivel) or not titular.is_active:  # titular cesado o sin respuesta: sube al jefe
        jefe = _jefe_de(titular)
        if jefe is not None:
            usuarios.append(jefe)
    return usuarios


def puede_aprobar(user, documento, niveles, nivel_aprobado, solicitante_id=None):
    """Nadie aprueba lo suyo (si hay otro usuario). Nivel con usuario: el titular, su suplente, su jefe si escaló o el
    administrador. Sin niveles o nivel sin usuario: el permiso del documento."""
    from .permisos import puede
    if not user.is_authenticated:
        return False
    if solicitante_id and user.pk == solicitante_id and get_user_model().objects.filter(is_active=True).exclude(
            pk=user.pk).exists():
        return False
    n = pendiente(niveles, nivel_aprobado)
    if niveles and n is None:
        return False
    if n and n.get('usuario'):
        return user.is_superuser or user.pk in {u.pk for u in aprobadores(n)}
    return puede(user, PERMISOS[documento])


def registrar(documento, objeto, usuario, accion, comentario='', nivel=None):
    from .models import HistorialAprobacion
    return HistorialAprobacion.objects.create(
        documento=documento, objeto_id=objeto.pk, descripcion=str(objeto)[:120], accion=accion, nivel=nivel,
        usuario=usuario if usuario and usuario.is_authenticated else None, comentario=(comentario or '')[:300])


def historial(documento, objeto):
    from .models import HistorialAprobacion
    return HistorialAprobacion.objects.filter(documento=documento, objeto_id=objeto.pk).select_related('usuario')


def pendientes_de(user):
    """{documento: n} de lo que el usuario puede aprobar hoy (bandeja de aprobaciones)."""
    from compras.models import OrdenCompra, SolicitudPedido
    from inventario.models import RequerimientoInterno
    res = {}
    for doc, qs, campo in (('OC', OrdenCompra.objects.filter(estado='PENDIENTE').exclude(niveles=[]), 'creado_por_id'),
                           ('SOLPED', SolicitudPedido.objects.filter(estado='ENVIADA'), 'solicitante_id'),
                           ('REQUERIMIENTO', RequerimientoInterno.objects.filter(estado='ENVIADO'), 'solicitante_id')):
        n = sum(1 for o in qs if o.niveles and puede_aprobar(user, doc, o.niveles, o.nivel_aprobado,
                                                              getattr(o, campo, None)))
        if n:
            res[doc] = n
    return res
