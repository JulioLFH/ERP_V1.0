"""Adelantos, valorizaciones y reajustes de la obra (OBR-13, OBR-14 y OBR-21).

- Adelanto directo o para materiales: factura de anticipo (Debe 1212 / Haber 122 Anticipos de clientes y 40111 IGV),
  con detracción de los contratos de construcción (código 030). Una factura ya emitida como venta se puede
  reclasificar a anticipo (sus líneas pasan al servicio «Adelanto de obra», cuya cuenta es la 122).
- Valorización mensual sobre el presupuesto contractual: metrado del periodo por partida (propuesto desde el avance
  físico), acumulado y saldo; gastos generales y utilidad en la proporción del presupuesto; amortización de los
  adelantos en proporción al avance (la última amortiza el saldo); reintegro por la fórmula polinómica
  R = V × (K − 1) menos su deducción por los adelantos D = A × (Kr / Ka − 1) (DS 011-79-VC); retención del fondo de
  garantía (% del contrato sobre el total de la factura). La aprueba el supervisor y se factura desde aquí.
- Factura de la valorización: lo valorizado (y el reintegro) en ventas de servicios; la amortización es la deducción
  del anticipo (regularización, línea negativa que referencia su factura): Debe 122 y el IGV solo sobre la diferencia.
  La retención del fondo de garantía es una aplicación sobre la factura (Debe 1683 / Haber 1212) que se cobra al
  recibir la obra (Tesorería). El resultado por obra cuenta lo valorizado, no los adelantos."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (AdelantoObra, AvancePartida, DevolucionGarantia, FormulaPolinomica, IndiceUnificado,
                     LineaValorizacion, Monomio, Presupuesto, Valorizacion)

D0 = Decimal('0')
VIGENTES = ('BORRADOR', 'POR_APROBAR', 'APROBADA', 'FACTURADA')


class ErrorValorizacion(Exception):
    pass


def r2(v):
    return Decimal(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def r3(v):
    return Decimal(v).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)


def periodo_de(fecha):
    return fecha.strftime('%Y%m')


def rango(periodo):
    desde = date(int(periodo[:4]), int(periodo[4:]), 1)
    hasta = (desde.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return desde, hasta


def _parametro(clave, fecha=None):
    from core import parametros
    return parametros.valor(clave, fecha)


def _usuario(u):
    return u if u is not None and getattr(u, 'is_authenticated', False) else None


# ---------------------------------------------------------------- presupuestos de la ejecución
def _vigente(obra, tipo):
    qs = Presupuesto.objects.filter(obra=obra, tipo=tipo)
    return qs.filter(estado='APROBADO').order_by('-version').first() or qs.order_by('-version').first()


def presupuesto_contractual(obra):
    """El contractual aprobado más reciente (o el último en borrador): sobre él se valoriza y se mide el avance."""
    return _vigente(obra, 'CONTRACTUAL')


def presupuesto_meta(obra):
    """La meta vigente (control de costos: tareo, partes de equipo, subcontratos); si no hay, el contractual."""
    return _vigente(obra, 'META') or presupuesto_contractual(obra)


def presupuesto_ejecucion(obra):
    return presupuesto_contractual(obra) or presupuesto_meta(obra)


def partidas_de(presupuesto):
    return list(presupuesto.partidas.filter(es_titulo=False).select_related('subpresupuesto')) if presupuesto else []


# ---------------------------------------------------------------- productos y comprobantes
def _producto(codigo, nombre, cuenta_clave):
    from contabilidad.models import CuentaDefecto
    from core.models import Producto
    p = Producto.objects.filter(codigo=codigo).first()
    if p is None:
        p = Producto(codigo=codigo, nombre=nombre, clase='SERVICIO', unidad='ZZ', puede_comprarse=False)
        p.cuenta_venta = CuentaDefecto.mapa().get(cuenta_clave)
        p.detraccion_pct = _parametro('OBR_DETRACCION_PCT')
        p.detraccion_codigo = '030'
        p.save()
    return p


def producto_adelanto():
    """Servicio «Adelanto de obra»: su cuenta de ventas es la de anticipos de clientes (122)."""
    return _producto('ANTOBRA', 'Adelanto de obra', 'mov_ANTICIPO_INGRESO')


def producto_valorizacion():
    return _producto('VALOBRA', 'Valorización de obra', 'ventas_servicios')


def _cliente(obra):
    if obra.cliente_id is None:
        raise ErrorValorizacion('Indique el cliente de la obra (Editar obra).')
    if obra.cliente.tipo_doc != '6':
        raise ErrorValorizacion(f'El cliente {obra.cliente.nombre} no tiene RUC: las valorizaciones se facturan.')
    return obra.cliente


def _validar_periodo(fecha):
    from contabilidad.models import PeriodoContable
    if PeriodoContable.esta_cerrado(periodo_de(fecha)):
        raise ErrorValorizacion(f'El periodo {fecha:%m/%Y} está cerrado.')


def _factura(obra, fecha, glosa, lineas):
    """Factura al cliente de la obra con lineas [(producto, descripción, valor sin IGV, anticipo_de)], al crédito a
    30 días y con la detracción de sus productos."""
    from core.models import Serie
    from ventas.forms import _serie_defecto
    from ventas.models import Venta, VentaItem

    from .inmobiliario import _enviar_si_corresponde
    cliente = _cliente(obra)
    serie, numero = Serie.siguiente('01', _serie_defecto('01'))
    comp = Venta.objects.create(
        tipo_comprobante='01', serie=serie, numero=numero, tercero=cliente, fecha_emision=fecha,
        fecha_vencimiento=fecha + timedelta(days=30), forma_pago='CREDITO', cuotas=1, tipo_operacion='GRAVADA',
        descontar_stock=False, centro_costo=obra.centro_costo, glosa=glosa[:500])
    for producto, descripcion, valor, anticipo_de in lineas:
        VentaItem.objects.create(documento=comp, producto=producto, cantidad=1, precio_unitario=valor,
                                 afectacion='GRAVADA', descripcion=descripcion[:250], anticipo_de=anticipo_de)
    comp.calcular_totales()
    comp.proponer_detraccion()
    comp.save()
    _enviar_si_corresponde(comp)
    return comp


# ---------------------------------------------------------------- adelantos (OBR-13)
@transaction.atomic
def registrar_adelanto(obra, tipo, monto, fecha, usuario, glosa=''):
    """Factura del adelanto (anticipo con IGV y detracción) y su registro para amortizarlo."""
    monto = r2(monto or 0)
    if monto <= 0:
        raise ErrorValorizacion('Indique el monto del adelanto sin IGV.')
    if tipo not in dict(AdelantoObra.TIPOS):
        raise ErrorValorizacion('Elija el tipo de adelanto.')
    fecha = fecha or timezone.localdate()
    _validar_periodo(fecha)
    texto = (f'{dict(AdelantoObra.TIPOS)[tipo]} de la obra {obra.nombre}'
             + (f' – contrato {obra.contrato}' if obra.contrato else ''))
    comp = _factura(obra, fecha, glosa or texto, [(producto_adelanto(), texto, monto, None)])
    return AdelantoObra.objects.create(obra=obra, tipo=tipo, fecha=fecha, monto=monto, factura=comp,
                                       glosa=(glosa or texto)[:250], creado_por=_usuario(usuario))


@transaction.atomic
def reclasificar(venta, obra, tipo, usuario, motivo=''):
    """Una factura emitida como venta era el adelanto de la obra: sus líneas pasan al servicio «Adelanto de obra»
    (cuenta 122), queda con el centro de costo de la obra y se amortiza en las valorizaciones. El comprobante ante
    SUNAT no cambia (un anticipo es una factura gravada); cambia su contabilidad."""
    from contabilidad.automatico import marcar_pendiente
    from core.auditoria import registrar
    if venta.tipo_comprobante != '01' or venta.estado != 'REGISTRADO':
        raise ErrorValorizacion('Solo una factura vigente se reclasifica como adelanto.')
    if AdelantoObra.objects.filter(factura=venta).exists():
        raise ErrorValorizacion(f'{venta} ya es un adelanto de obra.')
    if venta.items.filter(anticipo_de__isnull=False).exists() or venta.notas.filter(estado='REGISTRADO').exists():
        raise ErrorValorizacion(f'{venta} tiene deducciones de anticipos o notas: no se reclasifica.')
    if obra.cliente_id and venta.tercero_id != obra.cliente_id:
        raise ErrorValorizacion(f'{venta} es de {venta.tercero.nombre} y el cliente de la obra es '
                                f'{obra.cliente.nombre}.')
    if tipo not in dict(AdelantoObra.TIPOS):
        raise ErrorValorizacion('Elija el tipo de adelanto.')
    _validar_periodo(venta.fecha_emision)
    antes = {i.pk: (i.producto.codigo if i.producto_id else '') for i in venta.items.select_related('producto')}
    producto = producto_adelanto()
    venta.items.update(producto=producto)
    if venta.centro_costo_id != obra.centro_costo_id:
        venta.centro_costo = obra.centro_costo
        venta.save(update_fields=['centro_costo'])
    adelanto = AdelantoObra.objects.create(
        obra=obra, tipo=tipo, fecha=venta.fecha_emision, monto=venta.base_imponible + venta.no_gravado,
        factura=venta, reclasificada=True, creado_por=_usuario(usuario),
        glosa=f'Reclasificada a anticipo: {motivo}'[:250] if motivo else 'Factura reclasificada a anticipo')
    registrar('MODIFICAR', venta, {'reclasificada_a_anticipo': obra.codigo, 'productos_antes': antes},
              motivo or 'Factura de adelanto de obra registrada como venta')
    marcar_pendiente(venta.periodo)  # la contabilidad del periodo se regenera con la 122
    if obra.cliente_id is None:
        obra.cliente = venta.tercero
        obra.save(update_fields=['cliente'])
    return adelanto


def adelantos(obra, tipo=None):
    qs = AdelantoObra.objects.filter(obra=obra, factura__estado='REGISTRADO').select_related('factura')
    return qs.filter(tipo=tipo) if tipo else qs


def saldo_adelanto(obra, tipo, excluir=None):
    """Adelantos recibidos menos lo amortizado en las valorizaciones vigentes (sin IGV)."""
    total = sum((a.monto for a in adelantos(obra, tipo)), D0)
    campo = 'amort_directo' if tipo == 'DIRECTO' else 'amort_materiales'
    qs = Valorizacion.objects.filter(obra=obra, estado__in=VIGENTES)
    if excluir is not None and excluir.pk:
        qs = qs.exclude(pk=excluir.pk)
    return total - (qs.aggregate(s=Sum(campo))['s'] or D0)


# ---------------------------------------------------------------- fórmula polinómica (OBR-21)
def indice(codigo, area, periodo):
    i = IndiceUnificado.objects.filter(codigo=codigo, area=area, periodo=periodo).first()
    return i.valor if i else None


def coeficiente_k(formula, periodo):
    """(K redondeado al milésimo, [índices que faltan]) del mes: Σ coeficiente × Ir / Io."""
    k, faltan = D0, []
    for m in formula.monomios.all():
        io, ir = indice(m.indice, formula.area, formula.periodo_base), indice(m.indice, formula.area, periodo)
        if not io or ir is None:
            faltan.append(f'IU {m.indice} ({formula.periodo_base if not io else periodo})')
            continue
        k += m.coeficiente * ir / io
    return (None if faltan else r3(k)), faltan


def _formulas(obra):
    return list(FormulaPolinomica.objects.filter(obra=obra, activa=True).prefetch_related('monomios'))


def reajuste(v, lineas_monto):
    """(reintegro, deducción, K principal, avisos) de la valorización con las fórmulas activas de la obra.
    lineas_monto: {código de subpresupuesto: costo directo valorizado}."""
    formulas = _formulas(v.obra)
    if not formulas:
        return D0, D0, None, []
    p = v.presupuesto
    factor = (p.subtotal / p.costo_directo) if p.costo_directo else Decimal('1')  # con GG y utilidad
    reintegro, avisos, k_principal = D0, [], None
    total_cd = sum(lineas_monto.values(), D0)
    for f in formulas:
        k, faltan = coeficiente_k(f, v.periodo)
        if k is None:
            avisos.append(f'{f}: faltan los índices {", ".join(faltan)}; el reintegro se calcula cuando se carguen.')
            continue
        if k_principal is None:
            k_principal = k
        base = lineas_monto.get(f.subpresupuesto, D0) if f.subpresupuesto else total_cd
        if not f.subpresupuesto and len(formulas) > 1:  # la general cubre lo que no tiene fórmula propia
            base = total_cd - sum((lineas_monto.get(g.subpresupuesto, D0) for g in formulas if g.subpresupuesto), D0)
        reintegro += r2(base * factor * (k - 1))
    deduccion = D0
    if k_principal is not None:
        principal = formulas[0]
        for tipo, amort in (('DIRECTO', v.amort_directo), ('MATERIALES', v.amort_materiales)):
            if not amort:
                continue
            primero = adelantos(v.obra, tipo).order_by('fecha').first()
            if primero is None:
                continue
            ka, faltan = coeficiente_k(principal, periodo_de(primero.fecha))
            if ka is None:
                avisos.append(f'Deducción por el adelanto {tipo.lower()}: faltan los índices {", ".join(faltan)}.')
                continue
            deduccion += r2(amort * (k_principal / ka - 1))
    return reintegro, deduccion, k_principal, avisos


def _celda_decimal(v):
    if v in (None, ''):
        return None
    try:
        return Decimal(str(v).replace(',', '').strip())
    except Exception:
        return None


def _codigo_indice(v):
    texto = str(v or '').strip()
    if texto.endswith('.0'):
        texto = texto[:-2]
    return texto.zfill(2) if texto.isdigit() and 1 <= int(texto) <= 99 else None


def leer_formula(filas):
    """[(símbolo, coeficiente, índice, descripción)] desde las filas de una hoja (reporte de fórmula polinómica del
    S10 o plantilla). Reconoce la cabecera (símbolo, factor/coeficiente, %, índice, descripción); los monomios
    compuestos (filas con % y sin factor) multiplican el factor del monomio por su %."""
    cab, inicio = None, 0
    for n, fila in enumerate(filas):
        textos = [str(c or '').strip().lower() for c in fila]
        if any('ndice' in t for t in textos) and any(t.startswith(('factor', 'coef')) for t in textos):
            cab = textos
            inicio = n + 1
            break
    if cab is None:
        cab = ['símbolo', 'coeficiente', 'índice', 'descripción']

    def col(*nombres):  # el primer nombre manda (la columna «Monomio» es el número; el símbolo va en «Símbolo»)
        for x in nombres:
            for i, t in enumerate(cab):
                if t.startswith(x):
                    return i
        return None
    c_sim, c_coef, c_pct = col('símbolo', 'simbolo', 'monomio'), col('factor', 'coef'), col('%', '(%)', 'porc')
    c_ind, c_desc = col('índice', 'indice', 'ind'), col('descrip')
    salida, factor_actual, simbolo_actual = [], None, ''
    for fila in filas[inicio:]:
        fila = list(fila) + [None] * 8

        def v(i):
            return fila[i] if i is not None and i < len(fila) else None
        codigo = _codigo_indice(v(c_ind))
        if codigo is None:
            continue
        coef, pct = _celda_decimal(v(c_coef)), _celda_decimal(v(c_pct))
        simbolo = str(v(c_sim) or '').strip()
        if simbolo and not simbolo.replace('.', '').isdigit():
            simbolo_actual = simbolo[:10]
        if coef is not None and coef > 0:
            factor_actual = coef
            if pct is not None and 0 < pct < 100:  # primera parte de un monomio compuesto
                coef = factor_actual * pct / 100
        elif pct is not None and factor_actual is not None:
            coef = factor_actual * pct / 100
        else:
            continue
        salida.append((simbolo_actual or codigo, r3(coef), codigo, str(v(c_desc) or '').strip()[:120]))
    return salida


@transaction.atomic
def guardar_formula(obra, filas, numero=1, nombre='', subpresupuesto='', area='1', periodo_base=''):
    """Crea o reemplaza la fórmula N° numero con sus monomios [(símbolo, coeficiente, índice, descripción)]; la
    suma de los coeficientes debe ser 1."""
    if not filas:
        raise ErrorValorizacion('No se encontraron monomios (símbolo, coeficiente e índice de 2 dígitos).')
    suma = sum((Decimal(c) for _, c, _, _ in filas), D0)
    if abs(suma - 1) > Decimal('0.001'):
        raise ErrorValorizacion(f'Los coeficientes suman {suma:.3f}: deben sumar 1.000.')
    if not (len(periodo_base) == 6 and periodo_base.isdigit()):
        raise ErrorValorizacion('Indique el mes de los índices base (presupuesto base).')
    f, _ = FormulaPolinomica.objects.update_or_create(obra=obra, numero=numero, defaults={
        'nombre': nombre[:150], 'subpresupuesto': subpresupuesto[:10], 'area': area, 'periodo_base': periodo_base,
        'activa': True})
    f.monomios.all().delete()
    for simbolo, coef, codigo, descripcion in filas:
        Monomio.objects.create(formula=f, simbolo=simbolo[:10], coeficiente=coef, indice=codigo,
                               descripcion=descripcion[:120])
    return f


def leer_indices(filas):
    """{(código, área): valor} de la tabla del INEI (código y los valores de las áreas 1 a 6)."""
    salida = {}
    for fila in filas:
        celdas = list(fila)
        if not celdas:
            continue
        codigo = _codigo_indice(celdas[0])
        if codigo is None:
            continue
        valores = [_celda_decimal(c) for c in celdas[1:]]
        valores = [x for x in valores if x is not None]
        for area, valor in enumerate(valores[:6], 1):
            if valor > 0:
                salida[(codigo, str(area))] = valor
    return salida


@transaction.atomic
def guardar_indices(periodo, valores):
    if not (len(periodo) == 6 and periodo.isdigit()):
        raise ErrorValorizacion('Indique el mes de los índices (AAAAMM).')
    if not valores:
        raise ErrorValorizacion('No se encontraron índices (código de 2 dígitos y valores por área).')
    for (codigo, area), valor in valores.items():
        IndiceUnificado.objects.update_or_create(codigo=codigo, area=area, periodo=periodo,
                                                 defaults={'valor': valor})
    return len(valores)


# ---------------------------------------------------------------- valorizaciones (OBR-14)
def valorizado_acumulado(obra, excluir=None, hasta_numero=None):
    """{clave de partida: (metrado, monto)} valorizado en las valorizaciones vigentes de la obra."""
    qs = LineaValorizacion.objects.filter(valorizacion__obra=obra, valorizacion__estado__in=VIGENTES)
    if excluir is not None and excluir.pk:
        qs = qs.exclude(valorizacion=excluir)
    if hasta_numero is not None:
        qs = qs.filter(valorizacion__numero__lt=hasta_numero)
    salida = defaultdict(lambda: [D0, D0])
    for l in qs.select_related('partida__subpresupuesto'):
        salida[l.partida.clave][0] += l.metrado
        salida[l.partida.clave][1] += l.monto
    return salida


def avance_acumulado(obra, hasta=None):
    """{clave de partida: metrado ejecutado acumulado} (avance físico hasta la fecha)."""
    qs = AvancePartida.objects.filter(obra=obra)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    salida = defaultdict(lambda: D0)
    for a in qs.select_related('partida__subpresupuesto'):
        salida[a.partida.clave] += a.metrado
    return salida


@transaction.atomic
def nueva(obra, periodo, usuario, fecha_inicio=None, fecha_fin=None):
    """Valorización del mes con todas las partidas del contractual y el metrado propuesto desde el avance físico
    (lo ejecutado hasta el fin del periodo menos lo ya valorizado)."""
    p = presupuesto_contractual(obra)
    if p is None:
        raise ErrorValorizacion('La obra no tiene presupuesto contractual: cópielo desde la oferta o la meta y '
                                'apruébelo.')
    if not (len(periodo or '') == 6 and periodo.isdigit()):
        raise ErrorValorizacion('Indique el mes que se valoriza.')
    abiertas = obra.valorizaciones.filter(estado__in=('BORRADOR', 'POR_APROBAR')).first()
    if abiertas:
        raise ErrorValorizacion(f'{abiertas} sigue abierta: apruébela o anúlela antes de crear otra.')
    if obra.valorizaciones.filter(periodo=periodo, estado__in=VIGENTES).exists():
        raise ErrorValorizacion(f'El mes {periodo[4:]}/{periodo[:4]} ya está valorizado.')
    desde, hasta = rango(periodo)
    v = Valorizacion.objects.create(obra=obra, presupuesto=p, periodo=periodo, fecha_inicio=fecha_inicio or desde,
                                    fecha_fin=fecha_fin or hasta, supervisor=obra.supervisor[:150],
                                    creado_por=_usuario(usuario))
    previo, avance = valorizado_acumulado(obra, excluir=v), avance_acumulado(obra, v.fecha_fin)
    for partida in partidas_de(p):
        propuesto = max(avance.get(partida.clave, D0) - previo.get(partida.clave, (D0, D0))[0], D0)
        propuesto = min(propuesto, max(partida.metrado - previo.get(partida.clave, (D0, D0))[0], D0))
        LineaValorizacion.objects.create(valorizacion=v, partida=partida, metrado=propuesto, precio=partida.precio)
    calcular(v, recalcular_amortizacion=True)
    return v


def calcular(v, metrados=None, amortizaciones=None, retencion=None, recalcular_amortizacion=False):
    """Recalcula la valorización. metrados {línea_id: metrado}; amortizaciones {'DIRECTO'|'MATERIALES': monto}
    escritas a mano; retencion: monto a mano. Devuelve los avisos."""
    if not v.editable:
        raise ErrorValorizacion(f'La valorización está {v.get_estado_display().lower()}.')
    from core.models import Empresa
    p = v.presupuesto
    previo = valorizado_acumulado(v.obra, excluir=v)
    excesos, por_sub = [], defaultdict(lambda: D0)
    lineas = list(v.lineas.select_related('partida__subpresupuesto'))
    for l in lineas:
        if metrados is not None and l.pk in metrados:
            l.metrado = Decimal(metrados[l.pk] or 0)
        l.precio = l.partida.precio
        l.monto = r2(l.metrado * l.precio)
        acumulado = previo.get(l.partida.clave, (D0, D0))[0] + l.metrado
        if acumulado > l.partida.metrado + Decimal('0.0001'):
            excesos.append(f'{l.partida.item} {l.partida.descripcion[:40]} (acumulado {acumulado:,.2f} de '
                           f'{l.partida.metrado:,.2f})')
        if l.metrado < 0:
            excesos.append(f'{l.partida.item}: el metrado no puede ser negativo')
        por_sub[l.partida.subpresupuesto.codigo if l.partida.subpresupuesto_id else ''] += l.monto
    if excesos:
        raise ErrorValorizacion('Supera el metrado contractual (registre el adicional como nueva versión del '
                                'presupuesto): ' + '; '.join(excesos[:5]))
    LineaValorizacion.objects.bulk_update(lineas, ['metrado', 'precio', 'monto'])
    v.costo_directo = sum((l.monto for l in lineas), D0)
    if p.costo_directo:
        v.gastos_generales = r2(v.costo_directo * p.gastos_generales / p.costo_directo)
        v.utilidad = r2(v.costo_directo * p.utilidad / p.costo_directo)
    else:
        v.gastos_generales = v.utilidad = D0
    v.subtotal = v.costo_directo + v.gastos_generales + v.utilidad
    avisos = []
    # amortización de los adelantos: proporcional al avance (adelanto / contrato); la que completa el contrato, el saldo
    contrato = p.subtotal or D0
    acumulado_val = sum((m for _, m in previo.values()), D0)
    completa = contrato and (acumulado_val + v.costo_directo) >= (p.costo_directo - Decimal('1'))
    for tipo, campo in (('DIRECTO', 'amort_directo'), ('MATERIALES', 'amort_materiales')):
        saldo = saldo_adelanto(v.obra, tipo, excluir=v)
        if amortizaciones is not None and tipo in amortizaciones:
            monto = r2(amortizaciones[tipo] or 0)
        elif recalcular_amortizacion or getattr(v, campo) > saldo:
            recibido = sum((a.monto for a in adelantos(v.obra, tipo)), D0)
            monto = saldo if completa else r2(v.subtotal * recibido / contrato) if contrato else D0
        else:
            monto = getattr(v, campo)
        if monto < 0 or monto > saldo + Decimal('0.01'):
            raise ErrorValorizacion(f'La amortización del adelanto {tipo.lower()} debe estar entre 0 y su saldo '
                                    f'(S/ {saldo:,.2f}).')
        setattr(v, campo, min(max(monto, D0), max(saldo, D0)))
    v.reintegro, v.deduccion_reintegro, v.k, avisos_k = reajuste(v, por_sub)
    avisos += avisos_k
    v.base_factura = v.subtotal + v.reintegro - v.deduccion_reintegro - v.amort_directo - v.amort_materiales
    if v.base_factura < 0:
        raise ErrorValorizacion('Las amortizaciones superan lo valorizado: redúzcalas.')
    v.igv = r2(v.base_factura * Empresa.actual().igv_tasa / 100)
    v.total_factura = v.base_factura + v.igv
    if retencion is not None:
        v.retencion_garantia = r2(retencion or 0)
    else:
        v.retencion_garantia = r2(v.total_factura * (v.obra.retencion_garantia_pct or D0) / 100)
    if v.retencion_garantia < 0 or v.retencion_garantia > v.total_factura:
        raise ErrorValorizacion('La retención del fondo de garantía no puede superar el total de la factura.')
    v.save()
    return avisos


def acumulados(v):
    """Filas de la valorización con lo anterior, el periodo, el acumulado y el saldo por partida (orden del S10)."""
    previo = valorizado_acumulado(v.obra, hasta_numero=v.numero, excluir=v)
    filas = []
    for l in v.lineas.select_related('partida__subpresupuesto'):
        pa = l.partida
        ant_m, ant_v = previo.get(pa.clave, (D0, D0))
        filas.append({'linea': l, 'partida': pa, 'anterior': ant_m, 'anterior_monto': ant_v,
                      'acumulado': ant_m + l.metrado, 'acumulado_monto': ant_v + l.monto,
                      'saldo': pa.metrado - ant_m - l.metrado, 'saldo_monto': pa.parcial - ant_v - l.monto,
                      'avance': ((ant_m + l.metrado) / pa.metrado * 100).quantize(Decimal('0.1'))
                      if pa.metrado else D0})
    return filas


def enviar(v, usuario):
    if v.estado != 'BORRADOR':
        raise ErrorValorizacion('Solo se envía una valorización en borrador.')
    if v.costo_directo <= 0:
        raise ErrorValorizacion('La valorización no tiene metrados.')
    v.estado = 'POR_APROBAR'
    v.save(update_fields=['estado'])
    return v


def observar(v, usuario, observacion):
    if v.estado != 'POR_APROBAR':
        raise ErrorValorizacion('Solo se observa una valorización por aprobar.')
    if len((observacion or '').strip()) < 5:
        raise ErrorValorizacion('Escriba la observación del supervisor.')
    v.estado, v.observacion = 'BORRADOR', observacion.strip()[:300]
    v.save(update_fields=['estado', 'observacion'])
    return v


def aprobar(v, usuario, supervisor, fecha):
    """Registra la aprobación del supervisor o inspector (nombre y fecha)."""
    if v.estado != 'POR_APROBAR':
        raise ErrorValorizacion('Solo se aprueba una valorización enviada al supervisor.')
    if not (supervisor or '').strip():
        raise ErrorValorizacion('Indique el supervisor o inspector que aprobó la valorización.')
    v.estado, v.supervisor = 'APROBADA', supervisor.strip()[:150]
    v.fecha_aprobacion, v.aprobado_por = fecha or timezone.localdate(), _usuario(usuario)
    v.save(update_fields=['estado', 'supervisor', 'fecha_aprobacion', 'aprobado_por'])
    return v


@transaction.atomic
def facturar(v, usuario, fecha=None):
    """Factura de la valorización: lo valorizado, el reintegro neto y la deducción de cada adelanto (regularización
    del anticipo); la retención del fondo de garantía se aplica sobre la factura."""
    from finanzas.models import Aplicacion
    if v.estado != 'APROBADA':
        raise ErrorValorizacion('Solo se factura una valorización aprobada por el supervisor.')
    fecha = fecha or timezone.localdate()
    _validar_periodo(fecha)
    obra, mes = v.obra, f'{v.periodo[4:]}/{v.periodo[:4]}'
    prod_val, prod_ant = producto_valorizacion(), producto_adelanto()
    lineas = [(prod_val, f'Valorización N° {v.numero} ({mes}) – {obra.nombre}', v.subtotal, None)]
    if v.reintegro - v.deduccion_reintegro:
        lineas.append((prod_val, f'Reintegro de la valorización N° {v.numero} (fórmula polinómica, K = {v.k}) neto de '
                                 f'la deducción por adelantos', v.reintegro - v.deduccion_reintegro, None))
    for tipo, monto in (('DIRECTO', v.amort_directo), ('MATERIALES', v.amort_materiales)):
        resto = monto
        for a in adelantos(obra, tipo).order_by('fecha', 'id'):
            if resto <= 0:
                break
            parte = min(resto, a.saldo)
            if parte <= 0:
                continue
            lineas.append((prod_ant, f'Deducción del anticipo {a.factura.numero_completo} '
                                     f'({a.get_tipo_display().lower()})', -parte, a.factura))
            resto -= parte
        if resto > Decimal('0.01'):
            raise ErrorValorizacion(f'Los adelantos {tipo.lower()} no tienen saldo para amortizar S/ {monto:,.2f}.')
    comp = _factura(obra, fecha, f'Valorización N° {v.numero} ({mes}) de la obra {obra.nombre}'
                    + (f' – contrato {obra.contrato}' if obra.contrato else ''), lineas)
    v.factura, v.estado = comp, 'FACTURADA'
    if v.retencion_garantia:
        v.aplicacion_garantia = Aplicacion.objects.create(
            origen='GARANTIA', fecha=fecha, venta=comp, monto_doc=min(v.retencion_garantia, comp.total),
            creado_por=_usuario(usuario), glosa=f'Fondo de garantía retenido: valorización N° {v.numero} de '
                                                f'{obra.codigo}'[:250])
    v.save(update_fields=['factura', 'estado', 'aplicacion_garantia'])
    return comp


@transaction.atomic
def anular(v, usuario, motivo):
    if v.estado in ('FACTURADA', 'ANULADA'):
        raise ErrorValorizacion('Una valorización facturada se corrige con nota de crédito y una nueva valorización.'
                                if v.estado == 'FACTURADA' else 'La valorización ya está anulada.')
    if len((motivo or '').strip()) < 5:
        raise ErrorValorizacion('Indique el motivo de la anulación.')
    v.estado, v.observacion = 'ANULADA', motivo.strip()[:300]
    v.save(update_fields=['estado', 'observacion'])
    return v


# ---------------------------------------------------------------- fondo de garantía
def garantia(obra):
    """(retenido por el cliente, devuelto, saldo) del fondo de garantía de la obra."""
    retenido = sum((v.aplicacion_garantia.monto_doc for v in obra.valorizaciones.filter(
        aplicacion_garantia__isnull=False, aplicacion_garantia__estado='VIGENTE').select_related(
        'aplicacion_garantia')), D0)
    devuelto = sum((d.monto for d in obra.devoluciones_garantia.filter(movimiento__estado='VIGENTE')), D0)
    return retenido, devuelto, retenido - devuelto


@transaction.atomic
def cobrar_garantia(obra, monto, fecha, cuenta, usuario, numero_operacion=''):
    """El cliente devuelve el fondo de garantía (recepción o liquidación de la obra): ingreso a bancos contra la
    cuenta del fondo por cobrar."""
    from contabilidad.models import CuentaDefecto
    from finanzas.models import Movimiento
    monto = r2(monto or 0)
    _, _, saldo = garantia(obra)
    if monto <= 0 or monto > saldo:
        raise ErrorValorizacion(f'El cobro debe ser mayor a cero y hasta el saldo del fondo (S/ {saldo:,.2f}).')
    if cuenta is None:
        raise ErrorValorizacion('Elija la cuenta donde ingresa el cobro.')
    fecha = fecha or timezone.localdate()
    _validar_periodo(fecha)
    mov = Movimiento.objects.create(
        cuenta=cuenta, fecha=fecha, tipo='INGRESO', concepto='OTRO', medio_pago='TRANSFERENCIA',
        numero_operacion=(numero_operacion or '')[:40], tercero=obra.cliente, monto=monto,
        cuenta_contable=CuentaDefecto.mapa()['obras_garantia'], creado_por=_usuario(usuario),
        glosa=f'Devolución del fondo de garantía de la obra {obra.codigo} {obra.nombre}'[:250])
    return DevolucionGarantia.objects.create(obra=obra, fecha=fecha, monto=monto, movimiento=mov)


def resumen(obra):
    """Contrato, adelantos y su saldo, valorizado acumulado, facturado y fondo de garantía."""
    p = presupuesto_contractual(obra)
    vigentes = list(obra.valorizaciones.filter(estado__in=VIGENTES))
    valorizado = sum((v.subtotal for v in vigentes), D0)
    retenido, devuelto, saldo_g = garantia(obra)
    return {
        'presupuesto': p, 'contrato': p.subtotal if p else D0,
        'valorizado': valorizado, 'avance_pct': (valorizado / p.subtotal * 100).quantize(Decimal('0.1'))
        if p and p.subtotal else D0,
        'facturado': sum((v.total_factura for v in vigentes if v.estado == 'FACTURADA'), D0),
        'reintegros': sum((v.reintegro - v.deduccion_reintegro for v in vigentes), D0),
        'adelantos': [{'tipo': t, 'nombre': n, 'recibido': sum((a.monto for a in adelantos(obra, t)), D0),
                       'saldo': saldo_adelanto(obra, t)} for t, n in AdelantoObra.TIPOS],
        'garantia_retenida': retenido, 'garantia_devuelta': devuelto, 'garantia_saldo': saldo_g,
    }
