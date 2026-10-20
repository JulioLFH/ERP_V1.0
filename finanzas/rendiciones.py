"""Rendición de gastos: flujo Borrador → Enviada → En revisión → Aprobada / Observada / Rechazada → Contabilizada →
Reembolsada o Devuelta, con políticas de gasto, aprobación por niveles y la contabilidad por los auxiliares de
siempre (las facturas a nombre de la empresa van al registro de compras con crédito fiscal y se cancelan con el
fondo; boletas, tickets y movilidad son gastos rendidos contra la entrega a rendir o la caja chica)."""
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import D0, PerfilUsuario, Tercero, r2
from core.permisos import puede

from . import operaciones as op
from .models import (CategoriaGasto, EntregaRendir, GastoRendicion, HistorialRendicion, LineaGasto, PoliticaGasto,
                     ReglaAprobacion, Rendicion)

log = logging.getLogger(__name__)


class ErrorRendicion(Exception):
    pass


# ---------------------------------------------------------------- quién es quién
def perfil(user):
    if user is None or not user.is_authenticated:
        return None
    return PerfilUsuario.objects.filter(usuario=user).select_related('trabajador', 'jefe').first()


def trabajador_de(user):
    p = perfil(user)
    return p.trabajador if p else None


def ve_todo(user):
    """Revisores, aprobadores y tesorería ven todas las rendiciones; el rendidor solo las suyas."""
    return user.is_superuser or any(puede(user, f'rendiciones.{a}') for a in ('aprobar', 'contabilizar', 'tesoreria'))


def visibles(user):
    qs = Rendicion.objects.select_related('entrega__responsable', 'rendidor')
    if ve_todo(user):
        return qs
    t = trabajador_de(user)
    aprueba = [pk for pk, niveles in Rendicion.objects.exclude(estado='BORRADOR').values_list('pk', 'niveles')
               if any(n.get('usuario') == user.pk for n in niveles or [])]  # jefes y aprobadores designados
    filtro = Q(rendidor=user) | Q(pk__in=aprueba)
    if t is not None:
        filtro |= Q(entrega__responsable=t)
    return qs.filter(filtro)


def entregas_para_rendir(user):
    """Entregas abiertas en que el usuario puede rendir: las suyas (o todas si revisa o es administrador)."""
    qs = EntregaRendir.objects.filter(estado='ABIERTA').select_related('responsable')
    if ve_todo(user):
        return qs
    t = trabajador_de(user)
    return qs.filter(responsable=t) if t is not None else qs.none()


# ---------------------------------------------------------------- políticas
def politica_de(tercero):
    """Política del cargo del trabajador (ficha de planillas) o la general."""
    cargo = ''
    if tercero is not None:
        from planillas.models import Trabajador
        cargo = (Trabajador.objects.filter(numero_doc=tercero.numero_doc).values_list('cargo', flat=True).first()
                 or '').strip()
    qs = PoliticaGasto.objects.filter(activa=True)
    return (cargo and qs.filter(cargo__iexact=cargo).first()) or qs.filter(cargo='').first()


def tope_movilidad(fecha):
    """4% de la RMV vigente por día (gastos de movilidad sustentados con planilla)."""
    from planillas.models import Parametro
    try:
        return r2(Parametro.del_anio(fecha.year).rmv * Decimal('0.04'))
    except ValueError:
        return None


def _pen(linea):
    return r2(linea.total * linea.tipo_cambio) if linea.moneda == 'USD' else linea.total


def errores_linea(linea):
    """Datos que faltan o están mal: impiden enviar la rendición."""
    from contabilidad.pcge import exige_centro
    from core.forms import error_ruc
    e = []
    if linea.total <= 0:
        e.append('el total debe ser mayor a cero')
    if linea.tipo_comprobante in LineaGasto.CON_RUC:
        if error_ruc(linea.ruc):
            e.append(f'RUC del proveedor: {error_ruc(linea.ruc)}')
        if not linea.serie or not linea.numero:
            e.append('indique la serie y el número del comprobante')
    if linea.base + linea.igv > linea.total + Decimal('0.05'):
        e.append('base + IGV no puede superar el total')
    e += errores_indicador(linea)
    distribucion = list(linea.distribucion.all()) if linea.pk else []
    if distribucion and sum((d.porcentaje for d in distribucion), D0) != 100:
        e.append('la distribución por centros de costo debe sumar 100%')
    cuenta = linea.categoria.cuenta_en(linea.fecha)
    if not distribucion and not centro_de(linea) and (linea.categoria.exige_centro or exige_centro(cuenta)):
        e.append(f'{linea.categoria} exige centro de costo')
    if linea.con_credito_fiscal and linea.moneda != linea.rendicion.entrega.moneda:
        e.append('una factura con crédito fiscal debe estar en la moneda de la entrega')
    if linea.tipo_comprobante == 'KM' and (linea.kilometros <= 0 or not linea.categoria.tarifa_km):
        e.append('kilometraje: indique los km en una categoría con tarifa por km')
    if linea.categoria.exige_proyecto and not linea.proyecto_id:
        e.append(f'{linea.categoria} exige proyecto')
    if linea.sitio_id and linea.sitio.proyecto_id != linea.proyecto_id:
        e.append('el sitio no pertenece al proyecto')
    if linea.sunat_valido is False:
        e.append(f'SUNAT: {linea.estado_sunat}')
    dup = duplicado(linea)
    if dup:
        e.append(dup)
    return e


TOLERANCIA_IGV = Decimal('0.10')


def errores_indicador(linea):
    """El indicador de impuesto es obligatorio y define el IGV: base × tasa = IGV (tolerancia S/ 0.10)."""
    ind = linea.indicador
    if ind is None:
        return ['falta el indicador de impuesto']
    e = []
    if ind.tasa > 0:
        esperado = r2(linea.base * ind.tasa / 100)
        if abs(esperado - linea.igv) > TOLERANCIA_IGV:
            e.append(f'el IGV ({linea.igv:,.2f}) no es base {linea.base:,.2f} × {ind.tasa}% = {esperado:,.2f}')
    elif linea.igv:
        e.append(f'«{ind.nombre}» no lleva IGV')
    if ind.credito_fiscal and linea.tipo_comprobante != '01':
        e.append('solo una factura da crédito fiscal: use un indicador sin crédito fiscal')
    return e


def sugerir_indicador(tipo_comprobante, a_nombre_empresa=True):
    """Indicador sugerido según el comprobante (el rendidor lo confirma o cambia)."""
    from .models import IndicadorImpuesto
    codigo = ('C1' if a_nombre_empresa else 'S1') if tipo_comprobante == '01' else \
        'S1' if tipo_comprobante in ('03', '12') else 'NG'
    return IndicadorImpuesto.objects.filter(codigo=codigo, activo=True).first()


