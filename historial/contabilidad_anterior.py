"""Contabilidad del sistema anterior en los libros del ERP, hasta una fecha de corte.

- Se importa cada asiento del sistema anterior desde su apertura del ejercicio (la última «saldos iniciales al
  aperturar el ejercicio»): los meses anteriores a esa apertura ya están resumidos en ella y sumarlos duplicaría los
  saldos. Quedan con origen ANTERIOR, con las mismas cuentas, terceros y centros de costo.
- Hasta el corte el ERP no genera asientos propios (centralizar_periodo omite esos periodos) y los periodos quedan
  cerrados: así los libros son idénticos a los del sistema anterior.
- A la fecha de corte, un asiento MIGRACION pasa los saldos de facturas por cobrar y por pagar a las cuentas que usa el
  ERP y lleva las existencias contables al valor del kardex (la diferencia va a resultados acumulados), para que el
  ERP continúe desde ahí sin arrastrar diferencias a su primer mes.
"""
import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Min, Q, Sum

from .models import AsientoAnterior

D0 = Decimal('0')
LOTE = 5000
DIARIOS_VENTA = ('Facturas de cliente',)
DIARIOS_COMPRA = ('Facturas de proveedores', 'Facturas de proveedores servicios', 'Recibos por Honorarios')
BANCOS = ('BBVA', 'BCP', 'INTERBANK', 'SCOTIABANK', 'NACION', 'NACIÓN', 'CAJA')
EXISTENCIAS = ('20', '21', '22', '23', '24', '25', '26')
# cuentas del sistema anterior cuyo saldo pasa a la cuenta que usa el ERP: (prefijo, clave de CuentaDefecto)
RECLASIFICAR = [('1212', 'cliente'), ('4212', 'proveedor'), ('424', 'honorarios_por_pagar')]


class ErrorMigracion(Exception):
    pass


def periodo_apertura():
    """Periodo de la última apertura de ejercicio del sistema anterior (o el primero que haya)."""
    p = (AsientoAnterior.objects.filter(glosa__icontains='APERTURAR EL EJERCICIO').order_by('-fecha')
         .values_list('periodo', flat=True).first())
    return p or AsientoAnterior.objects.aggregate(p=Min('periodo'))['p'] or ''


def desde_apertura():
    """Asientos del sistema anterior desde su última apertura del ejercicio."""
    p = periodo_apertura()
    return AsientoAnterior.objects.filter(periodo__gte=p) if p else AsientoAnterior.objects.none()


def _libro(diario):
    d = diario.upper()
    if diario in DIARIOS_VENTA:
        return '14'
    if diario in DIARIOS_COMPRA:
        return '08'
    if any(b in d for b in BANCOS):
        return '01'
    return '05'


def _fin_mes(d):
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _cuentas(codigos, nombres):
    """{código: id} creando en el plan las cuentas del sistema anterior que falten."""
    from contabilidad.models import CuentaContable
    from contabilidad.pcge import naturaleza
    existentes = dict(CuentaContable.objects.filter(codigo__in=codigos).values_list('codigo', 'pk'))
    for c in sorted(set(codigos) - set(existentes)):
        existentes[c] = CuentaContable.objects.create(codigo=c[:12], nombre=(nombres.get(c) or c)[:200],
                                                      naturaleza=naturaleza(c), imputable=True).pk
    return existentes


def _saldos(prefijos, hasta, solo_cuentas=None):
    """{cuenta_id: (código, saldo debe - haber)} de los libros del ERP hasta la fecha."""
    from contabilidad.models import AsientoLinea
    filtro = Q()
    for p in prefijos:
        filtro |= Q(cuenta__codigo__startswith=p)
    qs = AsientoLinea.todas.filter(filtro, asiento__fecha__lte=hasta)
    if solo_cuentas is not None:
        qs = qs.filter(cuenta_id__in=solo_cuentas)
    return {cid: (cod, (d or D0) - (h or D0)) for cid, cod, d, h in qs.values_list('cuenta_id', 'cuenta__codigo')
            .annotate(d=Sum('debe'), h=Sum('haber')).values_list('cuenta_id', 'cuenta__codigo', 'd', 'h')}


