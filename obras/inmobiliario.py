"""Proyecto inmobiliario (venta de unidades): tablero de unidades, separación y venta con su cronograma de pagos,
cobro de cada cuota con su comprobante de anticipo y entrega con el costo de cada unidad.

Contabilidad (NIC 2 y NIIF 15: el ingreso se reconoce al entregar la unidad):
- Cada cobro emite su comprobante de anticipo. En la primera venta de inmuebles el IGV grava solo la construcción: el
  terreno (el % de la obra; la ley lo presume en el 50 % del valor de venta) va como línea inafecta. El ingreso del
  comprobante queda como anticipo recibido (122) hasta la entrega.
- Cada mes los costos del proyecto (clase 6 con el centro de costo de la obra) se activan como inmuebles en
  construcción (2341 contra la variación de la producción almacenada 7134). El terreno y lo que se carga directo a la
  2341 con el centro de la obra (compra con esa cuenta o asiento manual) ya están activados y suman al costo.
- En la entrega los anticipos pasan a la venta (122 contra 7024) y el costo de las unidades sale de los inmuebles en
  construcción al costo de ventas (6924 contra 2341), repartido por área techada o por precio de lista.
Los gastos generales repartidos a la obra (Contabilidad › Reparto de gastos) no se activan: administración y ventas
no forman parte del costo del inmueble (NIC 2 párr. 16)."""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import CobroInmueble, CuotaInmueble, Obra, Unidad, VentaInmueble

D0 = Decimal('0')
DIAS_AVISO = 3
ORIGENES_SIN_ACTIVAR = ('REPARTO', 'INMUEBLE')


class ErrorInmobiliario(Exception):
    pass