def duplicado(linea):
    """Mismo proveedor + tipo + serie-número ya rendido (por cualquier persona) o ya en el registro de compras."""
    if linea.tipo_comprobante in ('SC', 'PM') or not linea.numero:
        return ''
    otro = (LineaGasto.objects.filter(ruc=linea.ruc, tipo_comprobante=linea.tipo_comprobante, serie=linea.serie,
                                      numero=linea.numero).exclude(estado='RECHAZADA').exclude(pk=linea.pk)
            .exclude(rendicion__estado='RECHAZADA').select_related('rendicion').first())
    if otro is not None:
        return f'el comprobante ya fue rendido en {otro.rendicion.numero}'
    if linea.ruc and linea.tipo_comprobante in ('01', '02', '03'):
        from compras.models import Compra
        existe = Compra.objects.filter(tercero__numero_doc=linea.ruc, tipo_comprobante=linea.tipo_comprobante,
                                       serie=linea.serie, numero=linea.numero).exclude(estado='ANULADO')
        if linea.compra_id:
            existe = existe.exclude(pk=linea.compra_id)
        if existe.exists():
            return 'el comprobante ya está en el registro de compras'
    return ''


def evaluar(rendicion, guardar=True):
    """Marca en cada línea lo que sale de la política (tope, sin comprobante, fuera de plazo, no permitida) y
    devuelve las alertas de toda la rendición (tope por rendición)."""
    entrega = rendicion.entrega
    pol = politica_de(entrega.responsable)
    lineas = list(rendicion.lineas.exclude(estado='RECHAZADA').select_related('categoria'))
    alertas = defaultdict(list)
    hoy = timezone.localdate()
    por_cat_dia, por_dia, movilidad = defaultdict(list), defaultdict(list), defaultdict(list)
    no_permitidas = set(pol.no_permitidas.values_list('pk', flat=True)) if pol else set()
    for l in lineas:
        pen = _pen(l)
        cat = l.categoria
        if cat.pk in no_permitidas:
            alertas[l.pk].append('categoría no permitida para su cargo')
        if l.sin_comprobante and (cat.exige_comprobante or (pol and pol.comprobante_obligatorio)):
            alertas[l.pk].append('sin comprobante')
        if l.ruc_estado and any(m in l.ruc_estado for m in ('BAJA', 'SUSPENSION', 'NO HABIDO', 'NO HALLADO')):
            alertas[l.pk].append(f'proveedor con {l.ruc_estado}')
        if cat.tope and pen > cat.tope:
            alertas[l.pk].append(f'supera el tope por gasto de {cat} (S/ {cat.tope:,.2f})')
        if pol and pol.dias_para_rendir:
            limite = max(entrega.fecha, l.fecha) + timedelta(days=pol.dias_para_rendir)
            if (rendicion.enviada_en.date() if rendicion.enviada_en else hoy) > limite:
                alertas[l.pk].append(f'fuera de plazo (debía rendirse hasta el {limite:%d/%m/%Y})')
        por_cat_dia[(cat.pk, l.fecha)].append((l, pen))
        por_dia[l.fecha].append((l, pen))
        if cat.es_movilidad:
            movilidad[l.fecha].append((l, pen))
    for (_, fecha), grupo in por_cat_dia.items():
        cat = grupo[0][0].categoria
        if cat.tope_diario and sum(p for _, p in grupo) > cat.tope_diario:
            for l, _ in grupo:
                alertas[l.pk].append(f'{cat} supera su tope diario de S/ {cat.tope_diario:,.2f}')
    for fecha, grupo in movilidad.items():
        tope = tope_movilidad(fecha)
        if tope and sum(p for _, p in grupo) > tope:
            for l, _ in grupo:
                alertas[l.pk].append(f'movilidad del día supera el 4% de la RMV (S/ {tope:,.2f})')
    if pol and pol.tope_diario:
        for fecha, grupo in por_dia.items():
            if sum(p for _, p in grupo) > pol.tope_diario:
                for l, _ in grupo:
                    alertas[l.pk].append(f'gastos del día superan el tope diario (S/ {pol.tope_diario:,.2f})')
    generales = []
    total_pen = sum((_pen(l) for l in lineas), D0)
    if pol and pol.tope_por_rendicion and total_pen > pol.tope_por_rendicion:
        generales.append(f'El total (S/ {total_pen:,.2f}) supera el tope por rendición de la política '
                         f'«{pol.nombre}» (S/ {pol.tope_por_rendicion:,.2f}).')
    if guardar:
        for l in lineas:
            texto = '; '.join(dict.fromkeys(alertas.get(l.pk, [])))[:400]
            if l.alertas != texto:
                LineaGasto.objects.filter(pk=l.pk).update(alertas=texto)
                l.alertas = texto
    return generales


# ---------------------------------------------------------------- aprobadores
def _otro_usuario(user):
    return get_user_model().objects.filter(is_active=True).exclude(pk=user.pk).exists()


def calcular_niveles(rendicion):
    """[{nivel, usuario, nombre}] según las reglas activas. Por nivel aplica la regla más específica (mayor monto).
    Sin reglas: un nivel que aprueba cualquier usuario con el permiso «Aprobar rendiciones»."""
    total_pen = sum((_pen(l) for l in rendicion.lineas.exclude(estado='RECHAZADA')), D0)
    from core import aprobaciones
    if aprobaciones.hay_reglas('RENDICION'):  # motor único de aprobación (Ajustes › Aprobaciones)
        try:
            niveles = aprobaciones.calcular_niveles('RENDICION', rendicion.rendidor, total_pen,
                                                    rendicion.entrega.centro_costo)
        except aprobaciones.ErrorAprobacion as exc:
            raise ErrorRendicion(str(exc)) from exc
        return niveles or [{'nivel': 1, 'usuario': None, 'nombre': 'Aprobador de rendiciones'}]
    centros = set(rendicion.lineas.exclude(centro_costo=None).values_list('centro_costo_id', flat=True))
    por_nivel = {}
    for regla in ReglaAprobacion.objects.filter(activo=True).select_related('aprobador', 'centro_costo'):
        if total_pen < regla.monto_desde:
            continue
        if regla.centro_costo_id and not (centros & set(regla.centro_costo.descendientes_ids())):
            continue
        actual = por_nivel.get(regla.nivel)
        if actual is None or regla.monto_desde > actual.monto_desde:
            por_nivel[regla.nivel] = regla
    niveles = []
    for nivel in sorted(por_nivel):
        regla = por_nivel[nivel]
        if regla.tipo == 'JEFE':
            p = perfil(rendicion.rendidor)
            usuario = p.jefe if p else None
            if usuario is None:
                raise ErrorRendicion(f'{rendicion.rendidor.get_full_name() or rendicion.rendidor} no tiene jefe '
                                     f'directo: asígnelo en Ajustes › Usuarios.')
        else:
            usuario = regla.aprobador
        if usuario.pk == rendicion.rendidor_id:
            continue  # nadie aprueba su propia rendición: pasa al siguiente nivel
        if niveles and niveles[-1]['usuario'] == usuario.pk:
            continue
        niveles.append({'nivel': len(niveles) + 1, 'usuario': usuario.pk,
                        'nombre': usuario.get_full_name() or usuario.username})
    return niveles or [{'nivel': 1, 'usuario': None, 'nombre': 'Aprobador de rendiciones'}]


def puede_aprobar(user, rendicion):
    if rendicion.estado not in Rendicion.POR_APROBAR or not user.is_authenticated:
        return False
    if user.pk == rendicion.rendidor_id and _otro_usuario(user):
        return False  # segregación de funciones: nadie aprueba su propia rendición
    n = rendicion.nivel_pendiente
    if n is None:
        return False
    if n['usuario']:  # el titular, su suplente vigente o su jefe si el nivel escaló (motor único)
        from core.aprobaciones import aprobadores
        return user.is_superuser or user.pk in {u.pk for u in aprobadores(n)}
    return puede(user, 'rendiciones.aprobar')