@transaction.atomic
def importar(corte, log=print):
    """Importa los libros del sistema anterior hasta la fecha de corte y deja al ERP contabilizando desde el día
    siguiente. Se puede repetir: borra lo importado antes y lo vuelve a cargar."""
    from contabilidad.models import Asiento, AsientoLinea, CentroCosto, PeriodoContable
    from core.models import Empresa, Tercero
    if not AsientoAnterior.objects.exists():
        raise ErrorMigracion('No hay asientos del sistema anterior: impórtelos primero (importar_anteriores --solo contable).')
    if corte != _fin_mes(corte):
        raise ErrorMigracion('La fecha de corte debe ser el último día de un mes.')
    ultima = AsientoAnterior.objects.aggregate(f=Max('fecha'))['f']
    desde = periodo_apertura()
    periodo_corte = corte.strftime('%Y%m')
    Empresa.objects.update(fecha_corte_contable=corte)

    # 1) lo del ERP hasta el corte se reemplaza por los libros del sistema anterior (los manuales se conservan)
    borrados = Asiento.objects.filter(periodo__lte=periodo_corte).exclude(origen='MANUAL').delete()[0]
    log(f'Asientos del ERP hasta {corte:%d/%m/%Y} reemplazados: {borrados}')

    # 2) asientos del sistema anterior, uno por diario y voucher de cada mes
    qs = AsientoAnterior.objects.filter(periodo__gte=desde, fecha__lte=corte).exclude(debe=0, haber=0)
    nombres = dict(qs.values_list('cuenta', 'cuenta_nombre').distinct())
    cuentas = _cuentas([c for c in nombres if c], nombres)
    terceros = dict(Tercero.objects.values_list('numero_doc', 'pk'))
    centros = dict(CentroCosto.objects.values_list('codigo', 'pk'))
    sin_cuenta = qs.filter(cuenta='').aggregate(d=Sum('debe'), h=Sum('haber'))
    if sin_cuenta['d'] or sin_cuenta['h']:
        raise ErrorMigracion(f'Hay líneas sin cuenta con importe ({sin_cuenta}); corríjalas en el sistema anterior.')

    def centro_de(texto):
        import re
        m = re.match(r'\[(\w+)\]', texto or '')
        return centros.get(m.group(1)) if m else None

    contador, cabeceras, lineas_por = defaultdict(int), [], []
    n_lineas, actual, grupo = 0, None, []

    def cerrar_grupo():
        nonlocal grupo
        if not grupo:
            return
        a0 = grupo[0]
        libro = _libro(a0.diario)
        contador[(a0.periodo, libro)] += 1
        cabeceras.append(Asiento(
            numero=f'H{libro}-{a0.periodo}-{contador[(a0.periodo, libro)]:05d}', fecha=min(a.fecha for a in grupo),
            periodo=a0.periodo, libro=libro, origen='ANTERIOR',
            glosa=f'{a0.diario} {a0.voucher}: {a0.glosa}'[:250]))
        lineas_por.append([AsientoLinea(
            cuenta_id=cuentas[a.cuenta], tercero_id=terceros.get(a.contacto_doc), centro_costo_id=centro_de(a.centro_costo),
            documento=a.comprobante[:40], glosa=a.glosa[:200], debe=a.debe, haber=a.haber,
            es_destino=a.cuenta.startswith(('9', '79'))) for a in grupo])
        grupo = []

    def grabar():
        nonlocal n_lineas
        creados = Asiento.objects.bulk_create(cabeceras)
        lote = []
        for a, lineas in zip(creados, lineas_por):
            for l in lineas:
                l.asiento_id = a.pk
            lote += lineas
        for i in range(0, len(lote), LOTE):
            AsientoLinea.objects.bulk_create(lote[i:i + LOTE])
        n_lineas += len(lote)
        cabeceras.clear()
        lineas_por.clear()

    for a in qs.order_by('periodo', 'diario', 'voucher', 'id').iterator(chunk_size=10000):
        clave = (a.periodo, a.diario, a.voucher)
        if clave != actual:
            cerrar_grupo()
            actual = clave
            if len(cabeceras) >= 2000:
                grabar()
        grupo.append(a)
    cerrar_grupo()
    grabar()
    log(f'Asientos del sistema anterior importados: {sum(contador.values()):,} ({n_lineas:,} líneas) desde {desde}')

    # 3) ajustes de migración a la fecha de corte
    ajuste = asiento_migracion(corte)
    log(f'Asiento de ajustes de migración: {ajuste.numero if ajuste else "no hizo falta"}')

    # 4) periodos hasta el corte: cerrados (no se registran documentos con esas fechas) y sin pendientes
    primero = min(filter(None, [Asiento.objects.aggregate(p=Min('periodo'))['p'], desde]))
    anio, mes = int(primero[:4]), int(primero[4:])
    while f'{anio}{mes:02d}' <= periodo_corte:
        PeriodoContable.objects.update_or_create(periodo=f'{anio}{mes:02d}',
                                                 defaults={'cerrado': True, 'pendiente': False})
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    PeriodoContable.objects.filter(periodo__gt=periodo_corte, cerrado=False).update(pendiente=True)
    return {'desde': desde, 'corte': corte, 'ultima': ultima, 'asientos': sum(contador.values()), 'lineas': n_lineas,
            'ajuste': ajuste}


