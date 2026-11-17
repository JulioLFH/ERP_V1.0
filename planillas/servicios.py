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
# indemnizaciones (no remunerativas): sin código propio, no se exigen en el PLAME
NO_PLAME = {'INDEMNIZACION', 'INDEMN_VACACIONAL'}
# PLA-01: códigos que el PDT PLAME calcula y que la estructura 18 (.rem) dice no incluir: los encabezados de rubro
# de la tabla 22 (0100, 0200, 0300, 0400, 0500, 0600 y 0800; no son conceptos con monto), 0603, 0604, 0607 (ONP),
# 0610, 0612, 0616, 0802, 0804 (EsSalud), 0806 y 0808
CALCULA_EL_PDT = {'0100', '0200', '0300', '0400', '0500', '0600', '0603', '0604', '0607', '0610', '0612', '0616',
                  '0800', '0802', '0804', '0806', '0808'}


def _marcar(periodo):
    from contabilidad.automatico import marcar_pendiente
    marcar_pendiente(periodo, posteriores=False)


def centros_de_trabajadores(trabajadores, periodo):
    """{trabajador_id: {centro de costo: peso}}: el centro del trabajador; si no tiene, las horas hombre que declaró
    en las órdenes de producción terminadas en el periodo (centro del puesto o de la orden). Sin ninguno: {}."""
    from produccion.models import HoraHombre
    desde, hasta = rango(periodo)
    resultado = {}
    sin_centro = []
    for t in trabajadores:
        if t.centro_costo_id:
            resultado[t.pk] = {t.centro_costo: Decimal('1')}
        else:
            resultado[t.pk] = {}
            sin_centro.append(t.pk)
    if sin_centro:
        from django.db.models import Q
        # órdenes terminadas en un paso (por su término) y declaraciones de producción del mes (MAN-18)
        en_mes = (Q(declaracion__isnull=True, hora__orden__estado='TERMINADA', hora__orden__fecha_fin__range=(desde, hasta))
                  | Q(declaracion__anulada=False, declaracion__fecha__range=(desde, hasta)))
        for h in HoraHombre.objects.filter(en_mes, trabajador_id__in=sin_centro, horas__gt=0).select_related(
                'hora__centro__centro_costo', 'hora__orden__centro_costo'):
            centro = h.hora.centro.centro_costo or h.hora.orden.centro_costo
            if centro is not None:
                pesos = resultado[h.trabajador_id]
                pesos[centro] = pesos.get(centro, D0) + h.horas
    return resultado


def trabajadores_sin_centro(planilla):
    filas = list(planilla.filas.select_related('trabajador__centro_costo'))
    centros = centros_de_trabajadores([f.trabajador for f in filas], planilla.periodo)
    return [f.trabajador for f in filas if not centros[f.trabajador_id]]


@transaction.atomic
def cerrar(planilla):
    if planilla.estado != 'CALCULADA':
        raise ErrorPlanilla('Calcule la planilla antes de cerrarla.')
    if not planilla.filas.exists():
        raise ErrorPlanilla('La planilla no tiene trabajadores.')
    sin_centro = trabajadores_sin_centro(planilla)
    if sin_centro:
        nombres = ', '.join(t.nombre_completo for t in sin_centro[:8]) + (' …' if len(sin_centro) > 8 else '')
        raise ErrorPlanilla(f'{len(sin_centro)} trabajador(es) sin centro de costo: {nombres}. El gasto de personal se '
                            f'contabiliza por centro: asígnelo en Planillas › Trabajadores (o registre sus horas en las '
                            f'órdenes de producción del mes).')
    negativos = [f for f in planilla.filas.select_related('trabajador') if f.neto < 0]
    if negativos:  # PLA-16: los descuentos (adelantos, préstamos) no superan lo que se paga
        nombres = ', '.join(f'{f.trabajador.nombre_completo} (S/ {f.neto:,.2f})' for f in negativos[:5])
        raise ErrorPlanilla(f'{len(negativos)} trabajador(es) con neto a pagar negativo: {nombres}. Los descuentos '
                            f'superan los ingresos: corrija los adelantos u otros descuentos y vuelva a calcular.')
    planilla.estado = 'CERRADA'
    planilla.save(update_fields=['estado'])
    _marcar(planilla.periodo)


