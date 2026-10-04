"""Depreciación lineal mensual, bajas, registro de activos fijos (formato 7.1) y cuadre con la contabilidad."""
from collections import defaultdict
from datetime import date
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import r2

from .models import ActivoFijo, Depreciacion, ProcesoDepreciacion

D0 = Decimal('0')


class ErrorActivo(Exception):
    pass


# ---------------------------------------------------------------- periodos
def periodo_de(fecha):
    return fecha.strftime('%Y%m')


def siguiente(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    return f'{anio + (mes == 12)}{1 if mes == 12 else mes + 1:02d}'


def meses_entre(desde, hasta):
    """Meses de `desde` a `hasta` inclusive (0 si hasta < desde)."""
    n = (int(hasta[:4]) - int(desde[:4])) * 12 + int(hasta[4:]) - int(desde[4:]) + 1
    return max(n, 0)


def texto_periodo(periodo):
    return f'{periodo[4:]}/{periodo[:4]}'


def _periodo_contable_cerrado(periodo):
    from contabilidad.models import PeriodoContable
    return PeriodoContable.esta_cerrado(periodo)


# ---------------------------------------------------------------- cálculo
def estado_depreciacion(activo, registros=None):
    """(acumulada, meses usados, próximo periodo a depreciar) según lo ya registrado."""
    registros = list(activo.depreciaciones.all()) if registros is None else registros
    inicio = periodo_de(activo.fecha_uso)
    acumulada, meses, proximo = activo.dep_inicial, 0, inicio
    if activo.dep_inicial_hasta:
        hasta = periodo_de(activo.dep_inicial_hasta)
        meses = meses_entre(inicio, hasta)
        proximo = max(inicio, siguiente(hasta))
    for d in sorted(registros, key=lambda x: x.periodo):
        acumulada += d.cuota
        meses += d.meses
        proximo = siguiente(d.periodo)
    return acumulada, meses, proximo


def cuotas(activo, hasta_periodo, registros=None):
    """[(periodo, cuota, acumulada)] pendientes hasta `hasta_periodo` (método lineal: lo que falta depreciar entre
    los meses que quedan de vida útil; el último mes cierra el redondeo)."""
    if not activo.deprecia or activo.estado == 'ANULADO':
        return []
    acumulada, meses, periodo = estado_depreciacion(activo, registros)
    base = activo.base_depreciable
    tope = hasta_periodo
    if activo.estado == 'BAJA' and activo.fecha_baja:
        tope = min(tope, periodo_de(activo.fecha_baja))  # se deprecia hasta el mes de la baja
    filas = []
    while periodo <= tope:
        restantes = activo.vida_util_meses - meses
        if restantes <= 0 or acumulada >= base:
            break
        # se redondea hacia abajo: la diferencia la absorbe el último mes de vida útil
        cuota = base - acumulada if restantes == 1 else min(
            ((base - acumulada) / restantes).quantize(Decimal('0.01'), rounding=ROUND_DOWN), base - acumulada)
        acumulada += cuota
        meses += 1
        filas.append((periodo, cuota, acumulada))
        periodo = siguiente(periodo)
    return filas


def cronograma(activo):
    """Proyección completa de la depreciación pendiente (para mostrar en el detalle)."""
    fin = periodo_de(date(activo.fecha_uso.year + activo.vida_util_meses // 12 + 2, 12, 1))
    return cuotas(activo, fin)


# ---------------------------------------------------------------- proceso mensual
def ultimo_proceso():
    return ProcesoDepreciacion.objects.order_by('-periodo').first()


def periodo_siguiente():
    """Mes que toca depreciar: el siguiente al último procesado. Sin cálculos previos se sugiere el primer mes
    pendiente de los activos (sin pasar del mes actual) y el usuario puede elegir otro."""
    ultimo = ultimo_proceso()
    if ultimo:
        return siguiente(ultimo.periodo)
    actual = periodo_de(timezone.localdate())
    candidatos = [estado_depreciacion(a)[2] for a in ActivoFijo.objects.exclude(estado='ANULADO')]
    return min(candidatos + [actual])


def _registrar(activo, periodo, proceso=None):
    """Graba en `periodo` la depreciación pendiente del activo (incluye meses anteriores no registrados)."""
    filas = cuotas(activo, periodo)
    if not filas:
        return None
    return Depreciacion.objects.create(proceso=proceso, activo=activo, periodo=periodo, meses=len(filas),
                                       cuota=sum((f[1] for f in filas), D0), acumulada=filas[-1][2])


def depreciar(periodo, usuario):
    if len(periodo or '') != 6 or not periodo.isdigit() or not 1 <= int(periodo[4:]) <= 12:
        raise ErrorActivo('Periodo no válido.')
    if ultimo_proceso() and periodo != periodo_siguiente():
        raise ErrorActivo(f'Corresponde calcular {texto_periodo(periodo_siguiente())}: '
                          'los meses se procesan en orden.')
    if periodo > periodo_de(timezone.localdate()):
        raise ErrorActivo('No se puede depreciar un mes futuro.')
    if _periodo_contable_cerrado(periodo):
        raise ErrorActivo(f'El periodo contable {texto_periodo(periodo)} está cerrado.')
    with transaction.atomic():
        proceso = ProcesoDepreciacion.objects.create(periodo=periodo, usuario=usuario)
        total, cantidad = D0, 0
        activos = ActivoFijo.objects.exclude(estado='ANULADO').select_related('categoria').prefetch_related(
            'depreciaciones')
        for a in activos:
            if a.depreciaciones.filter(periodo=periodo).exists():  # ya registrada al darlo de baja
                continue
            d = _registrar(a, periodo, proceso)
            if d:
                total += d.cuota
                cantidad += 1
        proceso.total, proceso.cantidad = total, cantidad
        proceso.save(update_fields=['total', 'cantidad'])
        from core.auditoria import registrar
        registrar('CREAR', proceso, {'Total S/': str(total), 'Activos': cantidad})
        _marcar_pendiente(periodo)
    return proceso


def revertir(proceso, usuario, motivo):
    if proceso != ultimo_proceso():
        raise ErrorActivo('Solo se revierte el último mes calculado.')
    if not (usuario and usuario.is_superuser):
        raise ErrorActivo('Solo un administrador puede revertir la depreciación.')
    if len((motivo or '').strip()) < 10:
        raise ErrorActivo('Indique el motivo (mínimo 10 caracteres).')
    if _periodo_contable_cerrado(proceso.periodo):
        raise ErrorActivo(f'El periodo contable {texto_periodo(proceso.periodo)} está cerrado.')
    from core.auditoria import registrar
    with transaction.atomic():
        registrar('ELIMINAR', proceso, {'Total S/': str(proceso.total), 'Activos': proceso.cantidad},
                  f'Reversión: {motivo.strip()}')
        periodo = proceso.periodo
        proceso.delete()
        _marcar_pendiente(periodo)


def _marcar_pendiente(periodo):
    from contabilidad.models import PeriodoContable
    PeriodoContable.objects.filter(periodo=periodo, cerrado=False).update(pendiente=True)


# ---------------------------------------------------------------- baja y anulación
def dar_de_baja(activo, usuario, fecha, motivo, detalle, archivo):
    """archivo: sustento obligatorio (acta de baja, informe técnico, denuncia policial, factura de venta)."""
    from core.sustentos import ErrorSustento, adjuntar, validar_archivo
    if activo.estado != 'ACTIVO':
        raise ErrorActivo('El activo no está en uso.')
    if not fecha or fecha < activo.fecha_adquisicion:
        raise ErrorActivo('La fecha de baja no puede ser anterior a la adquisición.')
    if fecha > timezone.localdate():
        raise ErrorActivo('La fecha de baja no puede ser futura.')
    if motivo not in dict(ActivoFijo.MOTIVOS_BAJA):
        raise ErrorActivo('Indique el motivo de la baja.')
    if len((detalle or '').strip()) < 10:
        raise ErrorActivo('Describa la baja (mínimo 10 caracteres).')
    try:
        validar_archivo(archivo)
    except ErrorSustento as exc:
        raise ErrorActivo(f'{exc} (acta de baja, informe técnico, denuncia o factura de venta).')
    periodo = periodo_de(fecha)
    if _periodo_contable_cerrado(periodo):
        raise ErrorActivo(f'El periodo contable {texto_periodo(periodo)} está cerrado.')
    if activo.depreciaciones.filter(periodo__gt=periodo).exists():
        raise ErrorActivo('El activo ya tiene depreciación registrada después de esa fecha: revierta esos meses '
                          'o use una fecha posterior.')
    with transaction.atomic():
        activo.estado, activo.fecha_baja, activo.motivo_baja = 'BAJA', fecha, motivo
        activo.detalle_baja = detalle.strip()[:250]
        activo.baja_por = usuario if usuario and usuario.is_authenticated else None
        activo.baja_en = timezone.now()
        activo.save()
        adjuntar(activo, archivo, usuario, f'Baja: {activo.get_motivo_baja_display()}')
        # la depreciación hasta el mes de la baja queda registrada en ese mes
        if not activo.depreciaciones.filter(periodo=periodo).exists():
            _registrar(activo, periodo, ProcesoDepreciacion.objects.filter(periodo=periodo).first())
        _marcar_pendiente(periodo)
    return activo


def anular(activo, usuario, motivo):
    """Activo registrado por error: solo si no tiene depreciación registrada."""
    if activo.estado == 'ANULADO':
        raise ErrorActivo('El activo ya está anulado.')
    if activo.depreciaciones.exists():
        raise ErrorActivo('Tiene depreciación registrada: no se anula. Si ya no existe, dele de baja.')
    if len((motivo or '').strip()) < 10:
        raise ErrorActivo('Indique el motivo de la anulación (mínimo 10 caracteres).')
    activo.estado, activo.detalle_baja = 'ANULADO', motivo.strip()[:250]
    activo.baja_por = usuario if usuario and usuario.is_authenticated else None
    activo.baja_en = timezone.now()
    activo.save()
    _marcar_pendiente(periodo_de(activo.fecha_alta))
    return activo


# ---------------------------------------------------------------- compras
def items_compra(compra):
    """Ítems de activo fijo de la compra con lo pendiente de registrar: [(item, pendientes, valor unitario S/)]."""
    if compra.es_nota_credito or compra.estado == 'ANULADO' or compra.es_saldo_inicial:
        return []
    registrados = defaultdict(int)
    for pid in compra.activos.exclude(estado='ANULADO').values_list('producto_id', flat=True):
        registrados[pid] += 1
    filas = []
    for i in compra.items.select_related('producto'):
        es_activo = (i.producto and i.producto.clase == 'ACTIVO') or (
            not i.producto and compra.clasificacion == 'ACTIVO_FIJO')
        if not es_activo:
            continue
        unidades = max(int(i.cantidad), 1)
        clave = i.producto_id
        ya = min(registrados[clave], unidades)
        registrados[clave] -= ya
        if unidades - ya > 0:
            filas.append((i, unidades - ya, r2(i.subtotal * compra.tc_efectivo / unidades)))
    return filas


def cuenta_de_compra(compra, item):
    """Cuenta en la que la compra registró el activo (la del comprobante, la del producto o la por defecto)."""
    from contabilidad.models import CuentaDefecto
    if compra.cuenta_contable_id:
        return compra.cuenta_contable
    if item and item.producto and item.producto.cuenta_compra_id:
        return item.producto.cuenta_compra
    return CuentaDefecto.mapa().get(f'compra_{compra.clasificacion}')


# ---------------------------------------------------------------- registro 7.1 y cuadre
def registro(anio):
    """Formato 7.1: registro de activos fijos - detalle de los activos fijos revaluados y no revaluados."""
    inicio, fin = date(anio, 1, 1), date(anio, 12, 31)
    p_ini, p_fin = f'{anio}01', f'{anio}12'
    filas = []
    qs = (ActivoFijo.objects.exclude(estado='ANULADO').filter(fecha_adquisicion__lte=fin)
          .exclude(fecha_baja__lt=inicio).select_related('categoria__cuenta_activo').prefetch_related('depreciaciones'))
    for a in qs:
        saldo_inicial = a.valor if a.fecha_adquisicion < inicio else D0
        adquisiciones = a.valor if a.fecha_adquisicion >= inicio else D0
        retiros = a.valor if a.fecha_baja and inicio <= a.fecha_baja <= fin else D0
        inicial_anterior = a.dep_inicial if a.dep_inicial_hasta and a.dep_inicial_hasta < inicio else D0
        inicial_ejercicio = a.dep_inicial - inicial_anterior
        anterior = inicial_anterior + sum((d.cuota for d in a.depreciaciones.all() if d.periodo < p_ini), D0)
        ejercicio = inicial_ejercicio + sum((d.cuota for d in a.depreciaciones.all() if p_ini <= d.periodo <= p_fin),
                                            D0)
        dep_retiros = (anterior + ejercicio) if retiros else D0
        filas.append({'a': a, 'saldo_inicial': saldo_inicial, 'adquisiciones': adquisiciones, 'retiros': retiros,
                      'historico': saldo_inicial + adquisiciones - retiros, 'dep_anterior': anterior,
                      'dep_ejercicio': ejercicio, 'dep_retiros': dep_retiros,
                      'dep_acumulada': anterior + ejercicio - dep_retiros})
    claves = ['saldo_inicial', 'adquisiciones', 'retiros', 'historico', 'dep_anterior', 'dep_ejercicio',
              'dep_retiros', 'dep_acumulada']
    totales = {k: sum((f[k] for f in filas), D0) for k in claves}
    return filas, totales


def cuadre(fecha):
    """Por cuenta contable: valor según el registro de activos frente al saldo del libro mayor."""
    from contabilidad.models import AsientoLinea
    p = periodo_de(fecha)
    registro_cta = defaultdict(lambda: D0)
    for a in (ActivoFijo.objects.exclude(estado='ANULADO').filter(fecha_alta__lte=fecha)
              .select_related('categoria__cuenta_activo', 'categoria__cuenta_depreciacion')
              .prefetch_related('depreciaciones')):
        if a.estado == 'BAJA' and a.fecha_baja and a.fecha_baja <= fecha:
            continue
        registro_cta[a.categoria.cuenta_activo] += a.valor
        if a.categoria.cuenta_depreciacion_id:
            dep = a.dep_inicial + sum((d.cuota for d in a.depreciaciones.all() if d.periodo <= p), D0)
            registro_cta[a.categoria.cuenta_depreciacion] -= dep
    filas = []
    for cuenta, valor in sorted(registro_cta.items(), key=lambda x: x[0].codigo):
        agg = AsientoLinea.objects.filter(cuenta=cuenta, asiento__fecha__lte=fecha).aggregate(
            d=Sum('debe'), h=Sum('haber'))
        libro = (agg['d'] or D0) - (agg['h'] or D0)
        filas.append({'cuenta': cuenta, 'registro': valor, 'libro': libro, 'diferencia': libro - valor})
    return filas