# ---------------------------------------------------------------- flujo
def _historial(r, usuario, de, a, comentario='', nivel=None):
    HistorialRendicion.objects.create(rendicion=r, usuario=usuario if usuario and usuario.is_authenticated else None,
                                      de_estado=de, a_estado=a, comentario=(comentario or '')[:300], nivel=nivel)


def _cambiar(r, estado, usuario, comentario='', nivel=None, **campos):
    de = r.estado
    r.estado = estado
    for k, v in campos.items():
        setattr(r, k, v)
    r.save()
    _historial(r, usuario, de, estado, comentario, nivel)


def _motivo(valor, comentario):
    """Motivo estándar de la observación (catálogo); sin motivo pero con comentario: «Otro motivo»."""
    from .models import MotivoObservacion
    m = None
    if valor:
        m = (MotivoObservacion.objects.filter(pk=valor).first() if str(valor).isdigit()
             else MotivoObservacion.objects.filter(codigo=valor).first())
    if m is None and len((comentario or '').strip()) >= 3:
        m = MotivoObservacion.objects.filter(codigo='OTRO').first()
    if m is None:
        raise ErrorRendicion('Elija el motivo de la observación.')
    if m.exige_comentario and len((comentario or '').strip()) < 3:
        raise ErrorRendicion(f'«{m.nombre}»: escriba el comentario.')
    return m


def _registrar_observacion(r, linea, estado, motivo, comentario, usuario):
    from .models import ObservacionLinea
    ObservacionLinea.objects.create(
        rendicion=r, linea=linea, documento=(linea.documento or linea.glosa)[:60] if linea else '', estado=estado,
        motivo=motivo, comentario=(comentario or '')[:250], rendidor=r.rendidor,
        centro_costo=(linea.centro_costo if linea and linea.centro_costo_id else r.entrega.centro_costo),
        usuario=usuario if usuario and usuario.is_authenticated else None)


def _exigir_editable(r, usuario):
    if not r.editable:
        raise ErrorRendicion(f'La rendición está {r.get_estado_display().lower()}: ya no se modifica.')
    if usuario.pk != r.rendidor_id and not usuario.is_superuser:
        raise ErrorRendicion('Solo quien registró la rendición puede modificarla.')


@transaction.atomic
def crear(entrega, usuario, descripcion, fecha=None):
    if entrega.estado != 'ABIERTA':
        raise ErrorRendicion(f'{entrega.numero} está liquidada.')
    if not entregas_para_rendir(usuario).filter(pk=entrega.pk).exists():
        raise ErrorRendicion('Solo puede rendir sus propias entregas a rendir o cajas chicas.')
    r = Rendicion.objects.create(entrega=entrega, rendidor=usuario, descripcion=descripcion[:200],
                                 fecha=fecha or timezone.localdate())
    _historial(r, usuario, '', 'BORRADOR', 'Rendición creada')
    return r


def guardar_linea(r, linea, usuario):
    _exigir_editable(r, usuario)
    linea.rendicion = r
    dup = duplicado(linea)
    if dup:  # se bloquea al registrarlo (también desde la app, WhatsApp, correo o la API)
        raise ErrorRendicion(f'Comprobante repetido: {dup}.')
    if linea.estado == 'OBSERVADA':
        linea.estado, linea.motivo = 'PENDIENTE', None  # corregida por el rendidor
    linea.save()
    return linea


def quitar_linea(r, linea, usuario):
    _exigir_editable(r, usuario)
    linea.delete()


def enviar(r, usuario, comentario=''):
    _exigir_editable(r, usuario)
    from .comprobantes import ErrorLectura, validar_en_sunat
    # comprobantes electrónicos sin validar: se consultan en SUNAT (si hay credenciales); el resultado queda guardado
    # aunque el envío se rechace
    for l in r.lineas.exclude(estado='RECHAZADA').filter(validado_en__isnull=True):
        try:
            validar_en_sunat(l)
        except ErrorLectura as exc:
            log.info('Validación SUNAT omitida (%s): %s', l.documento, exc)
    return _enviar(r, usuario, comentario)


@transaction.atomic
def _enviar(r, usuario, comentario):
    lineas = list(r.lineas.exclude(estado='RECHAZADA').select_related('categoria__cuenta_contable', 'rendicion__entrega',
                                                                       'sitio'))
    if not lineas:
        raise ErrorRendicion('Agregue al menos un gasto.')
    errores = [f'{l.documento or l.glosa}: {"; ".join(errores_linea(l))}' for l in lineas if errores_linea(l)]
    if errores:
        raise ErrorRendicion('Corrija antes de enviar — ' + ' | '.join(errores))
    generales = evaluar(r)
    sin_justificar = [l for l in r.lineas.exclude(estado='RECHAZADA') if l.alertas and not l.justificacion.strip()]
    if sin_justificar:
        raise ErrorRendicion(f'{len(sin_justificar)} gasto(s) salen de la política: escriba la justificación de '
                             f'cada uno antes de enviar.')
    if generales and len((comentario or '').strip()) < 5:
        raise ErrorRendicion(generales[0] + ' Justifíquelo en el comentario del envío.')
    r.lineas.filter(estado__in=['OBSERVADA', 'APROBADA']).update(estado='PENDIENTE', comentario='')
    niveles = calcular_niveles(r)
    _cambiar(r, 'ENVIADA', usuario, comentario or 'Enviada para aprobación', niveles=niveles, nivel_aprobado=0,
             enviada_en=timezone.now())
    notificar(r, 'enviada')
    return r


@transaction.atomic
def aprobar(r, usuario, comentario='', decisiones=None):
    """decisiones: {linea_id: (estado, comentario[, motivo])} con estado APROBADA, OBSERVADA o RECHAZADA, para
    aprobar línea por línea; observar o rechazar exige un motivo del catálogo. Si alguna línea queda observada, la
    rendición vuelve al rendidor."""
    if not puede_aprobar(usuario, r):
        raise ErrorRendicion('No le corresponde aprobar esta rendición (nivel, permiso o es su propia rendición).')
    nivel = r.nivel_pendiente['nivel']
    for linea in r.lineas.exclude(estado='RECHAZADA'):
        decision = (decisiones or {}).get(linea.pk, ('APROBADA', ''))
        estado, nota = decision[0], (decision[1] or '').strip()
        motivo = None
        if estado in ('OBSERVADA', 'RECHAZADA'):
            try:
                motivo = _motivo(decision[2] if len(decision) > 2 else None, nota)
            except ErrorRendicion as exc:
                raise ErrorRendicion(f'{linea.documento or linea.glosa}: {exc}')
            _registrar_observacion(r, linea, estado, motivo, nota, usuario)
        linea.estado = 'PENDIENTE' if estado == 'APROBADA' else estado
        linea.comentario, linea.motivo = nota[:250], motivo
        linea.save(update_fields=['estado', 'comentario', 'motivo'])
    if r.lineas.filter(estado='OBSERVADA').exists():
        _cambiar(r, 'OBSERVADA', usuario, comentario or 'Gastos observados: corríjalos y vuelva a enviar', nivel)
        notificar(r, 'observada')
        return r
    if not r.lineas.exclude(estado='RECHAZADA').exists():
        _cambiar(r, 'RECHAZADA', usuario, comentario or 'Todos los gastos fueron rechazados', nivel)
        notificar(r, 'rechazada')
        return r
    r.nivel_aprobado = nivel
    if r.nivel_pendiente is None:
        r.lineas.filter(estado='PENDIENTE').update(estado='APROBADA')
        _cambiar(r, 'APROBADA', usuario, comentario or f'Aprobada (nivel {nivel})', nivel, aprobada_en=timezone.now())
        notificar(r, 'aprobada')
    else:
        _cambiar(r, 'EN_REVISION', usuario, comentario or f'Aprobada en el nivel {nivel}', nivel)
        notificar(r, 'enviada')
    return r