@transaction.atomic
def reabrir(planilla):
    if planilla.estado == 'PAGADA':
        raise ErrorPlanilla('La planilla ya se pagó: anule primero el pago en Tesorería › Movimientos.')
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
    """Al anular el movimiento de pago la planilla (o la de construcción civil de la obra) vuelve a 'Cerrada'."""
    Planilla.objects.filter(movimiento_pago=movimiento).update(estado='CERRADA', movimiento_pago=None)
    from obras.models import PlanillaCivil
    PlanillaCivil.objects.filter(movimiento_pago=movimiento).update(estado='CERRADA', movimiento_pago=None)


# ---------------------------------------------------------------- contabilidad
def asiento_planillas(periodo, cta):
    """Gasto de personal por concepto y centro de costo (con su destino 90/94/95), descuentos a sus cuentas por
    pagar (ONP, AFP, quinta) o por cobrar (adelantos), aportes del empleador y neto a 4111."""
    from contabilidad.centralizar import Borrador, _repartir
    from contabilidad.models import Asiento
    planillas = Planilla.objects.filter(periodo=periodo, estado__in=('CERRADA', 'PAGADA'))
    if not planillas.exists():
        return None
    _, hasta = rango(periodo)
    a = Asiento(fecha=hasta, libro='05', origen='PLANILLA', glosa=f'Planillas {periodo}')
    b = Borrador(a)
    gastos, abonos, neto = defaultdict(lambda: D0), defaultdict(lambda: D0), D0
    sin_cuenta = set()
    filas = list(FilaPlanilla.objects.filter(planilla__in=planillas).select_related(
        'trabajador__centro_costo').prefetch_related('lineas__concepto__cuenta', 'lineas__concepto__cuenta_pasivo'))
    centros = centros_de_trabajadores({f.trabajador for f in filas}, periodo)
    for fila in filas:
        # el gasto va al centro del trabajador o, si no tiene, a los centros donde trabajó (horas en las órdenes)
        pesos = centros[fila.trabajador_id] or {None: Decimal('1')}
        for l in fila.lineas.all():
            c = l.concepto
            if c.cuenta is None or (c.tipo == 'APORTE' and c.cuenta_pasivo is None):
                sin_cuenta.add(c.nombre)
                continue
            if c.tipo == 'DESCUENTO':
                abonos[c.cuenta] += l.monto
                continue
            for centro, parte in _repartir(l.monto, pesos):
                gastos[(c.cuenta, centro)] += parte
            if c.tipo != 'INGRESO':
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
    .rem (conceptos por trabajador), .jor (horas ordinarias y sobretiempo), .snl (días no laborados) y, si hay
    recibos por honorarios pagados en el periodo, .ps4 (prestadores de 4ta categoría) y .4ta (sus comprobantes)."""
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
                if l.concepto.clave not in NO_PLAME:
                    sin_codigo.add(l.concepto.nombre)
                continue
            if l.concepto.codigo_plame in CALCULA_EL_PDT:
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
        ps4, cuarta = archivos_cuarta(periodo)
        if cuarta:  # nombres con Ñ y tildes: en ANSI (Windows-1252), como los lee el PDT
            z.writestr(f'{base}.ps4', ('\r\n'.join(ps4) + '\r\n').encode('cp1252', 'replace'))
            z.writestr(f'{base}.4ta', ('\r\n'.join(cuarta) + '\r\n').encode('cp1252', 'replace'))
    return f'PLAME_{periodo}.zip', buf.getvalue()


# ---------------------------------------------------------------- PLAME: rentas de 4ta categoría (PLA-04)
# Estructuras de importación del PDT PLAME (SUNAT), campos separados por «|» (también al final de la línea):
#
# Estructura 7 · .ps4 · «Prestadores de servicios con rentas de 4ta categoría» (una línea por prestador):
#   1 tipo de documento (tabla 3: 01 DNI, 04 carné de extranjería, 06 RUC, 07 pasaporte) | 2 número de documento |
#   3 apellido paterno | 4 apellido materno | 5 nombres | 6 domiciliado (1 sí / 0 no) |
#   7 convenio para evitar la doble tributación (0 ninguno)
#
# Estructura 20 · .4ta · «Detalle de comprobantes - 4ta categoría» (una línea por comprobante):
#   1 tipo de documento del prestador | 2 número de documento | 3 tipo de comprobante (R recibo por honorarios /
#   N nota de crédito) | 4 serie | 5 número | 6 monto total del servicio (S/) | 7 fecha de emisión (dd/mm/aaaa) |
#   8 fecha de pago (dd/mm/aaaa) | 9 indicador de retención de 4ta categoría (1 sí / 0 no) |
#   10 indicador de retención del régimen pensionario | 11 importe del aporte al régimen pensionario
#   (10 y 11: aporte obligatorio de los independientes, Ley 29903, sin aplicación: van vacíos)
#
# El prestador con RUC 10 (persona natural con DNI) se declara con su DNI (dígitos 3 a 10 del RUC); el resto con
# su RUC (06) o el documento que tenga. Entran los recibos (tipo 02) cuyo pago se completó dentro del periodo
# (caja y bancos y lo cancelado sin caja), con las notas de crédito que los modifican.
DOC_PRESTADOR = {'1': '01', '4': '04', '6': '06', '7': '07'}


def _doc_prestador(tercero):
    numero = (tercero.numero_doc or '').strip()
    if tercero.tipo_doc == '6' and len(numero) == 11 and numero.startswith('10'):
        return '01', numero[2:10]
    return DOC_PRESTADOR.get(tercero.tipo_doc, '01'), numero


def _apellidos_nombres(nombre):
    """(paterno, materno, nombres) del nombre del proveedor: «PATERNO MATERNO, NOMBRES» o, como lo da el padrón
    de la SUNAT, «PATERNO MATERNO NOMBRES»."""
    nombre = ' '.join((nombre or '').upper().split())
    if ',' in nombre:
        apellidos, nombres = nombre.split(',', 1)
        partes = apellidos.split()
        return (partes[0] if partes else ''), ' '.join(partes[1:]), nombres.strip()
    partes = nombre.split()
    if len(partes) >= 3:
        return partes[0], partes[1], ' '.join(partes[2:])
    if len(partes) == 2:
        return partes[0], '', partes[1]
    return nombre, '', ''


def _fecha_pago(compra):
    """Fecha en que lo pagado completa el neto del recibo (None si aún tiene saldo)."""
    pagos = sorted([(m.fecha, m.monto_doc) for m in compra.movimientos.all()] +
                   [(a.fecha, a.monto_doc) for a in compra.aplicaciones.all()], key=lambda x: x[0])
    acumulado, tope = compra.pagado_anterior, compra.neto - Decimal('0.01')
    for fecha, monto in pagos:
        acumulado += monto
        if acumulado >= tope:
            return fecha
    return None


def archivos_cuarta(periodo):
    """(líneas .ps4, líneas .4ta) de los recibos por honorarios pagados en el periodo."""
    from django.db.models import Q
    from compras.models import Compra
    desde, hasta = rango(periodo)
    recibos = (Compra.objects.filter(tipo_comprobante='02', estado='REGISTRADO', es_historico=False)
               .filter(Q(movimientos__fecha__range=(desde, hasta), movimientos__estado='VIGENTE') |
                       Q(aplicaciones__fecha__range=(desde, hasta), aplicaciones__estado='VIGENTE'))
               .distinct().select_related('tercero'))
    pagados = []
    for r in recibos:
        fecha = _fecha_pago(r)
        if fecha and desde <= fecha <= hasta:
            pagados.append((r, fecha))
    notas = defaultdict(list)
    for nc in Compra.objects.filter(tipo_comprobante='07', estado='REGISTRADO',
                                    doc_referencia__in=[r for r, _ in pagados]):
        notas[nc.doc_referencia_id].append(nc)
    prestadores, cuarta = {}, []
    for r, fecha in sorted(pagados, key=lambda x: (x[0].tercero.nombre, x[0].fecha_emision, x[0].pk)):
        tipo, numero = _doc_prestador(r.tercero)
        if (tipo, numero) not in prestadores:
            paterno, materno, nombres = _apellidos_nombres(r.tercero.nombre)
            domiciliado = '1' if (r.tercero.pais or '').strip().upper() in ('', 'PERU', 'PERÚ') else '0'
            prestadores[(tipo, numero)] = f'{tipo}|{numero}|{paterno}|{materno}|{nombres}|{domiciliado}|0|'
        for c, clase in [(r, 'R')] + [(nc, 'N') for nc in notas[r.pk]]:
            cuarta.append(f'{tipo}|{numero}|{clase}|{c.serie}|{c.numero}|{r2(c.total_pen):.2f}|'
                          f'{c.fecha_emision:%d/%m/%Y}|{fecha:%d/%m/%Y}|{1 if c.retencion_monto > 0 else 0}|||')
    return [prestadores[k] for k in sorted(prestadores)], cuarta