def asiento_migracion(corte):
    """Ajustes a la fecha de corte para que el ERP continúe desde ahí: facturas por cobrar y por pagar a las cuentas
    del ERP y existencias contables al valor del kardex (diferencia contra resultados acumulados)."""
    from contabilidad.centralizar import Borrador
    from contabilidad.models import Asiento, CuentaContable, CuentaDefecto
    from core.inventario import valor_inventario
    cta = CuentaDefecto.mapa()
    Asiento.objects.filter(origen='MIGRACION').delete()
    a = Asiento(fecha=corte, libro='05', origen='MIGRACION',
                glosa=f'Ajustes de migración al {corte:%d/%m/%Y}: saldos a las cuentas del ERP e inventario al kardex')
    b = Borrador(a)
    por_id = {c.pk: c for c in CuentaContable.objects.all()}
    # facturas por cobrar y por pagar: a la cuenta que usará el ERP para cobrarlas o pagarlas
    for prefijo, clave in RECLASIFICAR:
        destino = cta.get(clave)
        if destino is None:
            continue
        for cid, (codigo, saldo) in _saldos((prefijo,), corte).items():
            if cid == destino.pk or not saldo:
                continue
            b.neto(destino, por_id[cid], saldo, glosa=f'Saldo de {codigo} a la cuenta del ERP')
    # existencias: cada cuenta de existencias al valor del kardex de sus productos
    filas, _ = valor_inventario(corte)
    objetivo = defaultdict(lambda: D0)
    for f in filas:
        cuenta = f['p'].cuenta_existencias if f['p'].cuenta_existencias_id else cta['mercaderias']
        objetivo[cuenta.pk] += f['valor']
    libros = _saldos(EXISTENCIAS, corte)
    diferencia = D0
    for cid in set(objetivo) | set(libros):
        codigo = por_id[cid].codigo
        if not codigo.startswith(EXISTENCIAS):
            continue
        delta = objetivo.get(cid, D0) - libros.get(cid, (codigo, D0))[1]
        if delta:
            b.add(por_id[cid], debe=max(delta, D0), haber=max(-delta, D0),
                  glosa=f'{codigo}: existencias al valor del kardex')
            diferencia += delta
    if diferencia:
        b.add(cta['apertura_patrimonio'], debe=max(-diferencia, D0), haber=max(diferencia, D0),
              glosa='Diferencia entre el kardex y las existencias contables del sistema anterior')
    return b.grabar()


def conciliacion(hasta=None):
    """Auxiliares del ERP frente a sus cuentas contables: [(concepto, auxiliar, libros, diferencia, detalle)]."""
    from compras.models import Compra
    from contabilidad.models import AsientoLinea, CuentaDefecto
    from core.inventario import valor_inventario
    from core.models import Empresa
    from django.utils import timezone
    from finanzas.models import Cuenta
    from ventas.models import Venta
    hasta = hasta or timezone.localdate()
    cta = CuentaDefecto.mapa()

    def libro(cuentas):
        s = AsientoLinea.todas.filter(cuenta__in=cuentas, asiento__fecha__lte=hasta).aggregate(d=Sum('debe'),
                                                                                              h=Sum('haber'))
        return (s['d'] or D0) - (s['h'] or D0)

    filas = []
    cxc = sum((v.saldo_pen for v in Venta.objects.cobrables().con_saldos().filter(fecha_emision__lte=hasta)
               .exclude(tipo_comprobante__in=['07', '08'])), D0)
    filas.append(('Facturas por cobrar', cxc, libro([cta['cliente']]), f'Cuenta {cta["cliente"].codigo}'))
    cxp = sum((c.saldo_pen for c in Compra.objects.cobrables().con_saldos().filter(fecha_emision__lte=hasta)
               .exclude(tipo_comprobante__in=['07', '08'])), D0)
    cuentas_cxp = [cta['proveedor']] + ([cta['honorarios_por_pagar']] if cta.get('honorarios_por_pagar') else [])
    filas.append(('Facturas por pagar', cxp, -libro(cuentas_cxp),
                  'Cuentas ' + ', '.join(c.codigo for c in cuentas_cxp)))
    from core.tipo_cambio import venta_del_dia
    tc = venta_del_dia(hasta)
    bancos, cuentas_banco = D0, []
    for c in Cuenta.objects.filter(activo=True).select_related('cuenta_contable'):
        saldo = c.saldo_al(hasta)
        bancos += (saldo * tc).quantize(Decimal('0.01')) if c.moneda == 'USD' else saldo
        if c.cuenta_contable_id:
            cuentas_banco.append(c.cuenta_contable)
    filas.append(('Caja y bancos', bancos, libro(cuentas_banco), f'{len(cuentas_banco)} cuentas (dólares al T.C. {tc})'))
    _, inventario = valor_inventario(hasta)
    existencias = AsientoLinea.todas.filter(
        Q(*[Q(cuenta__codigo__startswith=p) for p in EXISTENCIAS], _connector=Q.OR),
        asiento__fecha__lte=hasta).aggregate(d=Sum('debe'), h=Sum('haber'))
    filas.append(('Inventario valorizado (kardex)', inventario, (existencias['d'] or D0) - (existencias['h'] or D0),
                  'Cuentas 20 a 26'))
    return [{'concepto': c, 'auxiliar': a, 'libros': l, 'diferencia': a - l, 'detalle': d} for c, a, l, d in filas], \
        Empresa.actual().fecha_corte_contable