@transaction.atomic
def observar(r, usuario, comentario, motivo=None):
    if not puede_aprobar(usuario, r):
        raise ErrorRendicion('No le corresponde revisar esta rendición.')
    m = _motivo(motivo, comentario)
    if len((comentario or '').strip()) < 5 and not motivo:
        raise ErrorRendicion('Indique qué debe corregir el rendidor.')
    _registrar_observacion(r, None, 'OBSERVADA', m, comentario, usuario)
    _cambiar(r, 'OBSERVADA', usuario, f'{m.nombre}: {comentario}'.strip(': '), r.nivel_pendiente['nivel'])
    notificar(r, 'observada')


@transaction.atomic
def rechazar(r, usuario, comentario, motivo=None):
    if not puede_aprobar(usuario, r):
        raise ErrorRendicion('No le corresponde revisar esta rendición.')
    m = _motivo(motivo, comentario)
    if len((comentario or '').strip()) < 5 and not motivo:
        raise ErrorRendicion('Indique el motivo del rechazo.')
    _registrar_observacion(r, None, 'RECHAZADA', m, comentario, usuario)
    nivel = r.nivel_pendiente['nivel']
    r.lineas.update(estado='RECHAZADA')
    _cambiar(r, 'RECHAZADA', usuario, f'{m.nombre}: {comentario}'.strip(': '), nivel)
    notificar(r, 'rechazada')


# ---------------------------------------------------------------- contabilidad
def _proveedor(ruc, nombre):
    t = Tercero.objects.filter(numero_doc=ruc).first()
    if t is None:
        t = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc=ruc, nombre=(nombre or ruc)[:200])
    elif t.tipo == 'CLIENTE':
        Tercero.objects.filter(pk=t.pk).update(tipo='AMBOS')
    return t


def _registrar_factura(linea, entrega, fecha, usuario):
    """Factura a nombre de la empresa: registro de compras 8.1 con crédito fiscal, cancelada con el fondo."""
    from compras.models import Compra, CompraItem
    proveedor = _proveedor(linea.ruc, linea.razon_social)
    if Compra.objects.filter(tercero=proveedor, tipo_comprobante='01', serie=linea.serie.upper(),
                             numero=linea.numero).exists():
        raise ErrorRendicion(f'La factura {linea.documento} de {proveedor.nombre} ya está en el registro de compras.')
    c = Compra(tercero=proveedor, tipo_comprobante='01', serie=linea.serie.upper(), numero=linea.numero,
               fecha_emision=linea.fecha, periodo=fecha.strftime('%Y%m'), moneda=linea.moneda,
               tipo_cambio=linea.tipo_cambio if linea.moneda == 'USD' else Decimal('1'), clasificacion='GASTO',
               cuenta_contable=linea.categoria.cuenta_en(linea.fecha), centro_costo=centro_de(linea),
               ingresar_almacen=False, forma_pago='CONTADO',
               glosa=f'Rendición {linea.rendicion.numero}: {linea.glosa}'[:500])
    c.save()
    CompraItem.objects.create(documento=c, descripcion=linea.glosa[:250], cantidad=1, precio_unitario=linea.base)
    resto = linea.total - linea.base - linea.igv
    if resto > 0:
        CompraItem.objects.create(documento=c, descripcion='No gravado', cantidad=1, precio_unitario=resto,
                                  afectacion='INAFECTA')
    c.calcular_totales()
    c.igv, c.total = linea.igv, linea.total  # los importes del comprobante (p. ej. IGV de 10% en restaurantes)
    c.save()
    op.rendir_compra(entrega, Compra.objects.con_saldos().get(pk=c.pk), linea.total, fecha, usuario)
    return c


_DOCUMENTO_GASTO = {'03': '03', '12': '12', 'PM': 'PM', 'SC': 'RI', 'KM': 'RI'}


def centro_de(linea):
    """Centro de costo del gasto; si no tiene, el de su proyecto (o el primero de su distribución)."""
    centro = linea.centro_costo or (linea.proyecto.centro_costo if linea.proyecto_id else None)
    if centro is None and linea.pk:
        d = linea.distribucion.select_related('centro_costo').first()
        centro = d.centro_costo if d else None
    return centro


def _registrar_gasto(linea, entrega, fecha, usuario):
    """Gasto rendido (sin crédito fiscal); si está distribuido, uno por centro de costo (una línea del asiento por
    centro)."""
    from contabilidad.centralizar import _repartir
    detalle = f' · {linea.proyecto.codigo}' if linea.proyecto_id else ''
    if linea.sitio_id:
        detalle += f'/{linea.sitio.codigo}'
    if linea.tipo_comprobante == 'KM':
        detalle += f' · {linea.kilometros} km'
    distribucion = list(linea.distribucion.select_related('centro_costo'))
    partes = (_repartir(linea.importe, {d: d.porcentaje for d in distribucion}) if distribucion
              else [(None, linea.importe)])
    primero = None
    for d, monto in partes:
        g = GastoRendicion(fecha=fecha, tipo_documento=_DOCUMENTO_GASTO.get(linea.tipo_comprobante, '00'),
                           numero_documento=linea.documento[:30],
                           proveedor=f'{linea.ruc} {linea.razon_social}'.strip()[:150],
                           descripcion=f'{linea.glosa} ({linea.fecha:%d/%m/%Y}){detalle}'
                                       f'{f" · {d.porcentaje}%" if d else ""}'[:200],
                           cuenta_contable=linea.categoria.cuenta_en(linea.fecha),
                           centro_costo=d.centro_costo if d else centro_de(linea), monto=monto)
        g = op.rendir_gasto(entrega, g, usuario)
        if d:
            d.importe, d.gasto = monto, g
            d.save(update_fields=['importe', 'gasto'])
        primero = primero or g
    return primero


def _vincular_sustentos(linea, *destinos):
    """La foto, PDF o XML del gasto queda también en el registro de compras o el gasto rendido (y su asiento)."""
    from core.sustentos import de, vincular
    for s in de(linea):
        for obj in destinos:
            if obj is not None:
                vincular(obj, s.archivo, s.usuario, s.descripcion or 'Comprobante de la rendición')


