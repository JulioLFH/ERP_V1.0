"""Estado de Flujos de Efectivo (NIC 7) por el método directo y el indirecto, con lo percibido
(diseño: docs/EFE_NIC7.md).

Una sola fuente: los asientos que mueven las cuentas de efectivo. En cada uno, el efectivo se reparte entre sus otras
líneas (cada línea aporta −(debe − haber)) y cada línea toma su rubro por prioridad:
  R1 rubro manual del movimiento de Finanzas · R2 documento (pago de compra de activo fijo o intangible) ·
  R3 cuenta contable de contrapartida (prefijo más largo) · R4 concepto del movimiento · R5 sin clasificar.
La diferencia de cambio (676/776) y las cuentas analíticas (9, 79) de un cobro o pago van con su contrapartida
principal; un asiento con solo diferencia de cambio contra el efectivo es el «efecto del tipo de cambio».

El método indirecto se arma con las mismas líneas: utilidad neta + partidas de resultado que no son de operación
(depreciación, intereses según la política, diferencia de cambio del efectivo…) − variación del capital de trabajo
de operación. Un asiento sin efectivo que toca cuentas de inversión o financiamiento (depreciación 681/39, compra de
activo al crédito 33/42, devengo de intereses 673/455) no es de operación. Así el flujo de operación de los dos
métodos coincide (validación V4).
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import F, Q, Sum

from core.models import r2

from .models import Asiento, AsientoLinea, PoliticaFlujo, ReglaFlujoCuenta, RubroFlujo

D0 = Decimal('0')
# apertura del sistema anterior: «saldos iniciales al aperturar el ejercicio», «asiento de apertura»… (no el diario
# «aplicación NC saldos iniciales», que son cobros y aplicaciones normales)
APERTURA_GLOSA = r'saldos? iniciales?.*apertur|apertur(a|ar) (del |el )?ejercicio|asiento de apertura'
DIF_CAMBIO = ('676', '776')
ANALITICAS = ('9', '79')
ACTIVIDADES = [('OPERACION', 'ACTIVIDADES DE OPERACIÓN'), ('INVERSION', 'ACTIVIDADES DE INVERSIÓN'),
               ('FINANCIAMIENTO', 'ACTIVIDADES DE FINANCIAMIENTO')]

# capital de trabajo de operación: (clave, nombre, prefijos, excluidos)
CAPITAL_TRABAJO = [
    ('cxc', '(Aumento) disminución de cuentas por cobrar comerciales', ('12', '13', '19'), ()),
    ('otras_cxc', '(Aumento) disminución de otras cuentas por cobrar y gastos anticipados',
     ('14', '16', '18'), ('1421', '1422', '165', '168')),
    ('inventarios', '(Aumento) disminución de inventarios', ('2',), ()),
    ('diferidos', '(Aumento) disminución de activos y pasivos diferidos', ('37', '38', '49'), ()),
    ('tributos', 'Aumento (disminución) de tributos por pagar', ('40',), ()),
    ('remuneraciones', 'Aumento (disminución) de remuneraciones y participaciones por pagar', ('41',), ()),
    ('cxp', 'Aumento (disminución) de cuentas por pagar comerciales', ('42', '43'), ()),
    ('otras_cxp', 'Aumento (disminución) de otras cuentas por pagar y provisiones', ('46', '48'), ('465',)),
]
# ajustes que se muestran aparte aunque su contrapartida sea capital de trabajo (estimaciones y provisiones)
AJUSTES_PRESENTACION = [
    ('Estimación de cobranza dudosa', ('6841',), 'cxc'),
    ('Desvalorización de existencias', ('6842',), 'inventarios'),
    ('Provisiones del ejercicio', ('686',), 'otras_cxp'),
    ('Recupero de estimaciones', ('755',), 'cxc'),
]
# ajustes por partidas de resultado que no son de operación (asientos sin efectivo o reclasificados)
AJUSTES = [  # en este orden: la diferencia de cambio (676/776) antes que intereses (67/77)
    ('Diferencia de cambio del efectivo y de operaciones de inversión o financiamiento', DIF_CAMBIO),
    ('Depreciación', ('681',)),
    ('Amortización de intangibles', ('682',)),
    ('Deterioro del valor de los activos', ('685', '757')),
    ('Pérdida (ganancia) por medición a valor razonable', ('66', '76')),
    ('Pérdida (ganancia) en venta y baja de activos', ('655', '756')),
    ('Intereses, rendimientos y dividendos (presentados en inversión o financiamiento)', ('67', '77')),
]
# PCGE 2026: depreciación en la 683 y amortización en la 686 (las estimaciones se quedan en su variación)
AJUSTES_2026 = [
    AJUSTES[0], ('Depreciación', ('683',)), ('Amortización de intangibles', ('686',)), AJUSTES[4], AJUSTES[5],
    ('Intereses, rendimientos y dividendos (presentados en inversión o financiamiento)', ('67', '77', '759')),
]


def _es(codigo, prefijos):
    return codigo.startswith(prefijos)


def _anterior(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    return f'{anio - (mes == 1)}{(mes - 2) % 12 + 1:02d}'


class Motor:
    def __init__(self, desde, hasta):
        self.desde, self.hasta = desde, hasta
        self.politica = PoliticaFlujo.actual()
        self.rubros = {r.codigo: r for r in RubroFlujo.objects.all()}
        self.reglas = sorted(((r.prefijo, r.rubro_entrada_id, r.rubro_salida_id) for r in ReglaFlujoCuenta.objects.all()),
                             key=lambda x: -len(x[0]))
        self.por_id = {r.pk: r for r in self.rubros.values()}
        self.efectivo = self._cuentas_efectivo()

    # ------------------------------------------------------------ universo
    def _cuentas_efectivo(self):
        """Códigos contables de efectivo y equivalentes: la clase 10 sin las cajas/bancos excluidos del EFE (y sin
        la cuenta de detracciones si la política la deja fuera)."""
        from finanzas.models import Cuenta
        from .models import CuentaContable
        excluir = set(Cuenta.objects.filter(incluir_en_efe=False).exclude(cuenta_contable=None)
                      .values_list('cuenta_contable__codigo', flat=True))
        if not self.politica.detracciones_en_efectivo:
            excluir |= set(Cuenta.objects.filter(es_detracciones=True).exclude(cuenta_contable=None)
                           .values_list('cuenta_contable__codigo', flat=True))
            excluir |= set(CuentaContable.objects.filter(Q(codigo__startswith='1042') | Q(codigo__startswith='107'))
                           .values_list('codigo', flat=True))
        return {c for c in CuentaContable.objects.filter(codigo__startswith='10').values_list('codigo', flat=True)
                if c not in excluir}

    def es_efectivo(self, codigo):
        return codigo in self.efectivo

    def es_capital_trabajo(self, codigo):
        if self.es_efectivo(codigo):
            return False
        if codigo.startswith('10'):  # cajas y bancos excluidos del efectivo: fondos de operación (detracciones)
            return True
        if codigo.startswith('455') and self.politica.intereses_pagados == 'OPERACION':
            return True
        return any(_es(codigo, p) and not _es(codigo, e) for _, _, p, e in CAPITAL_TRABAJO)

    @staticmethod
    def es_resultado(codigo):
        return (codigo.startswith(('6', '7')) and not codigo.startswith('79')) or codigo.startswith('88')

    # ------------------------------------------------------------ clasificación
    def _politica(self, codigo):
        p = self.politica
        cambios = {'FI-S04': 'OP-S07' if p.intereses_pagados == 'OPERACION' else 'FI-S04',
                   'OP-S07': 'OP-S07' if p.intereses_pagados == 'OPERACION' else 'FI-S04',
                   'IN-E04': 'OP-E03' if p.intereses_cobrados == 'OPERACION' else 'IN-E04',
                   'OP-E03': 'OP-E03' if p.intereses_cobrados == 'OPERACION' else 'IN-E04',
                   'FI-S03': 'OP-S08' if p.dividendos_pagados == 'OPERACION' else 'FI-S03',
                   'OP-S08': 'OP-S08' if p.dividendos_pagados == 'OPERACION' else 'FI-S03'}
        return cambios.get(codigo, codigo)

    def _por_cuenta(self, codigo, entra):
        for prefijo, rid_entrada, rid_salida in self.reglas:
            if codigo.startswith(prefijo):
                rid = rid_entrada if entra else rid_salida
                return self.por_id[rid].codigo if rid in self.por_id else None
        return None

    def clasificar(self, codigo, entra, meta):
        """(rubro, regla) de una línea; entra = la línea aporta efectivo (+)."""
        if meta.get('rubro'):
            return self._politica(meta['rubro']), 'R1'
        if meta.get('activo_fijo') is not None and codigo.startswith(('42', '43', '46')):
            return ('IN-S02' if meta['activo_fijo'] == 'INTANGIBLE' else 'IN-S01') if not entra else 'IN-E01', 'R2'
        rubro = self._por_cuenta(codigo, entra)
        if rubro:
            return self._politica(rubro), 'R3'
        if meta.get('concepto') in _POR_CONCEPTO():
            entrada, salida = _POR_CONCEPTO()[meta['concepto']]
            return self._politica(entrada if entra else salida), 'R4'
        return ('OP-E02' if entra else 'OP-S06'), 'R5'

    # ------------------------------------------------------------ cálculo
    def asientos_apertura(self):
        """Asientos del rango que son saldos iniciales y no flujos: la apertura del ERP y la del sistema anterior
        (glosa «saldos iniciales» / «apertura del ejercicio»)."""
        if not hasattr(self, '_apertura'):
            qs = Asiento.objects.filter(periodo__gte=self.desde, periodo__lte=self.hasta)
            self._apertura = set(qs.filter(Q(origen='APERTURA') | Q(
                origen__in=('ANTERIOR', 'MIGRACION', 'MANUAL'), glosa__iregex=APERTURA_GLOSA)).values_list('pk', flat=True))
        return self._apertura

    def _lineas(self):
        """Líneas del rango (sin las aperturas ni los ajustes de migración) agrupadas por asiento:
        {asiento: [(código, debe − haber)]}."""
        excluir = self.asientos_apertura() | self.asientos_migracion()
        qs = (AsientoLinea.objects.filter(asiento__periodo__gte=self.desde, asiento__periodo__lte=self.hasta)
              .values_list('asiento_id', 'cuenta__codigo', 'debe', 'haber'))
        por_asiento = defaultdict(list)
        for asiento, codigo, debe, haber in qs:
            if asiento not in excluir:
                por_asiento[asiento].append((codigo, (debe or D0) - (haber or D0)))
        return por_asiento

    def asientos_migracion(self):
        """Ajustes de la migración de saldos al ERP (pasan saldos de cuentas del sistema anterior): no son flujos."""
        if not hasattr(self, '_migracion'):
            self._migracion = set(Asiento.objects.filter(periodo__gte=self.desde, periodo__lte=self.hasta,
                                                         origen='MIGRACION').values_list('pk', flat=True)) \
                - self.asientos_apertura()
        return self._migracion

    def ajuste_migracion(self):
        """Efecto de los ajustes de migración sobre el efectivo (línea aparte de la conciliación del efectivo)."""
        if not self.asientos_migracion():
            return D0
        return r2(_neto(AsientoLinea.objects.filter(asiento_id__in=self.asientos_migracion(),
                                                    cuenta__codigo__in=self.efectivo)))

    def _metadatos(self, asientos):
        """{asiento: {'rubro', 'concepto', 'activo_fijo'}} de los asientos de Finanzas con efectivo."""
        from compras.models import Compra
        filas = Asiento.objects.filter(pk__in=asientos, movimiento__isnull=False).values_list(
            'pk', 'movimiento__rubro_flujo__codigo', 'movimiento__concepto', 'movimiento__compra_id',
            'movimiento__compra__clasificacion')
        compras_af = {f[3] for f in filas if f[4] == 'ACTIVO_FIJO'}
        intangibles = set()
        if compras_af:  # compra de activo con más de la mitad en intangibles (34) → compra de intangibles
            peso = defaultdict(lambda: [D0, D0])
            for compra, codigo, debe in AsientoLinea.objects.filter(
                    asiento__compra_id__in=compras_af, asiento__origen='COMPRA', debe__gt=0).filter(
                    Q(cuenta__codigo__startswith='33') | Q(cuenta__codigo__startswith='34')).values_list(
                    'asiento__compra_id', 'cuenta__codigo', 'debe'):
                peso[compra][codigo.startswith('34')] += debe
            intangibles = {c for c, (ppe, inta) in peso.items() if inta > ppe}
        meta = {}
        for pk, rubro, concepto, compra, clasificacion in filas:
            meta[pk] = {'rubro': rubro, 'concepto': concepto,
                        'activo_fijo': ('INTANGIBLE' if compra in intangibles else 'PPE')
                        if clasificacion == 'ACTIVO_FIJO' else None}
        return meta

    def calcular(self):
        lineas = self._lineas()
        con_efectivo = [a for a, ls in lineas.items() if any(self.es_efectivo(c) for c, _ in ls)]
        meta = self._metadatos(con_efectivo)
        rubros = defaultdict(lambda: D0)
        detalle = defaultdict(lambda: defaultdict(lambda: D0))  # rubro → asiento → importe
        efecto_tc, sin_clasificar, transferencias_descuadradas = D0, [], []
        # para el indirecto: líneas de resultado que no son de operación y capital de trabajo de operación
        no_operacion_resultado = defaultdict(lambda: D0)   # código → debe − haber
        operacion_ct = defaultdict(lambda: D0)             # código → debe − haber
        funcion_sin_naturaleza = D0  # clase 9/79 que no cuadra en su asiento (gasto registrado solo por función)
        for asiento, ls in lineas.items():
            efectivo = sum((v for c, v in ls if self.es_efectivo(c)), D0)
            otras = [(c, v) for c, v in ls if not self.es_efectivo(c) and v]
            analitica = sum((v for c, v in otras if _es(c, ANALITICAS)), D0)
            if asiento not in meta and any(self.es_efectivo(c) for c, _ in ls):
                meta[asiento] = {}
            if asiento in meta:  # asiento con efectivo
                principales = [(c, v) for c, v in otras if not _es(c, DIF_CAMBIO + ANALITICAS)]
                if analitica:  # 9/79 sin su par en el asiento: actúa como un gasto o ingreso más
                    principales.append(('9', analitica))
                cambio = [(c, v) for c, v in otras if _es(c, DIF_CAMBIO)]
                if not principales:
                    if cambio and efectivo:  # solo diferencia de cambio contra el efectivo: ajuste de saldos en US$
                        efecto_tc += efectivo
                        for c, v in cambio:
                            no_operacion_resultado[c] += v
                    elif efectivo:
                        transferencias_descuadradas.append(asiento)
                    continue
                clasif = {}
                for c, v in principales:
                    clasif[(c, v)] = self.clasificar(c, -v > 0, meta[asiento])
                dominante = clasif[max(principales, key=lambda x: abs(x[1]))][0]
                for c, v in principales + cambio:  # la diferencia de cambio va con la contrapartida principal
                    rubro, regla = clasif.get((c, v), (dominante, 'R3'))
                    rubros[rubro] += -v
                    detalle[rubro][asiento] += -v
                    if regla == 'R5':
                        sin_clasificar.append(asiento)
                    operacion = self.rubros[rubro].actividad == 'OPERACION' if rubro in self.rubros else True
                    if c == '9':
                        if operacion:
                            funcion_sin_naturaleza += v
                    elif self.es_resultado(c) and not operacion:
                        no_operacion_resultado[c] += v
                    elif self.es_capital_trabajo(c) and operacion:
                        operacion_ct[c] += v
            else:  # asiento sin efectivo
                inversion_o_financiamiento = any(
                    not self.es_resultado(c) and not self.es_capital_trabajo(c) and not _es(c, ANALITICAS)
                    for c, v in otras)
                if analitica and not inversion_o_financiamiento:
                    funcion_sin_naturaleza += analitica
                for c, v in otras:
                    if inversion_o_financiamiento:
                        if self.es_resultado(c):
                            no_operacion_resultado[c] += v
                    elif self.es_capital_trabajo(c):
                        operacion_ct[c] += v
        self.resultado = {
            'rubros': {k: r2(v) for k, v in rubros.items() if v}, 'detalle': detalle, 'efecto_tc': r2(efecto_tc),
            'sin_clasificar': sorted(set(sin_clasificar)), 'transferencias': transferencias_descuadradas,
            'no_operacion_resultado': no_operacion_resultado, 'operacion_ct': operacion_ct,
            'funcion_sin_naturaleza': r2(funcion_sin_naturaleza)}
        return self.resultado

    # ------------------------------------------------------------ saldos de efectivo
    def saldos(self):
        """(saldo inicial, saldo final) de las cuentas de efectivo; la apertura del rango es parte del inicial."""
        base = AsientoLinea.objects.filter(cuenta__codigo__in=self.efectivo)
        inicial = _neto(base.filter(asiento__periodo__lt=self.desde)) + _neto(base.filter(
            asiento_id__in=self.asientos_apertura()))
        final = _neto(base.filter(asiento__periodo__lte=self.hasta))
        return r2(inicial), r2(final)


def _neto(qs, debe='debe', haber='haber'):
    agg = qs.aggregate(d=Sum(debe), h=Sum(haber))
    return (agg['d'] or D0) - (agg['h'] or D0)


def _POR_CONCEPTO():
    from .flujo_semilla import POR_CONCEPTO
    return POR_CONCEPTO


# ---------------------------------------------------------------- estado completo
def estado(desde, hasta):
    """El EFE del rango de periodos (AAAAMM): método directo, conciliación indirecta y validaciones."""
    from . import reportes
    m = Motor(desde, hasta)
    r = m.calcular()
    inicial, final = m.saldos()
    migracion = m.ajuste_migracion()
    aperturas = list(Asiento.objects.filter(pk__in=m.asientos_apertura()).exclude(origen='APERTURA')
                     .values_list('numero', 'glosa')[:5])
    secciones = []
    for actividad, titulo in ACTIVIDADES:
        filas = [{'rubro': rb, 'importe': r['rubros'][rb.codigo]}
                 for rb in sorted(m.rubros.values(), key=lambda x: (x.orden, x.codigo))
                 if rb.actividad == actividad and r['rubros'].get(rb.codigo)]
        secciones.append({'actividad': actividad, 'titulo': titulo, 'filas': filas,
                          'total': sum((f['importe'] for f in filas), D0)})
    total = {s['actividad']: s['total'] for s in secciones}
    neto = sum(total.values(), D0)
    # ---- método indirecto
    from . import pcge
    plan_2026 = pcge.plan() == pcge.PCGE2026
    _, utilidad = reportes.estado_resultados(desde, hasta)
    ajustes = []
    no_op = dict(r['no_operacion_resultado'])
    lista_ajustes = AJUSTES_2026 if plan_2026 else AJUSTES
    for nombre, prefijos in lista_ajustes:
        valor = sum((v for c, v in no_op.items() if _es(c, prefijos)), D0)
        for c in [c for c in no_op if _es(c, prefijos)]:
            no_op.pop(c)
        if valor:
            ajustes.append((nombre, r2(valor)))
    resto = sum(no_op.values(), D0)
    if resto:
        ajustes.append(('Otras partidas de resultado que no son de operación', r2(resto)))
    # resultados registrados en las aperturas y los ajustes de migración: están en la utilidad pero no son flujos
    excluidos = m.asientos_apertura() | m.asientos_migracion()
    if excluidos:
        sin_flujo = r2(sum((d - h for c, d, h in AsientoLinea.objects.filter(asiento_id__in=excluidos)
                            .values_list('cuenta__codigo', 'debe', 'haber') if Motor.es_resultado(c)), D0))
        if sin_flujo:
            ajustes.append(('Resultados de la apertura y de los ajustes de migración (sin efectivo)', sin_flujo))
    if r['funcion_sin_naturaleza']:  # no están en la utilidad: el estado de resultados parte de la naturaleza
        ajustes.append(('Gastos e ingresos registrados solo por función (clase 9/79 sin cuenta de naturaleza)',
                        -r['funcion_sin_naturaleza']))
    ct = defaultdict(lambda: D0)
    for c, v in r['operacion_ct'].items():
        grupo = next((k for k, _, p, e in CAPITAL_TRABAJO if _es(c, p) and not _es(c, e)), 'restringidos')
        if c.startswith('455'):
            grupo = 'otras_cxp'
        ct[grupo] += -v
    # estimaciones y provisiones: se muestran sumadas y se quitan de su variación de capital de trabajo
    presentacion = []
    sumas = reportes.sumas_por_cuenta(desde, hasta)
    for nombre, prefijos, grupo in ([] if plan_2026 else AJUSTES_PRESENTACION):
        valor = r2(sum((d - h for c, (d, h) in sumas.items() if _es(c, prefijos)), D0))
        if valor:
            presentacion.append((nombre, valor))
            ct[grupo] -= valor
    nombres_ct = {k: n for k, n, _, _ in CAPITAL_TRABAJO}
    nombres_ct['restringidos'] = '(Aumento) disminución de fondos sujetos a restricción (detracciones)'
    variaciones = [(nombres_ct[k], r2(ct[k])) for k in list(nombres_ct) if r2(ct.get(k, D0))]
    operacion_indirecto = r2(utilidad + sum((v for _, v in ajustes + presentacion + variaciones), D0))
    # ---- validaciones
    validaciones = _validaciones(m, r, inicial, final, neto + migracion, total['OPERACION'], operacion_indirecto)
    return {
        'desde': desde, 'hasta': hasta, 'politica': m.politica, 'secciones': secciones, 'total': total,
        'neto': neto, 'efecto_tc': r['efecto_tc'], 'inicial': inicial, 'final': final, 'migracion': migracion,
        'aperturas': aperturas,
        'indirecto': {'utilidad': utilidad, 'ajustes': ajustes + presentacion, 'variaciones': variaciones,
                      'operacion': operacion_indirecto},
        'validaciones': validaciones, 'bloqueado': any(v['bloquea'] and not v['ok'] for v in validaciones),
        'restringido': _restringido(m, final),
    }


def _restringido(m, final):
    """Efectivo de las cuentas marcadas como fondos sujetos a restricción (nota del efectivo)."""
    from finanzas.models import Cuenta
    codigos = [c for c in Cuenta.objects.filter(tipo_efectivo='RESTRINGIDO').exclude(cuenta_contable=None)
               .values_list('cuenta_contable__codigo', flat=True) if c in m.efectivo]
    if not codigos:
        return D0
    return r2(_neto(AsientoLinea.objects.filter(cuenta__codigo__in=codigos, asiento__periodo__lte=m.hasta)))


def _validaciones(m, r, inicial, final, neto, operacion_directo, operacion_indirecto):
    from finanzas.models import Cuenta
    from . import automatico
    v = []
    diferencia = r2(inicial + neto + r['efecto_tc'] - final)
    v.append({'codigo': 'V1', 'ok': diferencia == 0, 'bloquea': True,
              'texto': 'Saldo inicial + aumento (disminución) neto + efecto del tipo de cambio = saldo final del '
                       'efectivo (cuenta 10)', 'detalle': f'Diferencia S/ {diferencia:,.2f}' if diferencia else ''})
    # V2: tesorería = contabilidad al cierre del rango, por cuenta
    from calendar import monthrange
    from datetime import date
    anio, mes = int(m.hasta[:4]), int(m.hasta[4:])
    corte = date(anio, mes, monthrange(anio, mes)[1])
    descuadres = []
    for c in Cuenta.objects.exclude(cuenta_contable=None).select_related('cuenta_contable'):
        if c.cuenta_contable.codigo not in m.efectivo:
            continue
        lineas = AsientoLinea.objects.filter(cuenta=c.cuenta_contable, asiento__periodo__lte=m.hasta)
        libro = _neto(lineas, 'debe_me', 'haber_me') if c.moneda == 'USD' else _neto(lineas)
        tesoreria = c.saldo_al(corte)
        if abs(r2(libro) - r2(tesoreria)) >= Decimal('0.01'):
            descuadres.append(f'{c}: tesorería {tesoreria:,.2f} · contabilidad {libro:,.2f}')
    v.append({'codigo': 'V2', 'ok': not descuadres, 'bloquea': False,
              'texto': 'Saldo de cada caja y banco en Finanzas = saldo de su cuenta contable', 'detalle': '; '.join(descuadres[:5])})
    v.append({'codigo': 'V3', 'ok': not r['sin_clasificar'], 'bloquea': False,
              'texto': 'Todo el efectivo está clasificado en un rubro',
              'detalle': f'{len(r["sin_clasificar"])} asiento(s) sin regla: asigne la regla de su cuenta o el rubro '
                         f'del movimiento' if r['sin_clasificar'] else ''})
    diferencia_op = r2(operacion_directo - operacion_indirecto)
    v.append({'codigo': 'V4', 'ok': diferencia_op == 0, 'bloquea': False,
              'texto': 'Flujo de operación: método directo = método indirecto',
              'detalle': f'Diferencia S/ {diferencia_op:,.2f}' if diferencia_op else ''})
    v.append({'codigo': 'V5', 'ok': not r['transferencias'], 'bloquea': False,
              'texto': 'Transferencias entre cuentas de efectivo completas (cada salida con su entrada)',
              'detalle': f'{len(r["transferencias"])} asiento(s) solo con efectivo que no suman cero'
              if r['transferencias'] else ''})
    pendientes = [p for p in automatico.periodos_pendientes() if m.desde <= p <= m.hasta]
    descuadrados = list(AsientoLinea.objects.filter(asiento__periodo__gte=m.desde, asiento__periodo__lte=m.hasta)
                        .values('asiento__numero').annotate(d=Sum('debe'), h=Sum('haber')).exclude(d=F('h'))
                        .values_list('asiento__numero', flat=True)[:5])
    v.append({'codigo': 'V6', 'ok': not pendientes and not descuadrados, 'bloquea': True,
              'texto': 'Periodos centralizados y asientos cuadrados',
              'detalle': '; '.join(filter(None, [f'Pendientes: {", ".join(pendientes)}' if pendientes else '',
                                                f'Descuadrados: {", ".join(descuadrados)}' if descuadrados else '']))})
    return v


def detalle_rubro(desde, hasta, codigo, limite=500):
    """[(asiento, importe)] que forman un rubro (o 'TC' para el efecto del tipo de cambio)."""
    m = Motor(desde, hasta)
    r = m.calcular()
    importes = r['detalle'].get(codigo, {})
    asientos = {a.pk: a for a in Asiento.objects.filter(pk__in=list(importes)[:limite])
                .select_related('movimiento__cuenta')}
    return [(asientos[pk], r2(v)) for pk, v in sorted(importes.items(), key=lambda x: -abs(x[1]))[:limite]
            if pk in asientos and v]