def r2(v):
    return Decimal(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _sumar_meses(fecha, meses):
    mes = fecha.month - 1 + meses
    anio, mes = fecha.year + mes // 12, mes % 12 + 1
    dias = [31, 29 if anio % 4 == 0 and (anio % 100 or anio % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30,
            31][mes - 1]
    return fecha.replace(year=anio, month=mes, day=min(fecha.day, dias))


def cronograma(precio, financiamiento, fecha, separacion=D0, inicial=D0, fecha_inicial=None, cuotas=0,
               primera_cuota=None, bono=D0, fecha_banco=None):
    """[(concepto, vence, monto)] que suma el precio: separación, cuota inicial, cuotas mensuales con la empresa
    (financiamiento directo), bono o subsidio del Estado y el desembolso del banco (crédito hipotecario o MiVivienda)
    o, al contado, el saldo en la fecha de la cuota inicial."""
    precio, separacion, inicial, bono = (Decimal(x or 0) for x in (precio, separacion, inicial, bono))
    filas = []
    if separacion:
        filas.append(('SEPARACION', fecha, separacion))
    if inicial:
        filas.append(('INICIAL', fecha_inicial or fecha, inicial))
    if bono:
        filas.append(('BONO', fecha_banco or fecha_inicial or fecha, bono))
    resto = precio - separacion - inicial - bono
    if resto < 0:
        raise ErrorInmobiliario('La separación, la cuota inicial y el bono superan el precio.')
    if resto:
        if financiamiento == 'DIRECTO':
            n = max(int(cuotas or 1), 1)
            base = r2(resto / n)
            inicio = primera_cuota or _sumar_meses(fecha_inicial or fecha, 1)
            for i in range(n):
                filas.append(('CUOTA', _sumar_meses(inicio, i), resto - base * (n - 1) if i == n - 1 else base))
        elif financiamiento in ('HIPOTECARIO', 'MIVIVIENDA', 'TECHO_PROPIO'):
            filas.append(('BANCO', fecha_banco or _sumar_meses(fecha_inicial or fecha, 2), resto))
        else:
            filas.append(('CUOTA', fecha_inicial or fecha, resto))
    return filas


@transaction.atomic
def separar(obra, unidades, cliente, precio, usuario, filas, financiamiento='CONTADO', banco='', fecha=None,
            vence_separacion=None, oportunidad=None, notas=''):
    """Separación de una o más unidades con su cronograma de pagos [(concepto, vence, monto)]."""
    if obra.tipo != 'INMOBILIARIA':
        raise ErrorInmobiliario('La obra no es un proyecto inmobiliario.')
    unidades = list(Unidad.objects.select_for_update().filter(pk__in=[u.pk for u in unidades], obra=obra))
    if not unidades:
        raise ErrorInmobiliario('Elija al menos una unidad del proyecto.')
    ocupadas = [u.codigo for u in unidades if u.estado != 'DISPONIBLE']
    if ocupadas:
        raise ErrorInmobiliario(f'No están disponibles: {", ".join(ocupadas)}.')
    precio = Decimal(precio or 0)
    if precio <= 0:
        raise ErrorInmobiliario('Indique el precio de venta.')
    if not filas:
        filas = [('CUOTA', fecha or timezone.localdate(), precio)]
    if sum((Decimal(m) for _, _, m in filas), D0) != precio:
        raise ErrorInmobiliario(f'El cronograma suma S/ {sum((Decimal(m) for _, _, m in filas), D0):,.2f} y el precio es '
                                f'S/ {precio:,.2f}.')
    if any(Decimal(m) <= 0 for _, _, m in filas):
        raise ErrorInmobiliario('Cada cuota del cronograma debe ser mayor a cero.')
    v = VentaInmueble.objects.create(
        obra=obra, cliente=cliente, precio=precio, financiamiento=financiamiento, banco=banco[:120],
        fecha_separacion=fecha or timezone.localdate(), vence_separacion=vence_separacion, oportunidad=oportunidad,
        vendedor=usuario if usuario and usuario.is_authenticated else None, terreno_pct=obra.terreno_pct or D0,
        notas=notas)
    v.unidades.set(unidades)
    Unidad.objects.filter(pk__in=[u.pk for u in unidades]).update(estado='SEPARADA')
    if oportunidad is not None and oportunidad.etapa in ('PROSPECTO', 'CALIFICADO', 'PROPUESTA'):
        _etapa(oportunidad, 'NEGOCIACION', monto=precio)
    for n, (concepto, vence, monto) in enumerate(sorted(filas, key=lambda f: f[1]), 1):
        CuotaInmueble.objects.create(venta=v, numero=n, concepto=concepto, vence=vence, monto=Decimal(monto))
    return v


def _etapa(oportunidad, etapa, monto=None, motivo=''):
    """La oportunidad del comprador en el CRM sigue a la venta: separación → negociación, minuta → ganada,
    anulación → perdida."""
    oportunidad.etapa = etapa
    if monto is not None:
        oportunidad.monto = monto
    if motivo:
        oportunidad.motivo_perdida = motivo[:200]
    oportunidad.save(update_fields=['etapa', 'monto', 'motivo_perdida', 'actualizado'])


def firmar_minuta(venta, fecha=None):
    if venta.estado != 'SEPARADA':
        raise ErrorInmobiliario(f'La venta está {venta.get_estado_display().lower()}.')
    venta.estado, venta.fecha_minuta = 'VENDIDA', fecha or timezone.localdate()
    venta.save(update_fields=['estado', 'fecha_minuta'])
    venta.unidades.update(estado='VENDIDA')
    if venta.oportunidad_id and venta.oportunidad.etapa != 'GANADA':
        _etapa(venta.oportunidad, 'GANADA', monto=venta.precio)
    return venta


@transaction.atomic
def anular(venta, motivo):
    if venta.estado in ('ENTREGADA', 'ANULADA'):
        raise ErrorInmobiliario(f'La venta está {venta.get_estado_display().lower()}.')
    if not (motivo or '').strip():
        raise ErrorInmobiliario('Indique el motivo de la anulación (desistimiento, separación vencida…).')
    cobrado = venta.cobrado
    if cobrado:
        raise ErrorInmobiliario(f'Tiene cobros por S/ {cobrado:,.2f}: emita la nota de crédito de los anticipos y '
                                f'registre la devolución en Tesorería antes de anular.')
    venta.estado, venta.motivo_anulacion = 'ANULADA', motivo.strip()[:200]
    venta.save(update_fields=['estado', 'motivo_anulacion'])
    venta.unidades.update(estado='DISPONIBLE')
    if venta.oportunidad_id and venta.oportunidad.etapa in venta.oportunidad.ABIERTAS:
        _etapa(venta.oportunidad, 'PERDIDA', motivo=f'Venta {venta.numero} anulada: {venta.motivo_anulacion}')
    return venta


# ---------------------------------------------------------------- cobros con comprobante de anticipo
def producto_anticipo():
    """Servicio «Anticipo de inmueble» cuya cuenta de ventas es la de anticipos de clientes (122)."""
    from contabilidad.models import CuentaDefecto
    from core.models import Producto
    p = Producto.objects.filter(codigo='ANTINMUEBLE').first()
    if p is None:
        p = Producto(codigo='ANTINMUEBLE', nombre='Anticipo de inmueble', clase='SERVICIO', unidad='ZZ',
                     puede_comprarse=False)
        p.cuenta_venta = CuentaDefecto.mapa().get('mov_ANTICIPO_INGRESO')
        p.save()
    return p


def partir(monto, terreno_pct, igv_tasa=Decimal('18')):
    """(construcción sin IGV, IGV, terreno) de un importe cobrado: el terreno es el % del valor de venta (sin IGV) y
    el IGV grava solo la construcción. El terreno lleva el redondeo para que la suma sea el importe."""
    p = Decimal(terreno_pct or 0) / 100
    tasa = Decimal(igv_tasa) / 100
    construccion = r2(monto * (1 - p) / ((1 - p) * (1 + tasa) + p)) if p < 1 else D0
    igv = r2(construccion * tasa)
    return construccion, igv, Decimal(monto) - construccion - igv


def _enviar_sunat(pk):
    from core import sunat
    from ventas.models import Venta
    try:
        sunat.enviar_comprobante(Venta.objects.get(pk=pk))
    except sunat.ErrorFacturacion:
        pass  # queda con el error; se reenvía desde el comprobante


@transaction.atomic
def cobrar(cuota, monto, fecha, cuenta, usuario, medio_pago='TRANSFERENCIA', numero_operacion='', sustento=None):
    """Comprobante de anticipo (factura si el comprador tiene RUC; si no, boleta) y su cobranza en la cuenta
    recaudadora del proyecto."""
    from contabilidad.models import PeriodoContable
    from core.models import Empresa, FacturacionConfig, Serie
    from finanzas import multiple
    from finanzas.operaciones import ErrorTesoreria
    from ventas.forms import _serie_defecto
    from ventas.models import Venta, VentaItem
    venta = cuota.venta
    obra = venta.obra
    monto = r2(monto or 0)
    if venta.estado in ('ANULADA', 'ENTREGADA'):
        raise ErrorInmobiliario(f'La venta está {venta.get_estado_display().lower()}.')
    if monto <= 0:
        raise ErrorInmobiliario('Indique el importe cobrado.')
    if monto > cuota.saldo:
        raise ErrorInmobiliario(f'El importe supera el saldo de la cuota (S/ {cuota.saldo:,.2f}).')
    cuenta = cuenta or obra.cuenta_recaudadora
    if cuenta is None:
        raise ErrorInmobiliario('Elija la cuenta donde ingresa el cobro (o la cuenta recaudadora del proyecto).')
    fecha = fecha or timezone.localdate()
    if PeriodoContable.esta_cerrado(fecha.strftime('%Y%m')):
        raise ErrorInmobiliario(f'El periodo {fecha:%m/%Y} está cerrado.')
    cliente = venta.cliente
    tipo = '01' if cliente.tipo_doc == '6' else '03'
    serie, numero = Serie.siguiente(tipo, _serie_defecto(tipo))
    unidades = ', '.join(u.codigo for u in venta.unidades.all())
    concepto = f'{cuota.get_concepto_display()}{"" if cuota.concepto in ("SEPARACION", "INICIAL") else f" {cuota.numero}"}'
    comp = Venta.objects.create(
        tipo_comprobante=tipo, serie=serie, numero=numero, tercero=cliente, fecha_emision=fecha, forma_pago='CONTADO',
        tipo_operacion='GRAVADA', descontar_stock=False, centro_costo=obra.centro_costo, vendedor=(
            venta.vendedor.get_full_name() or venta.vendedor.username)[:80] if venta.vendedor_id else '',
        glosa=f'Anticipo ({concepto}) de la venta {venta.numero}: {unidades} – {obra.nombre}'[:500])
    construccion, _, terreno = partir(monto, venta.terreno_pct, Empresa.actual().igv_tasa)
    producto = producto_anticipo()
    if construccion:
        VentaItem.objects.create(documento=comp, producto=producto, cantidad=1, precio_unitario=construccion,
                                 afectacion='GRAVADA',
                                 descripcion=f'Anticipo {concepto.lower()}: construcción de {unidades} – {obra.nombre}'[:250])
    if terreno:
        VentaItem.objects.create(documento=comp, producto=producto, cantidad=1, precio_unitario=terreno,
                                 afectacion='INAFECTA',
                                 descripcion=f'Anticipo {concepto.lower()}: terreno de {unidades} '
                                             f'({venta.terreno_pct:g} % del valor, no gravado con IGV)'[:250])
    comp.calcular_totales()
    comp.save()
    datos = {'cuenta': cuenta, 'fecha': fecha, 'medio_pago': medio_pago, 'numero_operacion': numero_operacion or '',
             'glosa': f'Cobro {concepto} {venta.numero} {cliente.nombre}'[:250], 'sustento': sustento}
    try:
        movimiento = multiple.registrar('cobranza', datos, [(comp, False, comp.total, '')], usuario)[0]
    except ErrorTesoreria as exc:
        raise ErrorInmobiliario(str(exc))
    cobro = CobroInmueble.objects.create(cuota=cuota, fecha=fecha, monto=monto, comprobante=comp,
                                         movimiento=movimiento)
    cfg = FacturacionConfig.actual()
    from core import sunat
    if cfg.activa and cfg.envio_automatico and tipo in sunat.TIPO_NUBEFACT:
        transaction.on_commit(lambda: _enviar_sunat(comp.pk))
    return cobro


# ---------------------------------------------------------------- costo de las unidades y entrega
def costo_proyecto(obra, desde=None, hasta=None, con_directos=True):
    """Costos del proyecto con el centro de costo de la obra: los gastos (clase 6, que se activan cada mes) y, con
    con_directos, lo cargado directo a inmuebles en construcción (terreno, licencias). Sin destinos, sin el reparto de
    gastos generales ni los asientos inmobiliarios."""
    from django.db.models import Q

    from contabilidad.models import AsientoLinea, CuentaDefecto
    if not obra.centro_costo_id:
        return D0
    cuentas = Q(cuenta__codigo__startswith='6')
    proceso = CuentaDefecto.objects.filter(clave='inmuebles_proceso').values_list('cuenta_id', flat=True).first()
    if con_directos and proceso:
        cuentas |= Q(cuenta_id=proceso)
    qs = AsientoLinea.objects.filter(cuentas, centro_costo_id=obra.centro_costo_id, es_destino=False).exclude(
        asiento__origen__in=ORIGENES_SIN_ACTIVAR)
    if desde:
        qs = qs.filter(asiento__fecha__gte=desde)
    if hasta:
        qs = qs.filter(asiento__fecha__lte=hasta)
    agg = qs.aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['d'] or D0) - (agg['h'] or D0)


def pesos(obra):
    """{unidad_id: peso} para repartir el costo: área techada o precio de lista (si no hay áreas, el precio)."""
    unidades = list(obra.unidades.all())
    campo = 'precio_lista' if obra.prorrateo == 'VALOR' else 'area_techada'
    salida = {u.pk: getattr(u, campo) or D0 for u in unidades}
    if not sum(salida.values(), D0):
        salida = {u.pk: u.precio_lista or D0 for u in unidades}
    return salida


def costo_unidades(obra, unidades, hasta=None, _pesos=None, _costo=None):
    p = _pesos if _pesos is not None else pesos(obra)
    total = sum(p.values(), D0)
    if not total:
        return D0
    costo = _costo if _costo is not None else costo_proyecto(obra, hasta=hasta)
    return r2(costo * sum((p.get(u.pk, D0) for u in unidades), D0) / total)


def ingreso_de(venta):
    """Ingreso de la venta: valor sin IGV (construcción + terreno) de sus comprobantes de anticipo vigentes."""
    from ventas.models import Venta
    ids = venta.cuotas.values_list('cobros__comprobante_id', flat=True)
    agg = Venta.objects.filter(pk__in=[i for i in ids if i], estado='REGISTRADO').aggregate(
        b=Sum('base_pen'), n=Sum('nograv_pen'))
    return (agg['b'] or D0) + (agg['n'] or D0)


@transaction.atomic
def entregar(venta, fecha=None):
    from contabilidad.automatico import marcar_pendiente
    from contabilidad.models import PeriodoContable
    fecha = fecha or timezone.localdate()
    if venta.estado != 'VENDIDA':
        raise ErrorInmobiliario('Solo se entregan ventas con la minuta firmada.' if venta.estado == 'SEPARADA' else
                                f'La venta está {venta.get_estado_display().lower()}.')
    if venta.saldo > 0:
        raise ErrorInmobiliario(f'Falta cobrar S/ {venta.saldo:,.2f}: la unidad se entrega pagada (incluido el '
                                f'desembolso del banco).')
    if PeriodoContable.esta_cerrado(fecha.strftime('%Y%m')):
        raise ErrorInmobiliario(f'El periodo {fecha:%m/%Y} está cerrado.')
    venta.estado, venta.fecha_entrega = 'ENTREGADA', fecha
    venta.ingreso = ingreso_de(venta)
    venta.costo = costo_unidades(venta.obra, list(venta.unidades.all()), hasta=fecha)
    venta.save(update_fields=['estado', 'fecha_entrega', 'ingreso', 'costo'])
    venta.unidades.update(estado='ENTREGADA')
    marcar_pendiente(fecha.strftime('%Y%m'))
    return venta


def asiento_inmobiliario(periodo, cta):
    """Costos del mes activados y unidades entregadas en el mes (con su ingreso y su costo recalculados)."""
    from contabilidad.centralizar import Borrador, _rango
    from contabilidad.models import Asiento
    obras = list(Obra.objects.filter(tipo='INMOBILIARIA').exclude(centro_costo=None).order_by('codigo'))
    if not obras:
        return None
    desde, hasta = _rango(periodo)
    a = Asiento(fecha=hasta, libro='05', origen='INMUEBLE',
                glosa=f'Proyectos inmobiliarios {periodo}: costos activados y unidades entregadas')
    b = Borrador(a)
    for obra in obras:
        costo = costo_proyecto(obra, desde, hasta, con_directos=False)  # lo cargado directo a la 2341 ya está allí
        if costo:
            b.neto(cta['inmuebles_proceso'], cta['inmuebles_variacion'], costo, documento=obra.codigo,
                   glosa=f'Costos del mes activados: {obra.nombre}')
        for v in (obra.ventas_inmuebles.filter(estado='ENTREGADA', fecha_entrega__range=[desde, hasta])
                  .select_related('cliente').prefetch_related('unidades')):
            ingreso = ingreso_de(v)
            costo_v = costo_unidades(obra, list(v.unidades.all()), hasta=v.fecha_entrega)
            if (ingreso, costo_v) != (v.ingreso, v.costo):
                VentaInmueble.objects.filter(pk=v.pk).update(ingreso=ingreso, costo=costo_v)
            glosa = f'Entrega {v.numero} {", ".join(u.codigo for u in v.unidades.all())} a {v.cliente.nombre}'
            b.add(cta['mov_ANTICIPO_INGRESO'], debe=ingreso, tercero=v.cliente, documento=v.numero, glosa=glosa)
            b.add(cta['inmuebles_venta'], haber=ingreso, documento=v.numero, centro_costo=obra.centro_costo,
                  glosa=glosa)
            b.add(cta['inmuebles_costo'], debe=costo_v, documento=v.numero, centro_costo=obra.centro_costo,
                  glosa=f'Costo: {glosa}')
            b.add(cta['inmuebles_proceso'], haber=costo_v, documento=v.numero, glosa=f'Costo: {glosa}')
    b.agregar_destinos()
    return b.grabar()


def al_volverse_inmobiliaria(obra):
    """Una obra que pasa a proyecto inmobiliario vuelve a centralizar sus meses con costos (para activarlos)."""
    from contabilidad.automatico import marcar_pendiente
    from contabilidad.models import AsientoLinea
    primera = (AsientoLinea.objects.filter(centro_costo_id=obra.centro_costo_id).order_by('asiento__fecha')
               .values_list('asiento__fecha', flat=True).first())
    if primera:
        marcar_pendiente(primera.strftime('%Y%m'))


# ---------------------------------------------------------------- tablero, cobranza y alertas
def tablero(obra):
    """Unidades con su estado, costo asignado, precio sin IGV y margen; resumen por estado y costo por m²."""
    from core.models import Empresa
    igv = Empresa.actual().igv_tasa
    unidades = list(obra.unidades.all())
    p = pesos(obra)
    costo = costo_proyecto(obra)
    ventas = {}
    for v in obra.ventas_inmuebles.exclude(estado='ANULADA').prefetch_related('unidades').select_related('cliente'):
        grupo = list(v.unidades.all())
        lista = sum((u.precio_lista for u in grupo), D0)
        for u in grupo:
            parte = v.precio * (u.precio_lista / lista) if lista else v.precio / len(grupo)
            ventas[u.pk] = (v, parte)
    filas = []
    for u in unidades:
        v, precio = ventas.get(u.pk, (None, u.precio_lista))
        c, _, t = partir(precio, (v.terreno_pct if v else obra.terreno_pct), igv)
        u.venta, u.precio = v, r2(precio)
        u.valor = c + t  # sin IGV
        u.costo = costo_unidades(obra, [u], _pesos=p, _costo=costo)
        u.margen = u.valor - u.costo
        u.margen_pct = (u.margen * 100 / u.valor).quantize(Decimal('0.1')) if u.valor else None
        filas.append(u)
    area = sum((u.area_techada for u in unidades), D0)
    resumen = {}
    for estado, nombre in Unidad.ESTADOS:
        grupo = [u for u in filas if u.estado == estado]
        resumen[estado] = {'nombre': nombre, 'n': len(grupo), 'monto': sum((u.precio for u in grupo), D0)}
    vendidas = [u for u in filas if u.estado in ('SEPARADA', 'VENDIDA', 'ENTREGADA')]
    return {'unidades': filas, 'resumen': resumen, 'costo': costo, 'area': area,
            'costo_m2': r2(costo / area) if area else None,
            'avance_ventas': (Decimal(len(vendidas)) * 100 / len(filas)).quantize(Decimal('0.1')) if filas else None,
            'por_cobrar': sum((v.saldo for v in obra.ventas_inmuebles.exclude(estado='ANULADA')), D0),
            'margen': sum((u.margen for u in filas), D0)}


def cuotas_vencidas(obra=None, hasta=None):
    hasta = hasta or timezone.localdate()
    qs = (CuotaInmueble.objects.exclude(venta__estado='ANULADA').filter(vence__lt=hasta)
          .annotate(pagado=Coalesce(Sum('cobros__monto'), Value(D0), output_field=DecimalField()))
          .filter(pagado__lt=F('monto')).select_related('venta__cliente', 'venta__obra').order_by('vence'))
    return qs.filter(venta__obra=obra) if obra is not None else qs


def separaciones_por_vencer(dias=DIAS_AVISO):
    return (VentaInmueble.objects.filter(estado='SEPARADA', vence_separacion__isnull=False,
                                         vence_separacion__lte=timezone.localdate() + timedelta(days=dias))
            .select_related('cliente', 'obra').order_by('vence_separacion'))