# ---------------------------------------------------------------- distribución en varios centros de costo
def distribuir(linea, filas, usuario=None):
    """filas: [(centro de costo, porcentaje)] que suman 100. Reemplaza la distribución del gasto ([] la quita)."""
    from contabilidad.centralizar import _repartir
    from .models import DistribucionLinea
    _exigir_editable(linea.rendicion, usuario or linea.rendicion.rendidor)
    filas = [(c, Decimal(str(p))) for c, p in filas if c is not None and p]
    if filas:
        if len({c.pk for c, _ in filas}) != len(filas):
            raise ErrorRendicion('Un centro de costo está repetido en la distribución.')
        if any(p <= 0 for _, p in filas) or sum((p for _, p in filas), D0) != 100:
            raise ErrorRendicion('Los porcentajes de la distribución deben ser positivos y sumar 100%.')
    with transaction.atomic():
        linea.distribucion.all().delete()
        pesos = {i: p for i, (_, p) in enumerate(filas)}
        importes = dict(_repartir(linea.importe, pesos)) if filas else {}
        for i, (centro, p) in enumerate(filas):
            DistribucionLinea.objects.create(linea=linea, centro_costo=centro, porcentaje=p, importe=importes[i])
        if filas:
            LineaGasto.objects.filter(pk=linea.pk).update(centro_costo=None)
    return linea


def aplicar_plantilla(rendicion, plantilla, usuario=None, lineas=None):
    """Reparte con la plantilla todos los gastos de la rendición (fondo fijo) o los indicados."""
    filas = [(p.centro_costo, p.porcentaje) for p in plantilla.lineas.select_related('centro_costo')]
    n = 0
    for linea in (lineas or rendicion.lineas.exclude(estado='RECHAZADA')):
        distribuir(linea, filas, usuario)
        n += 1
    return n


# ---------------------------------------------------------------- corte del mes
def fecha_corte(fecha):
    """Día de corte de rendiciones del mes de la fecha (None si no hay corte configurado)."""
    import calendar
    from .models import ConfigRendicion
    dia = ConfigRendicion.actual().dia_corte
    if not dia:
        return None
    return fecha.replace(day=min(dia, calendar.monthrange(fecha.year, fecha.month)[1]))


def proximo_corte(hoy=None):
    hoy = hoy or timezone.localdate()
    corte = fecha_corte(hoy)
    if corte is None or corte >= hoy:
        return corte
    siguiente = (hoy.replace(day=1) + timedelta(days=32)).replace(day=1)
    return fecha_corte(siguiente)


def fecha_contable_sugerida(r):
    """Lo aprobado hasta el corte se contabiliza en su mes; lo aprobado después, el primer día del mes siguiente."""
    hoy = timezone.localdate()
    base = timezone.localtime(r.aprobada_en).date() if r.aprobada_en else hoy
    corte = fecha_corte(base)
    objetivo = (base.replace(day=1) + timedelta(days=32)).replace(day=1) if corte and base > corte else base
    # dentro del mismo mes, la fecha de hoy; si el mes ya pasó, la fecha que le corresponde
    return hoy if hoy > objetivo and hoy.strftime('%Y%m') == objetivo.strftime('%Y%m') else objetivo


def avisos_de_corte(hoy=None):
    """[(usuario, texto)] para recordar el corte: rendidores con borradores u observadas y aprobadores con
    pendientes, desde N días antes del corte."""
    from .models import ConfigRendicion
    hoy = hoy or timezone.localdate()
    cfg = ConfigRendicion.actual()
    corte = proximo_corte(hoy)
    if corte is None or (corte - hoy).days > cfg.dias_aviso_corte:
        return corte, []
    avisos = {}
    for r in Rendicion.objects.filter(estado__in=Rendicion.EDITABLES).select_related('rendidor'):
        avisos.setdefault(r.rendidor, []).append(f'{r.numero} ({r.get_estado_display().lower()}): envíela')
    User = get_user_model()
    for r in Rendicion.objects.filter(estado__in=Rendicion.POR_APROBAR):
        n = r.nivel_pendiente
        aprobadores = (User.objects.filter(pk=n['usuario']) if n and n['usuario'] else
                       [u for u in User.objects.filter(is_active=True) if puede(u, 'rendiciones.aprobar')])
        for u in aprobadores:
            if u.pk != r.rendidor_id:
                avisos.setdefault(u, []).append(f'{r.numero}: por aprobar')
    return corte, list(avisos.items())


# ---------------------------------------------------------------- historial de imputación de las categorías
def cambiar_cuenta_categoria(categoria, cuenta, vigente_desde, usuario, motivo=''):
    """Cambia la cuenta contable de la categoría desde una fecha (los gastos anteriores conservan la cuenta que
    tenían) y avisa a contabilidad."""
    from .models import CategoriaCuentaHistorial
    anterior = categoria.cuenta_en(vigente_desde - timedelta(days=1))
    if anterior == cuenta:
        return None
    h = CategoriaCuentaHistorial.objects.create(categoria=categoria, cuenta_anterior=anterior, cuenta=cuenta,
                                                vigente_desde=vigente_desde, motivo=motivo[:250],
                                                usuario=usuario if usuario and usuario.is_authenticated else None)
    type(categoria).objects.filter(pk=categoria.pk).update(cuenta_contable=cuenta)
    _avisar_contabilidad(h)
    return h


def _avisar_contabilidad(h):
    from core.correo import ErrorCorreo, enviar as enviar_correo
    correos = [u.email for u in get_user_model().objects.filter(is_active=True)
               if u.email and puede(u, 'rendiciones.contabilizar')]
    if not correos:
        return
    try:
        enviar_correo(correos, f'Cambio de imputación: {h.categoria}', 'finanzas/correo_imputacion.html', {'h': h})
    except ErrorCorreo as exc:
        log.info('Aviso de cambio de imputación no enviado: %s', exc)


# ---------------------------------------------------------------- trazabilidad y errores
def trazabilidad(desde, hasta):
    """Por rendición: cada paso de aprobación con aprobador, nivel, fecha y hora, horas desde el paso anterior y
    el último aprobador."""
    salida = []
    qs = (Rendicion.objects.filter(enviada_en__date__range=[desde, hasta])
          .select_related('entrega__responsable', 'rendidor').prefetch_related('historial__usuario'))
    for r in qs.order_by('enviada_en'):
        pasos, anterior, ultimo = [], None, None
        for h in r.historial.all():
            if h.a_estado == 'ENVIADA':
                anterior = h.fecha
                continue
            if h.a_estado in ('EN_REVISION', 'APROBADA', 'OBSERVADA', 'RECHAZADA') and anterior:
                horas = round((h.fecha - anterior).total_seconds() / 3600, 1)
                pasos.append({'h': h, 'horas': horas})
                anterior = h.fecha
                if h.a_estado in ('EN_REVISION', 'APROBADA'):
                    ultimo = h.usuario
        if pasos:
            salida.append({'r': r, 'pasos': pasos, 'ultimo': ultimo,
                           'total_horas': round(sum(p['horas'] for p in pasos), 1)})
    return salida


def errores_por(desde, hasta):
    """Observaciones y rechazos por motivo, por usuario (rendidor) y por área (centro de costo)."""
    from .models import ObservacionLinea
    obs = list(ObservacionLinea.objects.filter(fecha__date__range=[desde, hasta])
               .select_related('motivo', 'rendidor', 'centro_costo'))

    def contar(clave):
        d = defaultdict(int)
        for o in obs:
            d[clave(o)] += 1
        return sorted(d.items(), key=lambda x: -x[1])
    return {'total': len(obs), 'filas': obs,
            'por_motivo': contar(lambda o: o.motivo.nombre),
            'por_usuario': contar(lambda o: (o.rendidor.get_full_name() or o.rendidor.username) if o.rendidor
                                  else '—'),
            'por_area': contar(lambda o: f'{o.centro_costo.codigo} {o.centro_costo.nombre}' if o.centro_costo
                               else 'Sin centro de costo')}


