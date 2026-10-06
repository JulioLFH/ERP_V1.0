"""Cierre, pago, asiento contable y archivos PLAME de las planillas."""
import io
import zipfile
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction

from .calculo import ErrorPlanilla, r2, rango
from .models import ConceptoPlanilla, FilaPlanilla, Planilla

D0 = Decimal('0')
TIPO_DOC_PLAME = {'01': '01', '04': '04', '07': '07'}


def _marcar(periodo):
    from contabilidad.automatico import marcar_pendiente
    marcar_pendiente(periodo, posteriores=False)


@transaction.atomic
def cerrar(planilla):
    if planilla.estado != 'CALCULADA':
        raise ErrorPlanilla('Calcule la planilla antes de cerrarla.')
    if not planilla.filas.exists():
        raise ErrorPlanilla('La planilla no tiene trabajadores.')
    planilla.estado = 'CERRADA'
    planilla.save(update_fields=['estado'])
    _marcar(planilla.periodo)


@transaction.atomic
def reabrir(planilla):
    if planilla.estado == 'PAGADA':
        raise ErrorPlanilla('La planilla ya se pagó: anule primero el pago en Finanzas › Movimientos.')
    if planilla.estado != 'CERRADA':
        raise ErrorPlanilla('Solo se reabre una planilla cerrada.')
    from contabilidad.models import PeriodoContable
    if PeriodoContable.esta_cerrado(planilla.periodo):
        raise ErrorPlanilla(f'El periodo contable {planilla.periodo} está cerrado.')
    planilla.estado = 'CALCULADA'
    planilla.save(update_fields=['estado'])
    _marcar(planilla.periodo)


@transaction.atomic
def pagar(planilla, cuenta, fecha, usuario=None):
    """Registra en tesorería el egreso por el neto total (concepto Planilla → 4111)."""
    from finanzas.models import Movimiento
    if planilla.estado != 'CERRADA':
        raise ErrorPlanilla('Cierre la planilla antes de pagarla.')
    neto = planilla.totales()['n']
    if neto <= 0:
        raise ErrorPlanilla('La planilla no tiene neto a pagar.')
    if cuenta.moneda != 'PEN':
        raise ErrorPlanilla('Pague la planilla desde una cuenta en soles.')
    if not cuenta.admite_negativo and cuenta.saldo_al(fecha) < neto:
        raise ErrorPlanilla(f'Saldo insuficiente en {cuenta}: S/ {cuenta.saldo_al(fecha):,.2f} para pagar '
                            f'S/ {neto:,.2f}.')
    mov = Movimiento.objects.create(cuenta=cuenta, fecha=fecha, tipo='EGRESO', concepto='PLANILLA',
                                    medio_pago='TRANSFERENCIA', monto=neto, glosa=f'Pago {planilla}'[:250],
                                    creado_por=usuario)
    planilla.estado, planilla.movimiento_pago, planilla.fecha_pago = 'PAGADA', mov, fecha
    planilla.save(update_fields=['estado', 'movimiento_pago', 'fecha_pago'])
    return mov


def liberar_pago(movimiento):
    """Al anular el movimiento de pago la planilla vuelve a 'Cerrada'."""
    Planilla.objects.filter(movimiento_pago=movimiento).update(estado='CERRADA', movimiento_pago=None)


