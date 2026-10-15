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
    if linea.tipo_comprobante == '01' and linea.base + linea.igv > linea.total + Decimal('0.05'):
        e.append('base + IGV no puede superar el total')
    if not linea.centro_costo_id and (linea.categoria.exige_centro or
                                      exige_centro(linea.categoria.cuenta_contable.codigo)):
        e.append(f'{linea.categoria} exige centro de costo')
    if linea.con_credito_fiscal and linea.moneda != linea.rendicion.entrega.moneda:
        e.append('una factura con crédito fiscal debe estar en la moneda de la entrega')
    dup = duplicado(linea)
    if dup:
        e.append(dup)
    return e


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
        sin_comprobante = l.tipo_comprobante == 'SC' or (l.tipo_comprobante != 'PM' and not l.numero)
        if sin_comprobante and (cat.exige_comprobante or (pol and pol.comprobante_obligatorio)):
            alertas[l.pk].append('sin comprobante')
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
    if n['usuario']:
        return user.pk == n['usuario'] or user.is_superuser
    return puede(user, 'rendiciones.aprobar')


# ---------------------------------------------------------------- flujo
def _historial(r, usuario, de, a, comentario=''):
    HistorialRendicion.objects.create(rendicion=r, usuario=usuario if usuario and usuario.is_authenticated else None,
                                      de_estado=de, a_estado=a, comentario=(comentario or '')[:300])


def _cambiar(r, estado, usuario, comentario='', **campos):
    de = r.estado
    r.estado = estado
    for k, v in campos.items():
        setattr(r, k, v)
    r.save()
    _historial(r, usuario, de, estado, comentario)


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
    if linea.estado == 'OBSERVADA':
        linea.estado = 'PENDIENTE'  # corregida por el rendidor
    linea.save()
    return linea


def quitar_linea(r, linea, usuario):
    _exigir_editable(r, usuario)
    linea.delete()


@transaction.atomic
def enviar(r, usuario, comentario=''):
    _exigir_editable(r, usuario)
    lineas = list(r.lineas.exclude(estado='RECHAZADA').select_related('categoria__cuenta_contable', 'rendicion__entrega'))
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
    """decisiones: {linea_id: ('APROBADA' | 'OBSERVADA' | 'RECHAZADA', comentario)} para aprobar línea por línea.
    Si alguna línea queda observada, la rendición vuelve al rendidor."""
    if not puede_aprobar(usuario, r):
        raise ErrorRendicion('No le corresponde aprobar esta rendición (nivel, permiso o es su propia rendición).')
    for linea in r.lineas.exclude(estado='RECHAZADA'):
        estado, nota = (decisiones or {}).get(linea.pk, ('APROBADA', ''))
        if estado in ('OBSERVADA', 'RECHAZADA') and len((nota or '').strip()) < 3:
            raise ErrorRendicion(f'Indique el motivo para {estado.lower()[:-1]}r {linea.documento or linea.glosa}.')
        linea.estado = 'PENDIENTE' if estado == 'APROBADA' else estado
        linea.comentario = (nota or '').strip()[:250]
        linea.save(update_fields=['estado', 'comentario'])
    if r.lineas.filter(estado='OBSERVADA').exists():
        _cambiar(r, 'OBSERVADA', usuario, comentario or 'Gastos observados: corríjalos y vuelva a enviar')
        notificar(r, 'observada')
        return r
    if not r.lineas.exclude(estado='RECHAZADA').exists():
        _cambiar(r, 'RECHAZADA', usuario, comentario or 'Todos los gastos fueron rechazados')
        notificar(r, 'rechazada')
        return r
    nivel = r.nivel_pendiente['nivel']
    r.nivel_aprobado = nivel
    if r.nivel_pendiente is None:
        r.lineas.filter(estado='PENDIENTE').update(estado='APROBADA')
        _cambiar(r, 'APROBADA', usuario, comentario or f'Aprobada (nivel {nivel})', aprobada_en=timezone.now())
        notificar(r, 'aprobada')
    else:
        _cambiar(r, 'EN_REVISION', usuario, comentario or f'Aprobada en el nivel {nivel}')
        notificar(r, 'enviada')
    return r


@transaction.atomic
def observar(r, usuario, comentario):
    if not puede_aprobar(usuario, r):
        raise ErrorRendicion('No le corresponde revisar esta rendición.')
    if len((comentario or '').strip()) < 5:
        raise ErrorRendicion('Indique qué debe corregir el rendidor.')
    _cambiar(r, 'OBSERVADA', usuario, comentario)
    notificar(r, 'observada')


@transaction.atomic
def rechazar(r, usuario, comentario):
    if not puede_aprobar(usuario, r):
        raise ErrorRendicion('No le corresponde revisar esta rendición.')
    if len((comentario or '').strip()) < 5:
        raise ErrorRendicion('Indique el motivo del rechazo.')
    r.lineas.update(estado='RECHAZADA')
    _cambiar(r, 'RECHAZADA', usuario, comentario)
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
               cuenta_contable=linea.categoria.cuenta_contable, centro_costo=linea.centro_costo,
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


_DOCUMENTO_GASTO = {'03': '03', '12': '12', 'PM': 'PM', 'SC': 'RI'}


def _registrar_gasto(linea, entrega, fecha, usuario):
    g = GastoRendicion(fecha=fecha, tipo_documento=_DOCUMENTO_GASTO.get(linea.tipo_comprobante, '00'),
                       numero_documento=linea.documento[:30],
                       proveedor=f'{linea.ruc} {linea.razon_social}'.strip()[:150],
                       descripcion=f'{linea.glosa} ({linea.fecha:%d/%m/%Y})'[:200],
                       cuenta_contable=linea.categoria.cuenta_contable, centro_costo=linea.centro_costo,
                       monto=linea.importe)
    return op.rendir_gasto(entrega, g, usuario)


@transaction.atomic
def contabilizar(r, usuario, fecha=None):
    """Revisor contable: facturas al registro de compras (con crédito fiscal) y el resto como gastos rendidos;
    el asiento sale de la centralización (gasto y centro de costo contra la entrega a rendir o caja chica)."""
    if r.estado != 'APROBADA':
        raise ErrorRendicion('Solo se contabilizan rendiciones aprobadas.')
    fecha = fecha or timezone.localdate()
    try:
        op._periodo_abierto(fecha)
    except op.ErrorTesoreria as exc:
        raise ErrorRendicion(str(exc))
    entrega = r.entrega
    if entrega.estado != 'ABIERTA':
        raise ErrorRendicion(f'{entrega.numero} está liquidada.')
    for linea in r.lineas.filter(estado='APROBADA').select_related('categoria__cuenta_contable', 'centro_costo'):
        try:
            if linea.con_credito_fiscal:
                linea.compra = _registrar_factura(linea, entrega, fecha, usuario)
            else:
                linea.gasto = _registrar_gasto(linea, entrega, fecha, usuario)
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
                  .select_related('categoria', 'centro_costo', 'rendicion__entrega__responsable'))
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
    for e in EntregaRendir.objects.filter(estado='ABIERTA').select_related('responsable').order_by('fecha'):
        saldo = e.saldo
        if saldo == 0:
            continue
        dias = (hoy - e.fecha).days
        tramo = '0-15' if dias <= 15 else '16-30' if dias <= 30 else '+30'
        filas.append({'e': e, 'entregado': e.entregado, 'rendido': e.rendido, 'devuelto': e.devuelto,
                      'saldo': saldo, 'dias': dias, 'tramo': tramo})
    return filas