def sustentos_de_asiento(a):
    """Sustentos de los documentos de origen del asiento (compra o gastos rendidos), sin carga manual."""
    from core.sustentos import de
    from .models import GastoRendicion as Gasto
    vistos, salida = set(), []
    origenes = []
    if a.compra_id:
        origenes.append(a.compra)
    if a.origen == 'APLICACION' and a.glosa.startswith('Rendición '):
        numero = a.glosa.split()[1]
        origenes += list(Gasto.objects.filter(entrega__numero=numero, fecha__year=a.fecha.year,
                                              fecha__month=a.fecha.month))
    for obj in origenes:
        for s in de(obj):
            if s.archivo_id not in vistos:
                vistos.add(s.archivo_id)
                salida.append(s)
    return salida


@transaction.atomic
def contabilizar(r, usuario, fecha=None):
    """Revisor contable: facturas al registro de compras (con crédito fiscal) y el resto como gastos rendidos;
    el asiento sale de la centralización (gasto y centro de costo contra la entrega a rendir o caja chica)."""
    if r.estado != 'APROBADA':
        raise ErrorRendicion('Solo se contabilizan rendiciones aprobadas.')
    fecha = fecha or fecha_contable_sugerida(r)
    try:
        op._periodo_abierto(fecha)
    except op.ErrorTesoreria as exc:
        raise ErrorRendicion(str(exc))
    entrega = r.entrega
    if entrega.estado != 'ABIERTA':
        raise ErrorRendicion(f'{entrega.numero} está liquidada.')
    for linea in r.lineas.filter(estado='APROBADA').select_related('categoria__cuenta_contable', 'centro_costo',
                                                                   'proyecto__centro_costo', 'sitio'):
        try:
            if linea.con_credito_fiscal:
                linea.compra = _registrar_factura(linea, entrega, fecha, usuario)
                _vincular_sustentos(linea, linea.compra)
            else:
                linea.gasto = _registrar_gasto(linea, entrega, fecha, usuario)
                _vincular_sustentos(linea, *[d.gasto for d in linea.distribucion.all()] or [linea.gasto])
        except op.ErrorTesoreria as exc:
            raise ErrorRendicion(f'{linea.documento or linea.glosa}: {exc}')
        linea.save(update_fields=['compra', 'gasto'])
    _cambiar(r, 'CONTABILIZADA', usuario, f'Contabilizada al {fecha:%d/%m/%Y}', contabilizada_en=timezone.now(),
             fecha_contable=fecha)
    return r


# ---------------------------------------------------------------- tesorería
def _exigir_contabilizada(r):
    if r.estado != 'CONTABILIZADA':
        raise ErrorRendicion('Primero contabilice la rendición.')


@transaction.atomic
def reembolsar(r, usuario, cuenta, fecha, numero_operacion=''):
    """El trabajador gastó más de lo entregado: la empresa le paga la diferencia."""
    _exigir_contabilizada(r)
    saldo = r.entrega.saldo
    if saldo >= 0:
        raise ErrorRendicion('No hay nada por reembolsar: el responsable no gastó más de lo entregado.')
    try:
        m = op.movimiento_fondo(r.entrega, 'reembolsar', cuenta, -saldo, fecha, usuario, numero_operacion)
    except op.ErrorTesoreria as exc:
        raise ErrorRendicion(str(exc))
    _cambiar(r, 'REEMBOLSADA', usuario, f'Reembolso {m.voucher} por {r.simbolo} {-saldo:,.2f}')
    notificar(r, 'reembolsada')
    return m


@transaction.atomic
def devolver(r, usuario, cuenta, fecha, numero_operacion=''):
    """El trabajador gastó menos: devuelve el saldo (ingreso a caja o banco)."""
    _exigir_contabilizada(r)
    saldo = r.entrega.saldo
    if saldo <= 0:
        raise ErrorRendicion('No hay saldo por devolver.')
    try:
        m = op.movimiento_fondo(r.entrega, 'devolver', cuenta, saldo, fecha, usuario, numero_operacion)
    except op.ErrorTesoreria as exc:
        raise ErrorRendicion(str(exc))
    _cambiar(r, 'DEVUELTA', usuario, f'Devolución {m.voucher} por {r.simbolo} {saldo:,.2f}')
    return m


@transaction.atomic
def descontar_planilla(r, usuario, fecha=None):
    """El saldo no devuelto pasa a cuenta por cobrar al trabajador (la del concepto «Adelanto» de planillas) para
    descontarlo en su próxima planilla."""
    _exigir_contabilizada(r)
    entrega, fecha = r.entrega, fecha or timezone.localdate()
    saldo = entrega.saldo
    if saldo <= 0:
        raise ErrorRendicion('No hay saldo por descontar.')
    if entrega.moneda != 'PEN':
        raise ErrorRendicion('El descuento por planilla es en soles: registre la devolución.')
    from planillas.models import ConceptoPlanilla
    concepto = ConceptoPlanilla.objects.filter(clave='ADELANTO').select_related('cuenta').first()
    if concepto is None or concepto.cuenta is None:
        raise ErrorRendicion('Configure la cuenta del concepto «Adelanto» en Planillas › Parámetros, AFP y conceptos.')
    g = GastoRendicion(fecha=fecha, tipo_documento='RI', numero_documento=r.numero,
                       descripcion=f'Saldo no rendido: descuento por planilla ({r.numero})',
                       cuenta_contable=concepto.cuenta, monto=saldo)
    try:
        op.rendir_gasto(entrega, g, usuario)
    except op.ErrorTesoreria as exc:
        raise ErrorRendicion(str(exc))
    _cambiar(r, 'DEVUELTA', usuario, f'Por descontar en planilla S/ {saldo:,.2f} (regístrelo como adelanto)',
             descuento_planilla=saldo)
    return g


@transaction.atomic
def reponer_caja(entrega, usuario, cuenta, fecha, numero_operacion=''):
    """Caja chica en un paso: contabiliza las rendiciones aprobadas de la caja y repone el fondo fijo."""
    if entrega.tipo != 'CAJA_CHICA':
        raise ErrorRendicion('La reposición es solo para cajas chicas.')
    aprobadas = list(entrega.rendiciones.filter(estado='APROBADA'))
    for r in aprobadas:
        contabilizar(r, usuario, fecha)
    monto = EntregaRendir.objects.get(pk=entrega.pk).por_reponer
    if monto <= 0:
        raise ErrorRendicion('No hay nada por reponer en la caja chica.')
    try:
        m = op.movimiento_fondo(entrega, 'reponer', cuenta, monto, fecha, usuario, numero_operacion)
    except op.ErrorTesoreria as exc:
        raise ErrorRendicion(str(exc))
    for r in entrega.rendiciones.filter(estado='CONTABILIZADA'):
        _cambiar(r, 'REEMBOLSADA', usuario, f'Caja chica repuesta con {m.voucher}')
    return m, monto, len(aprobadas)


# ---------------------------------------------------------------- avisos
_ASUNTOS = {'enviada': 'Rendición por aprobar', 'observada': 'Rendición observada',
            'rechazada': 'Rendición rechazada', 'aprobada': 'Rendición aprobada',
            'reembolsada': 'Reembolso de gastos', 'recordatorio': 'Entrega a rendir vencida'}