# ---------------------------------------------------------------- contabilidad
def asiento_planillas(periodo, cta):
    """Gasto de personal por concepto y centro de costo (con su destino 90/94/95), descuentos a sus cuentas por
    pagar (ONP, AFP, quinta) o por cobrar (adelantos), aportes del empleador y neto a 4111."""
    from contabilidad.centralizar import Borrador
    from contabilidad.models import Asiento
    planillas = Planilla.objects.filter(periodo=periodo, estado__in=('CERRADA', 'PAGADA'))
    if not planillas.exists():
        return None
    _, hasta = rango(periodo)
    a = Asiento(fecha=hasta, libro='05', origen='PLANILLA', glosa=f'Planillas {periodo}')
    b = Borrador(a)
    gastos, abonos, neto = defaultdict(lambda: D0), defaultdict(lambda: D0), D0
    sin_cuenta = set()
    for fila in FilaPlanilla.objects.filter(planilla__in=planillas).select_related(
            'trabajador__centro_costo').prefetch_related('lineas__concepto__cuenta', 'lineas__concepto__cuenta_pasivo'):
        centro = fila.trabajador.centro_costo
        for l in fila.lineas.all():
            c = l.concepto
            if c.cuenta is None or (c.tipo == 'APORTE' and c.cuenta_pasivo is None):
                sin_cuenta.add(c.nombre)
                continue
            if c.tipo == 'INGRESO':
                gastos[(c.cuenta, centro)] += l.monto
            elif c.tipo == 'DESCUENTO':
                abonos[c.cuenta] += l.monto
            else:
                gastos[(c.cuenta, centro)] += l.monto
                abonos[c.cuenta_pasivo] += l.monto
        neto += fila.neto
    if sin_cuenta:
        from contabilidad.centralizar import ErrorContable
        raise ErrorContable(f'Planillas {periodo}: configure la cuenta contable de {", ".join(sorted(sin_cuenta))}.')
    for (cuenta, centro), monto in sorted(gastos.items(), key=lambda x: (x[0][0].codigo, x[0][1].codigo if x[0][1]
                                                                         else '')):
        b.add(cuenta, debe=monto, centro_costo=centro, glosa='Planilla del mes')
    for cuenta, monto in sorted(abonos.items(), key=lambda x: x[0].codigo):
        b.add(cuenta, haber=monto, glosa='Planilla del mes')
    b.add(cta['mov_PLANILLA'], haber=neto, glosa='Neto a pagar')
    b.agregar_destinos()
    return b.grabar()


# ---------------------------------------------------------------- PLAME
def archivos_plame(periodo, ruc):
    """(nombre del zip, bytes) con los archivos de importación del PDT PLAME del periodo:
    .rem (conceptos por trabajador), .jor (horas ordinarias y sobretiempo) y .snl (días no laborados)."""
    from .models import Parametro
    planillas = Planilla.objects.filter(periodo=periodo, estado__in=('CALCULADA', 'CERRADA', 'PAGADA'))
    if not planillas.exists():
        raise ErrorPlanilla('No hay planillas calculadas en el periodo.')
    p = Parametro.del_anio(int(periodo[:4]))
    rem, jor, snl = defaultdict(lambda: D0), [], []
    filas = FilaPlanilla.objects.filter(planilla__in=planillas).select_related('trabajador', 'planilla') \
        .prefetch_related('lineas__concepto')
    sin_codigo = set()
    for fila in filas:
        t = fila.trabajador
        doc = (TIPO_DOC_PLAME.get(t.tipo_doc, '01'), t.numero_doc)
        for l in fila.lineas.all():
            if not l.concepto.codigo_plame:
                sin_codigo.add(l.concepto.nombre)
                continue
            rem[(doc, l.concepto.codigo_plame)] += l.monto
        if fila.planilla.tipo == 'MENSUAL':
            dias = max(fila.dias_laborados - fila.dias_falta - fila.dias_subsidio, D0)
            horas = int(dias * p.horas_jornada)
            extra = fila.horas_extra_25 + fila.horas_extra_35
            jor.append(f'{doc[0]}|{doc[1]}|{horas}|0|{int(extra)}|{int((extra % 1) * 60)}|')
            if fila.dias_falta:
                snl.append(f'{doc[0]}|{doc[1]}|07|{int(fila.dias_falta)}|')  # 07: falta no justificada
            if fila.dias_subsidio:
                snl.append(f'{doc[0]}|{doc[1]}|21|{int(fila.dias_subsidio)}|')  # 21: incapacidad temporal
    if sin_codigo:
        raise ErrorPlanilla(f'Indique el código PLAME de: {", ".join(sorted(sin_codigo))} (Planillas › '
                            f'Configuración).')
    lineas_rem = [f'{tipo}|{numero}|{codigo}|{r2(monto):.2f}|{r2(monto):.2f}|'
                  for ((tipo, numero), codigo), monto in sorted(rem.items())]
    base = f'0601{periodo}{ruc}'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(f'{base}.rem', '\r\n'.join(lineas_rem) + '\r\n')
        z.writestr(f'{base}.jor', '\r\n'.join(jor) + ('\r\n' if jor else ''))
        if snl:
            z.writestr(f'{base}.snl', '\r\n'.join(snl) + '\r\n')
    return f'PLAME_{periodo}.zip', buf.getvalue()