def destinatarios(r, evento):
    User = get_user_model()
    if evento == 'enviada':
        n = r.nivel_pendiente
        if n and n['usuario']:
            return list(User.objects.filter(pk=n['usuario']))
        return []
    return [r.rendidor]


def notificar(r, evento):
    """Correo al siguiente aprobador o al rendidor. Si el correo no está configurado no interrumpe el flujo."""
    from core.correo import ErrorCorreo, enviar as enviar_correo
    correos = [u.email for u in destinatarios(r, evento) if u.email]
    if not correos:
        return False
    try:
        transaction.on_commit(lambda: _enviar_seguro(enviar_correo, ErrorCorreo, correos, r.pk, evento))
    except Exception:  # fuera de una transacción
        _enviar_seguro(enviar_correo, ErrorCorreo, correos, r.pk, evento)
    return True


def _enviar_seguro(enviar_correo, ErrorCorreo, correos, pk, evento):
    r = Rendicion.objects.select_related('entrega__responsable', 'rendidor').get(pk=pk)
    try:
        enviar_correo(correos, f'{_ASUNTOS[evento]}: {r.numero}', 'finanzas/correo_rendicion.html',
                      {'r': r, 'evento': evento, 'ultimo': r.historial.last()})
    except ErrorCorreo as exc:
        log.info('Aviso de rendición %s no enviado: %s', r.numero, exc)


def vencidas(hoy=None):
    """Entregas abiertas con saldo en poder del responsable y fuera del plazo de su política: [(entrega, límite)]."""
    hoy = hoy or timezone.localdate()
    salida = []
    for e in EntregaRendir.objects.filter(estado='ABIERTA', tipo='ENTREGA').select_related('responsable'):
        pol = politica_de(e.responsable)
        dias = pol.dias_para_rendir if pol else 15
        limite = e.fecha + timedelta(days=dias)
        if limite < hoy and e.saldo > 0 and not e.rendiciones.exclude(estado__in=Rendicion.EDITABLES).exists():
            salida.append((e, limite))
    return salida


def usuarios_de(tercero):
    """Usuarios que rinden como ese trabajador y sus jefes (para los recordatorios)."""
    usuarios, jefes = [], []
    for p in PerfilUsuario.objects.filter(trabajador=tercero).select_related('usuario', 'jefe'):
        usuarios.append(p.usuario)
        if p.jefe:
            jefes.append(p.jefe)
    return usuarios, jefes


# ---------------------------------------------------------------- reportes
TERMINADAS = ('APROBADA', 'CONTABILIZADA', 'REEMBOLSADA', 'DEVUELTA')


def tablero(desde, hasta):
    lineas = list(LineaGasto.objects.filter(rendicion__estado__in=TERMINADAS, fecha__range=[desde, hasta])
                  .exclude(estado='RECHAZADA')
                  .select_related('categoria', 'centro_costo', 'proyecto', 'indicador',
                                  'rendicion__entrega__responsable'))
    def agrupar(clave):
        d = defaultdict(lambda: D0)
        for l in lineas:
            d[clave(l)] += _pen(l)
        return sorted(d.items(), key=lambda x: -x[1])
    total = sum((_pen(l) for l in lineas), D0)
    fuera = [l for l in lineas if l.alertas]
    aprobadas = Rendicion.objects.filter(aprobada_en__isnull=False, enviada_en__isnull=False,
                                         aprobada_en__date__range=[desde, hasta])
    dias = [(r.aprobada_en - r.enviada_en).total_seconds() / 86400 for r in aprobadas]
    return {
        'total': total, 'lineas': len(lineas),
        'por_categoria': agrupar(lambda l: l.categoria.nombre),
        'por_centro': agrupar(lambda l: f'{l.centro_costo.codigo} {l.centro_costo.nombre}' if l.centro_costo else
                              'Sin centro de costo'),
        'por_persona': agrupar(lambda l: l.rendicion.entrega.responsable.nombre),
        'por_proyecto': agrupar(lambda l: str(l.proyecto) if l.proyecto_id else 'Sin proyecto'),
        'por_indicador': agrupar(lambda l: str(l.indicador) if l.indicador_id else 'Sin indicador'),
        'por_mes': sorted(agrupar(lambda l: l.fecha.strftime('%Y-%m')), key=lambda x: x[0]),
        'fuera_politica': len(fuera), 'fuera_monto': sum((_pen(l) for l in fuera), D0),
        'pendientes': Rendicion.objects.filter(estado__in=('ENVIADA', 'EN_REVISION', 'OBSERVADA')).count(),
        'por_contabilizar': Rendicion.objects.filter(estado='APROBADA').count(),
        'vencidas': len(vencidas()),
        'dias_aprobacion': round(sum(dias) / len(dias), 1) if dias else None,
    }


def antiguedad(hoy=None):
    """Por entrega abierta: entregado, rendido, saldo y días desde la entrega en tramos 0-15, 16-30 y más de 30."""
    hoy = hoy or timezone.localdate()
    filas = []
    for e in (EntregaRendir.objects.filter(estado='ABIERTA').exclude(tipo='TARJETA').select_related('responsable')
              .order_by('fecha')):
        saldo = e.saldo
        if saldo == 0:
            continue
        dias = (hoy - e.fecha).days
        tramo = '0-15' if dias <= 15 else '16-30' if dias <= 30 else '+30'
        filas.append({'e': e, 'entregado': e.entregado, 'rendido': e.rendido, 'devuelto': e.devuelto,
                      'saldo': saldo, 'dias': dias, 'tramo': tramo})
    return filas


# ---------------------------------------------------------------- borradores recibidos (app, WhatsApp, correo, API)
def rendicion_abierta(user, crear=True, titulo='Gastos recibidos'):
    """La rendición en borrador (u observada) más reciente del usuario; si no hay, una nueva en su entrega abierta
    más reciente."""
    r = (Rendicion.objects.filter(rendidor=user, estado__in=Rendicion.EDITABLES, entrega__estado='ABIERTA')
         .exclude(entrega__tipo='TARJETA').order_by('-fecha', '-id').first())
    if r is not None or not crear:
        return r
    t = trabajador_de(user)
    entrega = (EntregaRendir.objects.filter(estado='ABIERTA', responsable=t).exclude(tipo='TARJETA')
               .order_by('-fecha', '-id').first()) if t else None
    if entrega is None:
        raise ErrorRendicion('No tiene una entrega a rendir ni caja chica abierta a su nombre.')
    return _crear_simple(entrega, user, titulo)


def _crear_simple(entrega, user, titulo):
    r = Rendicion.objects.create(entrega=entrega, rendidor=user, descripcion=titulo[:200])
    _historial(r, user, '', 'BORRADOR', titulo)
    return r


def borrador_recibido(user, datos, archivos=(), origen='MOVIL', rendicion=None):
    """Crea un gasto en borrador con los datos leídos (QR, XML o IA) y adjunta los archivos [(bytes, nombre, tipo)].
    El rendidor completa y confirma antes de enviar."""
    from core.sustentos import guardar_archivo, vincular
    from .models import ConfigRendicion
    r = rendicion or rendicion_abierta(user)
    if not r.editable:
        raise ErrorRendicion(f'{r.numero} ya no se modifica.')
    categoria = datos.get('categoria') or ConfigRendicion.actual().categoria_defecto or \
        CategoriaGasto.objects.filter(activo=True).first()
    if categoria is None:
        raise ErrorRendicion('Registre al menos una categoría de gasto.')
    linea = LineaGasto(
        rendicion=r, fecha=datos.get('fecha') or timezone.localdate(), tipo_comprobante=datos.get('tipo_comprobante') or '12',
        ruc=(datos.get('ruc') or '')[:11], razon_social=(datos.get('razon_social') or '')[:200],
        serie=(datos.get('serie') or '')[:4], numero=(datos.get('numero') or '')[:10],
        a_nombre_empresa=datos.get('a_nombre_empresa', True), moneda=datos.get('moneda') or r.entrega.moneda,
        base=datos.get('base') or D0, igv=datos.get('igv') or D0, total=datos.get('total') or D0,
        categoria=categoria, centro_costo=r.entrega.centro_costo, origen=origen,
        glosa=(datos.get('glosa') or f'Recibido por {dict(LineaGasto.ORIGENES)[origen].lower()}')[:200])
    if linea.moneda == 'USD' and r.entrega.moneda != 'USD':
        from core.tipo_cambio import venta_del_dia
        linea.tipo_cambio = venta_del_dia(linea.fecha) or Decimal('1')
    if datos.get('tipo_comprobante'):  # comprobante leído (QR, XML o IA): indicador sugerido, el rendidor confirma
        linea.indicador = datos.get('indicador') or sugerir_indicador(linea.tipo_comprobante, linea.a_nombre_empresa)
    dup = duplicado(linea)
    if dup:
        raise ErrorRendicion(f'Comprobante repetido: {dup}.')
    linea.save()
    for contenido, nombre, tipo in archivos:
        vincular(linea, guardar_archivo(None, user, datos=contenido, nombre=nombre, tipo=tipo), user,
                 f'Comprobante recibido ({dict(LineaGasto.ORIGENES)[origen]})')
    evaluar(r)
    return linea


# ---------------------------------------------------------------- tarjetas corporativas
def crear_tarjeta(tarjeta, usuario):
    """Guarda la tarjeta y su fondo (entrega de tipo tarjeta, contra la cuenta por pagar de la tarjeta)."""
    with transaction.atomic():
        if tarjeta.entrega_id is None:
            e = EntregaRendir(tipo='TARJETA', responsable=tarjeta.titular, moneda=tarjeta.moneda,
                              cuenta_contable=tarjeta.cuenta_contable, centro_costo=tarjeta.centro_costo,
                              motivo=f'Tarjeta {tarjeta.nombre} ****{tarjeta.ultimos4}'[:250], creado_por=usuario)
            e.save()
            tarjeta.entrega = e
        else:
            EntregaRendir.objects.filter(pk=tarjeta.entrega_id).update(
                responsable=tarjeta.titular, cuenta_contable=tarjeta.cuenta_contable, centro_costo=tarjeta.centro_costo)
        tarjeta.save()
    return tarjeta


def _filas_estado_cuenta(archivo):
    """[(fecha, descripción, referencia, monto)] de un Excel o CSV del banco (columnas fecha, descripción, monto;
    referencia opcional). Los abonos (montos negativos o pagos) se ignoran."""
    from compras.sire import _dec as dec, _fecha as fecha_de, _filas, _norm
    filas = _filas(archivo)
    for i, fila in enumerate(filas[:20]):
        enc = [_norm(c) for c in fila]
        if any(e.startswith('fecha') for e in enc) and any(e.startswith(('monto', 'importe', 'cargo')) for e in enc):
            break
    else:
        raise ErrorRendicion('No se reconoce el estado de cuenta: se esperan las columnas Fecha, Descripción y Monto.')
    col = lambda *p: next((j for j, e in enumerate(enc) if e.startswith(p)), None)
    c_fecha, c_desc = col('fecha'), col('descripcion', 'detalle', 'concepto', 'establecimiento')
    c_monto, c_ref = col('monto', 'importe', 'cargo'), col('referencia', 'operacion', 'nro')
    salida = []
    for fila in filas[i + 1:]:
        if c_fecha is None or c_monto is None or len(fila) <= max(c_fecha, c_monto):
            continue
        fecha, monto = fecha_de(fila[c_fecha]), dec(fila[c_monto])
        if fecha is None or monto <= 0:
            continue
        desc = str(fila[c_desc] if c_desc is not None and c_desc < len(fila) else '').strip() or 'Consumo'
        ref = str(fila[c_ref] if c_ref is not None and c_ref < len(fila) else '').strip()
        salida.append((fecha, desc[:200], ref[:60], monto))
    return salida


@transaction.atomic
def importar_estado_cuenta(tarjeta, archivo, usuario):
    """Un cargo = un gasto sin comprobante en la rendición del estado de cuenta, que el titular sustenta (concilia)
    con su comprobante. Los cargos ya importados no se repiten."""
    from .models import MovimientoTarjeta
    filas = _filas_estado_cuenta(archivo)
    if not filas:
        raise ErrorRendicion('El archivo no tiene cargos.')
    nuevos = []
    for fecha, desc, ref, monto in filas:
        mov, creado = MovimientoTarjeta.objects.get_or_create(tarjeta=tarjeta, fecha=fecha, descripcion=desc,
                                                              monto=monto, referencia=ref)
        if creado:
            nuevos.append(mov)
    if not nuevos:
        return None, 0
    user = PerfilUsuario.objects.filter(trabajador=tarjeta.titular).values_list('usuario', flat=True).first()
    rendidor = get_user_model().objects.get(pk=user) if user else usuario
    hasta = max(m.fecha for m in nuevos)
    r = _crear_simple(tarjeta.entrega, rendidor, f'Estado de cuenta {tarjeta} al {hasta:%d/%m/%Y}')
    for m in nuevos:
        m.linea = LineaGasto.objects.create(
            rendicion=r, fecha=m.fecha, tipo_comprobante='SC', moneda=tarjeta.moneda, total=m.monto, base=m.monto,
            categoria=tarjeta.categoria_defecto, centro_costo=tarjeta.centro_costo, origen='TARJETA',
            glosa=f'{m.descripcion} {m.referencia}'.strip()[:200])
        m.save(update_fields=['linea'])
    evaluar(r)
    return r, len(nuevos)


def pagar_tarjeta(tarjeta, cuenta, monto, fecha, usuario, numero_operacion=''):
    """Pago del estado de cuenta al banco: cancela la deuda de la tarjeta (egreso contra su cuenta por pagar)."""
    try:
        return op._mov_entrega(tarjeta.entrega, cuenta, fecha, 'EGRESO', monto, usuario,
                               f'Pago de la tarjeta {tarjeta}', numero_operacion)
    except op.ErrorTesoreria as exc:
        raise ErrorRendicion(str(exc))


def sin_comprobante(hoy=None):
    """Cargos de tarjeta pendientes de comprobante, con su antigüedad."""
    from .models import MovimientoTarjeta
    hoy = hoy or timezone.localdate()
    filas = []
    for m in MovimientoTarjeta.objects.select_related('tarjeta__titular', 'linea__rendicion').order_by('fecha'):
        if m.linea_id and not m.linea.sin_comprobante:
            continue
        if m.linea_id and m.linea.estado == 'RECHAZADA':
            continue
        dias = (hoy - m.fecha).days
        filas.append({'m': m, 'dias': dias, 'tramo': '0-15' if dias <= 15 else '16-30' if dias <= 30 else '+30'})
    return filas
