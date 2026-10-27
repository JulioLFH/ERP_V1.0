"""Gestión del contrato y del proyecto (v1.86, HR-01 a HR-20): tasa de éxito de las propuestas, sensibilidad del
margen, adendas, riesgo contractual por cliente, valor ganado, escenarios del cronograma, control de costo por partida
con su proyección al cierre (EAC) y alertas, productividad de cuadrillas, pronóstico de compras, comprar o alquilar
equipos, evaluación de subcontratistas, rentabilidad por cliente y tipo, valorizaciones y flujo de caja del proyecto,
cartera de proyectos y modelos predictivos simples (promedios de las obras terminadas)."""
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .ejecucion import _meses, _periodo
from .models import (AdendaContrato, AvancePartida, EscenarioCronograma, EvaluacionSubcontrato, EventoContrato, Obra,
                     ProgramacionPartida)
from .valorizaciones import (VIGENTES, avance_acumulado, partidas_de, presupuesto_contractual, presupuesto_ejecucion,
                             presupuesto_meta, r2)

D0 = Decimal('0')
UNO = Decimal('1')
ACTIVAS = ('ADJUDICADA', 'EJECUCION', 'PARALIZADA', 'RECEPCION')
TERMINADAS = ('RECEPCION', 'LIQUIDADA', 'CERRADA')
MIN_HISTORIA = 3  # obras terminadas para que un modelo predictivo diga algo
DIAS_APROBACION = 15  # de fin de mes a la factura de la valorización (aprobación del supervisor)
DIAS_ANTICIPACION = 15  # pedir los materiales antes del mes en que se usan


class ErrorGestion(Exception):
    pass


def _usuario(u):
    return u if u is not None and getattr(u, 'is_authenticated', False) else None


def _pct(a, b, q='0.1'):
    return (Decimal(a) * 100 / b).quantize(Decimal(q)) if b else None


def _div(a, b, q='0.01'):
    return (Decimal(a) / b).quantize(Decimal(q)) if b else None


def _parametro(clave):
    from core import parametros
    return parametros.valor(clave)


def _nombre(u):
    return (u.get_full_name() or u.username) if u is not None else 'Sin responsable'


# ---------------------------------------------------------------- HR-01: tasa de éxito de las propuestas
def _acumular(grupo, clave, ganada, monto, motivo=''):
    g = grupo.setdefault(clave, {'clave': clave, 'presentadas': 0, 'ganadas': 0, 'monto': D0, 'monto_ganado': D0,
                                 'motivos': defaultdict(int)})
    g['presentadas'] += 1
    g['monto'] += monto or D0
    if ganada:
        g['ganadas'] += 1
        g['monto_ganado'] += monto or D0
    elif motivo:
        g['motivos'][motivo] += 1


def _cerrar(grupo, motivos=None):
    filas = []
    for g in grupo.values():
        g['perdidas'] = g['presentadas'] - g['ganadas']
        g['tasa'] = _pct(g['ganadas'], g['presentadas'])
        g['tasa_monto'] = _pct(g['monto_ganado'], g['monto'])
        principal = max(g['motivos'].items(), key=lambda kv: kv[1])[0] if g['motivos'] else ''
        g['motivo'] = (motivos or {}).get(principal, principal)
        filas.append(g)
    return sorted(filas, key=lambda g: (-g['presentadas'], g['clave']))


def tasa_exito(desde=None, hasta=None):
    """Licitaciones y propuestas con resultado (ganadas ÷ presentadas, en número y en monto) por tipo de proceso,
    cliente y responsable; las desiertas no cuentan. También las oportunidades cerradas del CRM."""
    from ventas.models import Oportunidad

    from .models import Licitacion
    qs = (Licitacion.objects.filter(resultado__in=['GANADA', 'PERDIDA'])
          .select_related('obra__cliente', 'obra__residente', 'oportunidad__responsable'))
    if desde:
        qs = qs.filter(fecha_resultado__gte=desde)
    if hasta:
        qs = qs.filter(fecha_resultado__lte=hasta)
    grupos = {'procedimiento': {}, 'cliente': {}, 'responsable': {}}
    total = {}
    for l in qs:
        oferta = l.oferta
        monto = oferta.total if oferta is not None and oferta.total else l.valor_referencial
        resp = (l.oportunidad.responsable if l.oportunidad_id and l.oportunidad.responsable_id
                else l.obra.residente)
        ganada = l.resultado == 'GANADA'
        for g, k in (('procedimiento', l.get_procedimiento_display()),
                     ('cliente', l.obra.cliente.nombre if l.obra.cliente_id else 'Sin cliente'),
                     ('responsable', _nombre(resp))):
            _acumular(grupos[g], k, ganada, monto, l.motivo_perdida)
        _acumular(total, 'Total', ganada, monto, l.motivo_perdida)
    motivos = dict(Licitacion.MOTIVOS)
    crm_qs = Oportunidad.objects.filter(etapa__in=['GANADA', 'PERDIDA']).select_related('tercero', 'responsable')
    if desde:
        crm_qs = crm_qs.filter(cerrada_en__gte=desde)
    if hasta:
        crm_qs = crm_qs.filter(cerrada_en__lte=hasta)
    crm = {'origen': {}, 'cliente': {}, 'responsable': {}}
    crm_total = {}
    origenes = dict(Oportunidad._meta.get_field('origen').choices)
    for o in crm_qs:
        ganada = o.etapa == 'GANADA'
        for g, k in (('origen', origenes.get(o.origen) or 'Sin origen'),
                     ('cliente', o.tercero.nombre if o.tercero_id else (o.prospecto or 'Sin cliente')),
                     ('responsable', _nombre(o.responsable))):
            _acumular(crm[g], k, ganada, o.monto, o.motivo_perdida_tipo)
        _acumular(crm_total, 'Total', ganada, o.monto, o.motivo_perdida_tipo)
    motivos_crm = dict(Oportunidad._meta.get_field('motivo_perdida_tipo').choices or [])
    return {'licitaciones': {g: _cerrar(v, motivos) for g, v in grupos.items()},
            'total': _cerrar(total, motivos), 'crm': {g: _cerrar(v, motivos_crm) for g, v in crm.items()},
            'crm_total': _cerrar(crm_total, motivos_crm)}


# ---------------------------------------------------------------- HR-02: sensibilidad del margen
VARIACIONES = (Decimal('-10'), Decimal('-5'), Decimal('5'), Decimal('10'), Decimal('20'))


def sensibilidad(presupuesto, variaciones=None, n=10):
    """Margen del presupuesto si sube o baja el precio de sus insumos principales (relación de insumos de la meta, o
    del mismo presupuesto si no hay meta): margen = venta sin IGV − costo directo − gastos generales."""
    from .calculos import explosion
    variaciones = list(variaciones or VARIACIONES)
    obra = presupuesto.obra
    costo_de = presupuesto
    if presupuesto.tipo != 'META':
        meta = presupuesto_meta(obra)
        if meta is not None and meta.tipo == 'META' and meta.costo_directo:
            costo_de = meta
    e = explosion(costo_de)
    ingreso = presupuesto.subtotal
    costo = costo_de.costo_directo + presupuesto.gastos_generales
    margen = ingreso - costo

    def escenario(monto):
        salida = []
        for v in variaciones:
            m = margen - monto * v / 100
            salida.append({'variacion': v, 'margen': r2(m), 'pct': _pct(m, ingreso)})
        return salida

    insumos = sorted((t for t in e['filas'] if t['monto'] > 0), key=lambda t: -t['monto'])[:n]
    filas = [{'recurso': t['recurso'], 'monto': t['monto'], 'peso': _pct(t['monto'], costo_de.costo_directo),
              'escenarios': escenario(t['monto']),
              'quiebre': _pct(margen, t['monto']) if margen > 0 else None} for t in insumos]
    materiales = sum((t['monto'] for t in e['filas'] if t['recurso'].tipo == 'MAT'), D0)
    principales = sum((t['monto'] for t in insumos), D0)
    return {'presupuesto': presupuesto, 'costo_de': costo_de, 'ingreso': ingreso, 'costo': costo, 'margen': margen,
            'margen_pct': _pct(margen, ingreso), 'variaciones': variaciones, 'filas': filas,
            'conjunto': [{'nombre': f'Los {len(insumos)} insumos principales', 'monto': principales,
                          'escenarios': escenario(principales),
                          'quiebre': _pct(margen, principales) if margen > 0 else None},
                         {'nombre': 'Todos los materiales', 'monto': materiales, 'escenarios': escenario(materiales),
                          'quiebre': _pct(margen, materiales) if margen > 0 else None}]}


# ---------------------------------------------------------------- HR-03: adendas del contrato
CON_CONTRATO = ('ADJUDICADA', 'EJECUCION', 'PARALIZADA', 'RECEPCION', 'LIQUIDADA')


def _fecha_razonable(obra, fecha, que):
    """OBR-39: ni futura ni anterior al contrato de la obra (ni de antes del 2000)."""
    if fecha > timezone.localdate():
        raise ErrorGestion(f'{que} no puede ser futura ({fecha:%d/%m/%Y}).')
    inicio = obra.fecha_contrato or obra.fecha_inicio
    if inicio and fecha < inicio:
        raise ErrorGestion(f'{que} ({fecha:%d/%m/%Y}) es anterior al contrato ({inicio:%d/%m/%Y}).')
    if fecha.year < 2000:
        raise ErrorGestion(f'{que}: revise el año ({fecha:%d/%m/%Y}).')


def registrar_adenda(obra, tipo, monto, plazo_dias, motivo, usuario, fecha=None, resolucion='', fecha_resolucion=None,
                     presupuesto=None):
    if not (motivo or '').strip():
        raise ErrorGestion('Indique el motivo de la adenda.')
    if tipo not in dict(AdendaContrato.TIPOS):
        raise ErrorGestion('Elija el tipo de adenda.')
    monto, plazo_dias = Decimal(monto or 0), int(plazo_dias or 0)
    if tipo == 'MONTO' and not monto or tipo == 'PLAZO' and not plazo_dias or \
            tipo == 'MONTO_PLAZO' and not (monto and plazo_dias):
        raise ErrorGestion('La adenda debe indicar la variación del monto y/o del plazo según su tipo.')
    if presupuesto is not None and presupuesto.obra_id != obra.pk:
        raise ErrorGestion('El presupuesto es de otra obra.')
    # OBR-39: solo sobre un contrato adjudicado, plazo que no se reduce, monto final no negativo y fechas razonables
    if obra.estado not in CON_CONTRATO:
        raise ErrorGestion(f'La obra está «{obra.get_estado_display()}»: las adendas son de obras adjudicadas.')
    if plazo_dias < 0:
        raise ErrorGestion('La ampliación de plazo no puede ser negativa (una reducción se registra como «Otra '
                           'modificación» con su resolución).')
    pendientes = sum((a.monto for a in obra.adendas.filter(estado='BORRADOR')), D0)
    if obra.monto_contratado + pendientes + (monto if tipo in ('MONTO', 'MONTO_PLAZO') else D0) < 0:
        raise ErrorGestion(f'La reducción ({monto:,.2f}) deja el contrato en negativo (monto vigente '
                           f'{obra.monto_contratado:,.2f}).')
    _fecha_razonable(obra, fecha or timezone.localdate(), 'La fecha de la adenda')
    if fecha_resolucion:
        _fecha_razonable(obra, fecha_resolucion, 'La fecha de la resolución')
    return AdendaContrato.objects.create(
        obra=obra, tipo=tipo, monto=r2(monto) if tipo in ('MONTO', 'MONTO_PLAZO') else D0,
        plazo_dias=plazo_dias if tipo in ('PLAZO', 'MONTO_PLAZO') else 0, motivo=motivo.strip()[:300],
        fecha=fecha or timezone.localdate(), resolucion=(resolucion or '')[:120], fecha_resolucion=fecha_resolucion,
        presupuesto=presupuesto, creado_por=_usuario(usuario))


@transaction.atomic
def aprobar_adenda(adenda, usuario):
    """Cambia el monto contratado y el plazo de la obra (guarda los anteriores para el historial)."""
    from core.segregacion import propio
    adenda = AdendaContrato.objects.select_for_update().get(pk=adenda.pk)
    if adenda.estado != 'BORRADOR':
        raise ErrorGestion('La adenda ya fue aprobada o anulada.')
    aviso = propio(usuario, adenda)
    if aviso:
        raise ErrorGestion(aviso)
    obra = Obra.objects.select_for_update().get(pk=adenda.obra_id)
    adenda.monto_anterior, adenda.plazo_anterior = obra.monto_contratado, obra.plazo_dias
    obra.monto_contratado = r2(obra.monto_contratado + adenda.monto)
    obra.plazo_dias = max(obra.plazo_dias + adenda.plazo_dias, 0)
    if obra.monto_contratado < 0:
        raise ErrorGestion('La reducción deja el contrato en negativo.')
    obra.save(update_fields=['monto_contratado', 'plazo_dias'])
    adenda.estado, adenda.aprobado_por, adenda.aprobado_en = 'APROBADA', _usuario(usuario), timezone.now()
    adenda.save(update_fields=['estado', 'monto_anterior', 'plazo_anterior', 'aprobado_por', 'aprobado_en'])
    return adenda


@transaction.atomic
def anular_adenda(adenda, usuario, motivo):
    """Una adenda por aprobar se anula sin efecto; una aprobada solo si es la última (devuelve el monto y el plazo)."""
    if not (motivo or '').strip():
        raise ErrorGestion('Indique el motivo de la anulación.')
    adenda = AdendaContrato.objects.select_for_update().get(pk=adenda.pk)
    if adenda.estado == 'ANULADA':
        raise ErrorGestion('La adenda ya está anulada.')
    if adenda.estado == 'APROBADA':
        posterior = AdendaContrato.objects.filter(obra=adenda.obra, estado='APROBADA', numero__gt=adenda.numero)
        if posterior.exists():
            raise ErrorGestion('Solo se anula la última adenda aprobada: anule antes las posteriores.')
        obra = Obra.objects.select_for_update().get(pk=adenda.obra_id)
        obra.monto_contratado = r2(obra.monto_contratado - adenda.monto)
        obra.plazo_dias = max(obra.plazo_dias - adenda.plazo_dias, 0)
        obra.save(update_fields=['monto_contratado', 'plazo_dias'])
    adenda.estado, adenda.motivo_anulacion = 'ANULADA', motivo.strip()[:250]
    adenda.save(update_fields=['estado', 'motivo_anulacion'])
    return adenda


def contrato_vigente(obra):
    """Contrato original, adendas aprobadas, monto y plazo vigentes, término y lo que falta valorizar."""
    aprobadas = list(obra.adendas.filter(estado='APROBADA').order_by('numero'))
    variacion_monto = sum((a.monto for a in aprobadas), D0)
    variacion_plazo = sum((a.plazo_dias for a in aprobadas), 0)
    vigentes = list(obra.valorizaciones.filter(estado__in=VIGENTES))
    valorizado = sum((v.subtotal for v in vigentes), D0)
    contractual = presupuesto_contractual(obra)
    termino = obra.fecha_inicio + timedelta(days=obra.plazo_dias) if obra.fecha_inicio and obra.plazo_dias else None
    original_termino = (obra.fecha_inicio + timedelta(days=obra.plazo_dias - variacion_plazo)
                        if obra.fecha_inicio and obra.plazo_dias - variacion_plazo > 0 else None)
    igv = (UNO + (contractual.igv_pct if contractual else Decimal('18')) / 100)
    sin_igv = r2(obra.monto_contratado / igv)
    return {'original': obra.monto_contratado - variacion_monto, 'plazo_original': obra.plazo_dias - variacion_plazo,
            'variacion_monto': variacion_monto, 'variacion_plazo': variacion_plazo, 'monto': obra.monto_contratado,
            'monto_sin_igv': sin_igv, 'plazo': obra.plazo_dias, 'termino': termino,
            'termino_original': original_termino, 'adendas': aprobadas, 'valorizado': valorizado,
            'por_valorizar': sin_igv - valorizado,
            'presupuesto': contractual,
            # el monto vigente y el presupuesto que se valoriza deberían coincidir (adicionales/deductivos cargados)
            'diferencia_presupuesto': (sin_igv - contractual.subtotal) if contractual and contractual.subtotal
            and aprobadas else D0}


# ---------------------------------------------------------------- HR-04: riesgo contractual por cliente
def riesgo_clientes():
    """Puntaje 0-100 por cliente con: penalidades sobre su contrato, observaciones y controversias (abiertas y
    graves), atraso promedio de pago y saldo vencido. Bajo < 25, medio < 50, alto desde 50."""
    from finanzas.cobranza import historial_pagos, pendientes
    por = {}
    for o in Obra.objects.filter(cliente__isnull=False).exclude(estado__in=('LICITACION', 'NO_ADJUDICADA')) \
            .select_related('cliente'):
        c = por.setdefault(o.cliente_id, {'tercero': o.cliente, 'obras': 0, 'contrato': D0, 'penalidades': D0,
                                          'eventos': 0, 'abiertos': 0, 'graves': 0, 'vencido': D0, 'mora': 0})
        c['obras'] += 1
        c['contrato'] += o.monto_contratado
    for e in EventoContrato.objects.filter(obra__cliente_id__in=list(por)).select_related('obra'):
        c = por[e.obra.cliente_id]
        if e.tipo == 'PENALIDAD':
            c['penalidades'] += e.monto
        else:
            c['eventos'] += 1
        c['abiertos'] += 0 if e.resuelto else 1
        c['graves'] += 1 if e.gravedad == 'ALTA' else 0
    for v in pendientes():
        if v.tercero_id in por and v.dias_mora > 0:
            por[v.tercero_id]['vencido'] += v.saldo_pen
            por[v.tercero_id]['mora'] = max(por[v.tercero_id]['mora'], v.dias_mora)
    hist = historial_pagos(set(por))
    filas = []
    for pk, c in por.items():
        h = hist.get(pk) or {}
        c['atraso'] = h.get('atraso')
        c['tarde_pct'] = h.get('tarde_pct')
        c['docs_cobrados'] = h.get('docs', 0)
        base = c['contrato'] or UNO
        puntos = (min(c['penalidades'] * 100 / base * 4, Decimal('30'))
                  + min(Decimal(max(c['atraso'] or 0, 0)) / 2, Decimal('30'))
                  + min(Decimal(c['abiertos'] * 4 + c['graves'] * 4 + c['eventos']), Decimal('20'))
                  + min(c['vencido'] * 100 / base * 2, Decimal('20')))
        c['puntaje'] = puntos.quantize(Decimal('1'))
        c['nivel'] = 'Alto' if c['puntaje'] >= 50 else 'Medio' if c['puntaje'] >= 25 else 'Bajo'
        filas.append(c)
    return sorted(filas, key=lambda c: -c['puntaje'])


# ---------------------------------------------------------------- costos reales
def es_gg(partida):
    """Partida del subpresupuesto de gastos generales detallados (no es costo directo)."""
    return bool(partida.subpresupuesto_id and partida.subpresupuesto.es_gastos_generales)


def directas(presupuesto):
    """Partidas de costo directo del presupuesto (sin las del subpresupuesto de gastos generales, OBR-25)."""
    return [x for x in partidas_de(presupuesto) if not es_gg(x)]


def registros_costo(obra):
    """Fuente única del costo real (OBR-27): [(periodo, clave de partida o None, tipo, monto)] de las planillas de
    construcción civil cerradas (por las horas del tareo), los partes de equipo, las valorizaciones aprobadas de los
    subcontratos y los materiales consumidos con el centro de la obra (repartidos entre las partidas que los usan
    según su análisis). Sin ninguno de ellos, los gastos contables (clase 6) con el centro de costo, sin partida.
    Devuelve (registros, fuente)."""
    from core.models import Kardex

    from .models import LineaPlanillaCivil, LineaValSubcontrato, ParteEquipo, Partida, RecursoAPU
    registros, ids = [], set()
    for hasta, costo, partidas in LineaPlanillaCivil.objects.filter(
            planilla__obra=obra, planilla__estado__in=('CERRADA', 'PAGADA')).values_list(
            'planilla__hasta', 'costo', 'partidas'):
        horas = sum((Decimal(str(h)) for h in (partidas or {}).values()), D0)
        if not horas:
            registros.append((_periodo(hasta), None, 'mo', costo))
            continue
        for pid, h in partidas.items():
            pid = int(pid) if pid not in ('', 'None') else None
            ids.add(pid)
            registros.append((_periodo(hasta), pid, 'mo', costo * Decimal(str(h)) / horas))
    for fecha, pid, costo in ParteEquipo.objects.filter(obra=obra).values_list('fecha', 'partida_id', 'costo'):
        ids.add(pid)
        registros.append((_periodo(fecha), pid, 'eq', costo))
    for per, pid, monto in LineaValSubcontrato.objects.filter(
            valorizacion__subcontrato__obra=obra, valorizacion__estado='APROBADA').values_list(
            'valorizacion__periodo', 'linea__partida_id', 'monto'):
        ids.add(pid)
        registros.append((per, pid, 'sub', monto))
    claves = {x.pk: x.clave for x in Partida.objects.filter(pk__in=[i for i in ids if i]).select_related(
        'subpresupuesto')}
    registros = [(per, claves.get(pid) if pid else None, tipo, m) for per, pid, tipo, m in registros]
    if obra.centro_costo_id:
        meta = presupuesto_meta(obra)
        avance = avance_acumulado(obra)
        pesos = defaultdict(dict)  # producto -> {clave: peso}
        if meta is not None:
            for l in RecursoAPU.objects.filter(partida__presupuesto=meta, recurso__producto__isnull=False) \
                    .select_related('recurso', 'partida__subpresupuesto'):
                x = l.partida
                base = avance.get(x.clave) or x.metrado
                p = pesos[l.recurso.producto_id]
                p[x.clave] = p.get(x.clave, D0) + l.cantidad * base
        for fecha, producto, cantidad, costo in Kardex.objects.filter(
                centro_costo_id=obra.centro_costo_id, tipo='SALIDA').values_list(
                'fecha', 'producto_id', 'cantidad', 'costo_unitario'):
            valor = cantidad * (costo or D0)
            w = {k: v for k, v in pesos.get(producto, {}).items() if v > 0}
            suma = sum(w.values(), D0)
            if not suma:
                registros.append((_periodo(fecha), None, 'mat', valor))
                continue
            for k, v in w.items():
                registros.append((_periodo(fecha), k, 'mat', valor * v / suma))
    if any(m for *_, m in registros) or not obra.centro_costo_id:
        return registros, 'operativo'
    from contabilidad.models import AsientoLinea
    return [(per, None, 'mat', debe - haber) for per, debe, haber in AsientoLinea.objects.filter(
        centro_costo_id=obra.centro_costo_id, es_destino=False, cuenta__codigo__startswith='6').values_list(
        'asiento__periodo', 'debe', 'haber')], 'contable'


def _costos_por_clave(obra, meta=None, avance=None):
    """{clave de partida: costo real} (None = sin partida), de registros_costo."""
    salida = defaultdict(lambda: D0)
    for _, clave, _, monto in registros_costo(obra)[0]:
        salida[clave] += monto
    return salida


def costos_por_mes(obra, excluir=()):
    """({periodo: {'mo', 'eq', 'sub', 'mat', 'total'}}, fuente) de registros_costo; «excluir»: claves que no cuentan
    (las partidas de gastos generales en el valor ganado)."""
    registros, fuente = registros_costo(obra)
    meses = defaultdict(lambda: {'mo': D0, 'eq': D0, 'sub': D0, 'mat': D0})
    for per, clave, tipo, monto in registros:
        if clave is not None and clave in excluir:
            continue
        meses[per][tipo] += monto
    salida = {}
    for per, m in meses.items():
        m = {k: r2(v) for k, v in m.items()}
        m['total'] = sum(m.values(), D0)
        salida[per] = m
    return dict(sorted(salida.items())), fuente


def costo_real(obra):
    """Costo real total de la obra (el mismo en la cartera, la rentabilidad, el valor ganado y la proyección)."""
    return r2(sum((m for *_, m in registros_costo(obra)[0]), D0))


# ---------------------------------------------------------------- HR-07, HR-12, HR-14: control por partida y proyección
def margen_meta(obra):
    return obra.margen_meta_pct if obra.margen_meta_pct is not None else _parametro('OBR_MARGEN_META_PCT')


def control_partidas(obra, tolerancia=None):
    """Por partida del contractual: presupuesto del costo (BAC: la meta, o el contractual), avance, valor ganado (EV),
    costo real directo (AC), alerta si AC > EV × (1 + tolerancia), proyección al cierre EAC = AC × BAC / EV (o
    AC + BAC sin avance; AC si terminó) y margen proyectado. Totales del proyecto con el margen frente a la meta."""
    p = presupuesto_ejecucion(obra)
    if p is None:
        return None
    meta = presupuesto_meta(obra)
    meta_por_clave = {x.clave: x for x in partidas_de(meta)} if meta is not None and meta.pk != p.pk else {}
    avance = avance_acumulado(obra)
    costos = _costos_por_clave(obra)
    tol = _parametro('OBR_TOLERANCIA_COSTO_PCT') if tolerancia is None else Decimal(tolerancia)
    filas, t = [], defaultdict(lambda: D0)
    claves_gg = {x.clave for x in partidas_de(p) if es_gg(x)} | {k for k, m in meta_por_clave.items() if es_gg(m)}
    for x in directas(p):
        m = meta_por_clave.get(x.clave)
        bac = m.parcial if m is not None else x.parcial
        av = avance.get(x.clave, D0)
        pct = (av / x.metrado) if x.metrado else (UNO if av else D0)
        ev = r2(bac * min(pct, UNO))
        ac = r2(costos.get(x.clave, D0))
        if pct >= 1:
            eac = ac
        elif ev > 0:
            eac = r2(ac * bac / ev)
        else:
            eac = ac + bac
        alerta = ac > 0 and ac > ev * (1 + tol / 100)
        f = {'partida': x, 'contractual': x.parcial, 'bac': bac, 'avance': av, 'avance_pct': _pct(pct, 1),
             'ev': ev, 'ac': ac, 'cpi': _div(ev, ac), 'eac': eac, 'margen': x.parcial - eac,
             'margen_pct': _pct(x.parcial - eac, x.parcial), 'alerta': alerta,
             'exceso': ac - ev if alerta else D0, 'exceso_pct': _pct(ac - ev, ev) if alerta else None}
        filas.append(f)
        for k in ('contractual', 'bac', 'ev', 'ac', 'eac', 'exceso'):
            t[k] += f[k]
    sin_partida = r2(costos.get(None, D0) + sum((v for k, v in costos.items() if k is not None and k not in claves_gg
                                                 and k not in {f['partida'].clave for f in filas}), D0))
    t['ac'] += sin_partida
    t['eac'] += sin_partida
    if p.subtotal:
        ingreso = p.subtotal
    else:
        from .servicios import contrato_sin_igv
        ingreso = contrato_sin_igv(obra)
    # gastos generales: los del presupuesto (en % o su subpresupuesto detallado) y los reales cargados a sus partidas;
    # no están en el costo directo (OBR-25), se proyectan por el mayor de los dos
    gg = p.gastos_generales
    gg_real = r2(sum((v for k, v in costos.items() if k in claves_gg), D0))
    gg_cierre = max(gg, gg_real)
    margen = ingreso - t['eac'] - gg_cierre
    margen_pct = _pct(margen, ingreso)
    objetivo = margen_meta(obra)
    return {'presupuesto': p, 'meta': meta, 'filas': filas, 'totales': dict(t), 'sin_partida': sin_partida,
            'tolerancia': tol, 'ingreso': ingreso, 'gg': gg, 'gg_real': gg_real, 'gg_cierre': gg_cierre,
            'costo_real': t['ac'] + gg_real, 'costo_cierre': t['eac'] + gg_cierre,
            'margen_presupuestado': ingreso - t['bac'] - gg,
            'margen_presupuestado_pct': _pct(ingreso - t['bac'] - gg, ingreso), 'margen': margen,
            'margen_pct': margen_pct, 'margen_meta': objetivo, 'cpi': _div(t['ev'], t['ac']),
            'avance_pct': _pct(t['ev'], t['bac']), 'alertas': [f for f in filas if f['alerta']],
            'claves_gg': claves_gg,
            'bajo_meta': margen_pct is not None and t['ac'] > 0 and margen_pct < objetivo}


def alertas_proyectos():
    """Obras en ejecución con partidas sobre su costo esperado (HR-07) y con margen proyectado bajo la meta (HR-14)."""
    costo, margen = [], []
    for o in Obra.objects.filter(estado__in=('EJECUCION', 'PARALIZADA')).exclude(tipo='SERVICIO'):
        c = control_partidas(o)
        if c is None:
            continue
        if c['alertas']:
            costo.append((o, c['alertas']))
        if c['bajo_meta']:
            margen.append((o, c['margen_pct'], c['margen_meta']))
    return costo, margen


# ---------------------------------------------------------------- HR-05: valor ganado
def _precios_costo(obra):
    """(presupuesto del cronograma, {clave: precio unitario del costo}): el de la meta si existe."""
    p = presupuesto_ejecucion(obra)
    if p is None:
        return None, {}
    precios = {x.clave: x.precio for x in partidas_de(p)}
    meta = presupuesto_meta(obra)
    if meta is not None and meta.pk != p.pk:
        precios.update({x.clave: x.precio for x in partidas_de(meta)})
    return p, precios


def valor_ganado(obra, hoy=None):
    """Por mes: valor planificado (PV, cronograma), valor ganado (EV, avance físico) a precios del costo (meta) y costo
    real (AC), acumulados, con SPI = EV/PV y CPI = EV/AC; al cierre: EAC = BAC/CPI, VAC y el término estimado."""
    hoy = hoy or timezone.localdate()
    p, precios = _precios_costo(obra)
    if p is None:
        return None
    pv, ev = defaultdict(lambda: D0), defaultdict(lambda: D0)
    bac = D0
    for x in directas(p):  # el valor ganado es del costo directo: sin el subpresupuesto de gastos generales
        bac += x.metrado * precios.get(x.clave, x.precio)
    for prog in ProgramacionPartida.objects.filter(partida__presupuesto=p).select_related('partida__subpresupuesto'):
        if not es_gg(prog.partida):
            pv[prog.periodo] += prog.metrado * precios.get(prog.partida.clave, prog.partida.precio)
    for a in AvancePartida.objects.filter(obra=obra).select_related('partida__subpresupuesto'):
        if not es_gg(a.partida):
            ev[_periodo(a.fecha)] += a.metrado * precios.get(a.partida.clave, a.partida.precio)
    control = control_partidas(obra)
    ac, fuente = costos_por_mes(obra, excluir=control['claves_gg'] if control else ())
    actual = _periodo(hoy)
    periodos = sorted(set(pv) | set(ev) | set(ac))
    filas, acum = [], {'pv': D0, 'ev': D0, 'ac': D0}
    if periodos:
        ultimo = max(periodos[-1], actual) if periodos[0] <= actual else periodos[-1]
        for per in _meses(date(int(periodos[0][:4]), int(periodos[0][4:]), 1), date(int(ultimo[:4]), int(ultimo[4:]), 1)):
            pasado = per <= actual
            acum['pv'] += pv.get(per, D0)
            if pasado:
                acum['ev'] += ev.get(per, D0)
                acum['ac'] += ac.get(per, {}).get('total', D0)
            filas.append({'periodo': per, 'etiqueta': f'{per[4:]}/{per[2:4]}', 'pv': r2(pv.get(per, D0)),
                          'ev': r2(ev.get(per, D0)) if pasado else None,
                          'ac': ac.get(per, {}).get('total', D0) if pasado else None,
                          'pv_acum': r2(acum['pv']), 'ev_acum': r2(acum['ev']) if pasado else None,
                          'ac_acum': r2(acum['ac']) if pasado else None,
                          'spi': _div(acum['ev'], acum['pv']) if pasado else None,
                          'cpi': _div(acum['ev'], acum['ac']) if pasado else None})
    pv_hoy = sum((v for k, v in pv.items() if k <= actual), D0)
    ev_t, ac_t = sum(ev.values(), D0), sum((m['total'] for k, m in ac.items() if k <= actual), D0)
    spi, cpi = _div(ev_t, pv_hoy), _div(ev_t, ac_t)
    # OBR-26: un solo EAC en todo el sistema, el de la proyección por partida; el del índice (BAC / CPI, con el CPI
    # sin redondear) se muestra aparte como referencia
    eac_indice = r2(bac * ac_t / ev_t) if ev_t else None
    eac = control['totales'].get('eac') if control else eac_indice
    termino = None
    if ev_t and pv_hoy and obra.fecha_inicio and obra.plazo_dias:
        termino = obra.fecha_inicio + timedelta(days=int(obra.plazo_dias * float(pv_hoy / ev_t)))
    return {'presupuesto': p, 'filas': filas, 'bac': r2(bac), 'pv': r2(pv_hoy), 'ev': r2(ev_t), 'ac': r2(ac_t),
            'gg_real': control['gg_real'] if control else D0, 'eac_indice': eac_indice,
            'spi': spi, 'cpi': cpi, 'sv': r2(ev_t - pv_hoy), 'cv': r2(ev_t - ac_t), 'eac': eac,
            'etc': r2(eac - ac_t) if eac is not None else None, 'vac': r2(bac - eac) if eac is not None else None,
            'tcpi': _div(bac - ev_t, bac - ac_t) if bac != ac_t else None, 'avance_pct': _pct(ev_t, bac),
            'termino_estimado': termino, 'termino': obra.fecha_inicio + timedelta(days=obra.plazo_dias)
            if obra.fecha_inicio and obra.plazo_dias else None, 'fuente_costo': fuente}


# ---------------------------------------------------------------- HR-06: escenarios del cronograma
def _programa_actual(presupuesto):
    datos = defaultdict(dict)
    for prog in ProgramacionPartida.objects.filter(partida__presupuesto=presupuesto).select_related(
            'partida__subpresupuesto'):
        datos[prog.partida.clave][prog.periodo] = prog.metrado
    return datos


def guardar_escenario(obra, nombre, usuario, descripcion=''):
    p = presupuesto_ejecucion(obra)
    if p is None:
        raise ErrorGestion('La obra no tiene presupuesto.')
    nombre = (nombre or '').strip()
    if not nombre:
        raise ErrorGestion('Indique el nombre del escenario.')
    if len(nombre) > 120:  # OBR-41
        raise ErrorGestion(f'El nombre tiene {len(nombre)} caracteres: el máximo es 120.')
    if EscenarioCronograma.objects.filter(obra=obra, nombre__iexact=nombre).exists():
        raise ErrorGestion(f'Ya hay un escenario «{nombre}» en la obra: use otro nombre.')
    datos = _programa_actual(p)
    if not datos:
        raise ErrorGestion('El presupuesto no tiene cronograma: impórtelo o escríbalo en «Cronograma y curva S».')
    return EscenarioCronograma.objects.create(
        obra=obra, presupuesto=p, nombre=nombre.strip()[:120], descripcion=(descripcion or '')[:250],
        creado_por=_usuario(usuario),
        datos={k: {per: str(m) for per, m in v.items()} for k, v in datos.items()})


@transaction.atomic
def restaurar_escenario(escenario, usuario):
    """El cronograma vuelve al del escenario (el actual se guarda antes como otro escenario)."""
    p = escenario.presupuesto
    actual = _programa_actual(p)
    if actual:
        guardar_escenario(escenario.obra, f'Antes de volver a «{escenario.nombre}» '
                                          f'({timezone.localtime():%d/%m/%Y %H:%M})', usuario)
    partidas = {x.clave: x for x in partidas_de(p)}
    ProgramacionPartida.objects.filter(partida__presupuesto=p).delete()
    nuevas = [ProgramacionPartida(partida=partidas[k], periodo=per, metrado=Decimal(m))
              for k, meses in escenario.datos.items() if k in partidas for per, m in meses.items()]
    ProgramacionPartida.objects.bulk_create(nuevas)
    return len(nuevas)


def _resumen_programa(presupuesto, datos):
    """{periodo: costo directo programado}, inicio, término y total del programa."""
    precios = {x.clave: x.precio for x in partidas_de(presupuesto)}
    meses = defaultdict(lambda: D0)
    for k, v in datos.items():
        for per, m in v.items():
            meses[per] += Decimal(m) * precios.get(k, D0)
    periodos = sorted(p for p, v in meses.items() if v)
    return {'meses': meses, 'inicio': periodos[0] if periodos else None, 'fin': periodos[-1] if periodos else None,
            'total': r2(sum(meses.values(), D0)), 'partidas': len(datos)}


def comparar_escenarios(obra, a=None, b=None):
    """Mes a mes (costo directo programado y acumulado) de dos escenarios; None = el cronograma vigente. Además las
    partidas que cambian de mes de término."""
    p = presupuesto_ejecucion(obra)
    if p is None:
        return None
    datos_a = a.datos if a is not None else _programa_actual(p)
    datos_b = b.datos if b is not None else _programa_actual(p)
    ra, rb = _resumen_programa(p, datos_a), _resumen_programa(p, datos_b)
    periodos = sorted(set(ra['meses']) | set(rb['meses']))
    filas, aa, ab = [], D0, D0
    for per in periodos:
        va, vb = ra['meses'].get(per, D0), rb['meses'].get(per, D0)
        aa, ab = aa + va, ab + vb
        filas.append({'periodo': per, 'a': r2(va), 'b': r2(vb), 'acum_a': r2(aa), 'acum_b': r2(ab),
                      'pct_a': _pct(aa, ra['total']), 'pct_b': _pct(ab, rb['total'])})
    partidas = {x.clave: x for x in partidas_de(p)}
    cambios = []
    for k in sorted(set(datos_a) | set(datos_b)):
        fa = max((per for per, m in datos_a.get(k, {}).items() if Decimal(m)), default=None)
        fb = max((per for per, m in datos_b.get(k, {}).items() if Decimal(m)), default=None)
        if fa != fb and k in partidas:
            cambios.append({'partida': partidas[k], 'fin_a': fa, 'fin_b': fb})
    meses_diferencia = None
    if ra['fin'] and rb['fin']:
        meses_diferencia = (int(rb['fin'][:4]) - int(ra['fin'][:4])) * 12 + int(rb['fin'][4:]) - int(ra['fin'][4:])
    return {'a': ra, 'b': rb, 'filas': filas, 'cambios': cambios, 'meses_diferencia': meses_diferencia}


# ---------------------------------------------------------------- HR-08: productividad de cuadrillas
def hh_por_unidad(presupuesto):
    """{clave: horas-hombre por unidad de la partida} según la mano de obra de su análisis (hh)."""
    from .models import RecursoAPU
    salida = defaultdict(lambda: D0)
    for l in RecursoAPU.objects.filter(partida__presupuesto=presupuesto, recurso__tipo='MO').select_related(
            'recurso', 'partida__subpresupuesto'):
        if l.recurso.unidad.strip().lower() in ('hh', 'h-h', 'hh.', 'h.h.'):
            salida[l.partida.clave] += l.cantidad
    return salida


def productividad(obra):
    """Horas del tareo por cuadrilla y partida frente al metrado ejecutado (repartido entre las cuadrillas por sus
    horas del mes) y a las horas del análisis (APU): eficiencia = horas ganadas (APU × metrado) ÷ horas reales."""
    from .models import LineaTareo
    meta = presupuesto_meta(obra)
    apu = hh_por_unidad(meta) if meta is not None else {}
    def semana(fecha):  # HR-08: avance y horas se cruzan por semana (lunes), no por mes
        return fecha - timedelta(days=fecha.weekday())

    horas_sem = defaultdict(lambda: D0)  # (cuadrilla, clave, semana)
    total_horas = defaultdict(lambda: D0)  # (clave, semana)
    partidas = {}
    for l in LineaTareo.objects.filter(tareo__obra=obra, partida__isnull=False).select_related(
            'tareo', 'partida__subpresupuesto'):
        h = l.horas + l.horas_60 + l.horas_100
        clave, sem = l.partida.clave, semana(l.tareo.fecha)
        cuadrilla = (l.tareo.cuadrilla or 'Sin cuadrilla').strip()
        horas_sem[(cuadrilla, clave, sem)] += h
        total_horas[(clave, sem)] += h
        partidas[clave] = l.partida
    metrado_sem = defaultdict(lambda: D0)
    for a in AvancePartida.objects.filter(obra=obra).select_related('partida__subpresupuesto'):
        metrado_sem[(a.partida.clave, semana(a.fecha))] += a.metrado
        partidas.setdefault(a.partida.clave, a.partida)
    # avance de semanas sin horas de tareo en la partida: lo hizo otra gente (subcontrato, equipo) o falta el tareo
    sin_horas = [{'partida': partidas[k], 'semana': s, 'metrado': m} for (k, s), m in sorted(
        metrado_sem.items(), key=lambda kv: (kv[0][1], kv[0][0])) if m and not total_horas.get((k, s))]
    horas, metrado_parte = defaultdict(lambda: D0), defaultdict(lambda: D0)  # (cuadrilla, clave, periodo)
    for (cuadrilla, clave, sem), h in horas_sem.items():
        if h:
            horas[(cuadrilla, clave, _periodo(sem))] += h
            metrado_parte[(cuadrilla, clave, _periodo(sem))] += (metrado_sem.get((clave, sem), D0) * h
                                                               / total_horas[(clave, sem)])
    detalle = []
    por_cuadrilla = defaultdict(lambda: {'horas': D0, 'ganadas': D0, 'metrado': D0})
    tendencia = defaultdict(lambda: defaultdict(lambda: {'horas': D0, 'ganadas': D0}))
    por_partida = defaultdict(lambda: {'horas': D0, 'metrado': D0, 'ganadas': D0})
    for (cuadrilla, clave, per), h in sorted(horas.items()):
        if not h:
            continue
        parte = metrado_parte[(cuadrilla, clave, per)]
        ganadas = parte * apu.get(clave, D0)
        detalle.append({'cuadrilla': cuadrilla, 'partida': partidas[clave], 'periodo': per, 'horas': h,
                        'metrado': parte.quantize(Decimal('0.01')),
                        'rendimiento': _div(parte, h, '0.001'), 'rendimiento_apu': _div(UNO, apu[clave], '0.001')
                        if apu.get(clave) else None, 'eficiencia': _pct(ganadas, h) if apu.get(clave) else None})
        c = por_cuadrilla[cuadrilla]
        c['horas'] += h
        c['metrado'] += parte
        tendencia[cuadrilla][per]['horas'] += h
        pp = por_partida[(cuadrilla, clave)]
        pp['horas'] += h
        pp['metrado'] += parte
        if apu.get(clave):
            c['ganadas'] += ganadas
            tendencia[cuadrilla][per]['ganadas'] += ganadas
            pp['ganadas'] += ganadas
    cuadrillas = [{'cuadrilla': k, 'horas': v['horas'], 'ganadas': r2(v['ganadas']),
                   'eficiencia': _pct(v['ganadas'], v['horas']),
                   'tendencia': [(per, _pct(t['ganadas'], t['horas'])) for per, t in sorted(tendencia[k].items())]}
                  for k, v in sorted(por_cuadrilla.items())]
    for c in cuadrillas:
        valores = [e for _, e in c['tendencia'] if e is not None]
        c['tendencia_texto'] = ('mejora' if valores[-1] > valores[0] else 'empeora' if valores[-1] < valores[0]
                                else 'estable') if len(valores) >= 2 else '—'
    return {'cuadrillas': cuadrillas, 'detalle': detalle, 'sin_apu': not apu, 'sin_horas': sin_horas,
            'partidas': [{'cuadrilla': c, 'partida': partidas[k], 'horas': v['horas'],
                          'metrado': v['metrado'].quantize(Decimal('0.01')),
                          'rendimiento': _div(v['metrado'], v['horas'], '0.001'),
                          'rendimiento_apu': _div(UNO, apu[k], '0.001') if apu.get(k) else None,
                          'eficiencia': _pct(v['ganadas'], v['horas']) if apu.get(k) else None}
                         for (c, k), v in sorted(por_partida.items(), key=lambda kv: (kv[0][0], kv[0][1]))]}


# ---------------------------------------------------------------- HR-09: pronóstico de compras
def _aportes_materiales(presupuesto):
    """{clave: {recurso: cantidad por unidad}} de materiales (los sub-análisis se explotan a sus componentes)."""
    from .calculos import aporte, componentes_de
    from .models import RecursoAPU
    comps = componentes_de()
    salida = defaultdict(lambda: defaultdict(lambda: D0))

    def agregar(clave, recurso, cantidad, camino):
        if comps.get(recurso.pk) and recurso.pk not in camino:
            for c in comps[recurso.pk]:
                agregar(clave, c.recurso, cantidad * aporte(c, recurso), camino | {recurso.pk})
        elif recurso.tipo == 'MAT' and not recurso.es_porcentaje_mo and cantidad:
            salida[clave][recurso] += cantidad

    for l in RecursoAPU.objects.filter(partida__presupuesto=presupuesto).select_related('recurso__producto',
                                                                                       'partida__subpresupuesto'):
        agregar(l.partida.clave, l.recurso, l.cantidad, frozenset())
    return salida


def pronostico_compras(obra, meses=4, hoy=None):
    """Materiales que pide el cronograma en los próximos meses (metrado programado × aporte del análisis de la meta),
    frente al stock del almacén de obra y a lo pedido por llegar: lo que falta pedir y desde cuándo."""
    from core.models import StockAlmacen

    from .control import almacen, pedido
    hoy = hoy or timezone.localdate()
    p = presupuesto_ejecucion(obra)
    if p is None:
        return None
    meta = presupuesto_meta(obra) or p
    aportes = _aportes_materiales(meta)
    if not aportes and meta.pk != p.pk:
        aportes = _aportes_materiales(p)
    actual = _periodo(hoy)
    columnas = _meses(date(hoy.year, hoy.month, 1), date(hoy.year + (hoy.month + meses - 2) // 12,
                                                          (hoy.month + meses - 2) % 12 + 1, 1))
    avance_mes = defaultdict(lambda: D0)
    for a in AvancePartida.objects.filter(obra=obra, fecha__gte=date(hoy.year, hoy.month, 1)).select_related(
            'partida__subpresupuesto'):
        avance_mes[a.partida.clave] += a.metrado
    necesidad = defaultdict(lambda: defaultdict(lambda: D0))
    for prog in ProgramacionPartida.objects.filter(partida__presupuesto=p, periodo__in=columnas).select_related(
            'partida__subpresupuesto'):
        clave = prog.partida.clave
        programado = prog.metrado
        if prog.periodo == actual:  # lo ya ejecutado en el mes no se vuelve a pedir
            programado = max(programado - avance_mes.get(clave, D0), D0)
        for recurso, aporte in aportes.get(clave, {}).items():
            necesidad[recurso][prog.periodo] += programado * aporte
    productos = {r.producto_id for r in necesidad if r.producto_id}
    stock = dict(StockAlmacen.objects.filter(almacen_id=obra.almacen_id, producto_id__in=productos).values_list(
        'producto_id', 'cantidad')) if obra.almacen_id else {}
    pedidos = pedido(obra) if obra.centro_costo_id else {}
    recibido, _ = almacen(obra) if obra.centro_costo_id else ({}, {})
    filas = []
    for r, por_mes in sorted(necesidad.items(), key=lambda kv: kv[0].nombre):
        total = sum(por_mes.values(), D0)
        if not total:
            continue
        disponible = stock.get(r.producto_id, D0) if r.producto_id else D0
        por_llegar = max(pedidos.get(r.producto_id, D0) - recibido.get(r.producto_id, D0), D0) if r.producto_id else D0
        faltante, pedir_desde, acumulado = D0, None, D0
        for per in columnas:
            acumulado += por_mes.get(per, D0)
            if pedir_desde is None and acumulado > disponible + por_llegar:
                inicio = date(int(per[:4]), int(per[4:]), 1)
                pedir_desde = max(inicio - timedelta(days=DIAS_ANTICIPACION), hoy)
        faltante = max(total - disponible - por_llegar, D0)
        filas.append({'recurso': r, 'producto': r.producto, 'unidad': r.unidad,
                      'meses': [por_mes.get(per, D0).quantize(Decimal('0.01')) for per in columnas],
                      'total': total.quantize(Decimal('0.01')), 'stock': disponible, 'por_llegar': por_llegar,
                      'faltante': faltante.quantize(Decimal('0.01')), 'pedir_desde': pedir_desde,
                      'monto': r2(faltante * r.precio)})
    return {'columnas': columnas, 'filas': filas, 'presupuesto': p, 'meta': meta,
            'sin_cronograma': not ProgramacionPartida.objects.filter(partida__presupuesto=p).exists()}


# ---------------------------------------------------------------- HR-10: comprar o alquilar equipos
def comprar_o_alquilar(meses=12, hoy=None):
    """Por equipo propio: costo hora = (depreciación del activo fijo + combustible + gastos + mantenimiento) ÷ horas de
    los partes, frente a la tarifa de alquiler del mercado (ficha del equipo o lo pagado en partes de alquiler).
    Aparte, los equipos alquilados con sus horas y gasto."""
    from django.db.models import Sum

    from flota.models import CargaCombustible, GastoEquipo
    from produccion.models import Equipo, OrdenMantenimiento

    from .models import ParteEquipo
    hoy = hoy or timezone.localdate()
    desde = hoy - timedelta(days=30 * meses)
    partes = ParteEquipo.objects.filter(fecha__gte=desde, fecha__lte=hoy)
    usados = set(partes.filter(propiedad='PROPIO', equipo__isnull=False).values_list('equipo_id', flat=True))
    equipos = Equipo.objects.filter(pk__in=usados) | Equipo.objects.filter(tarifa_alquiler__gt=0, activo=True)
    filas = []
    for e in equipos.select_related('activo_fijo').distinct().order_by('codigo'):
        horas = partes.filter(equipo=e, propiedad='PROPIO').aggregate(h=Sum('horas'))['h'] or D0
        horas_base = horas or Decimal(e.horas_mes * meses)
        af = e.activo_fijo
        if af is not None and af.vida_util_meses:
            dep_mes = (af.valor - (af.valor_residual or D0)) / af.vida_util_meses
            fuente_dep = f'activo fijo {af.codigo}'
        elif e.valor_reposicion:
            dep_mes = e.valor_reposicion / 60
            fuente_dep = 'valor de reposición en 5 años'
        else:
            dep_mes, fuente_dep = D0, 'sin activo fijo ni valor de reposición'
        combustible = CargaCombustible.objects.filter(equipo=e, fecha__gte=desde, fecha__lte=hoy).aggregate(
            s=Sum('importe'))['s'] or D0
        gastos = GastoEquipo.objects.filter(equipo=e, fecha__gte=desde, fecha__lte=hoy).aggregate(
            s=Sum('importe'))['s'] or D0
        mant = OrdenMantenimiento.objects.filter(equipo=e, fecha_programada__gte=desde, fecha_programada__lte=hoy) \
            .exclude(estado='ANULADA').aggregate(m=Sum('costo_mano_obra'), s=Sum('costo_servicios'))
        mantenimiento = (mant['m'] or D0) + (mant['s'] or D0)
        fijo = dep_mes * meses
        variable = combustible + gastos + mantenimiento
        costo_hora = r2((fijo + variable) / horas_base) if horas_base else None
        variable_hora = (variable / horas_base) if horas_base else D0
        tarifa = e.tarifa_alquiler
        fuente_tarifa = 'ficha del equipo'
        if not tarifa:
            prom = partes.filter(equipo=e, propiedad='ALQUILADO', tarifa__gt=0).aggregate(t=Sum('tarifa'))
            n = partes.filter(equipo=e, propiedad='ALQUILADO', tarifa__gt=0).count()
            tarifa = r2(prom['t'] / n) if n else D0
            fuente_tarifa = 'partes de alquiler' if n else ''
        if costo_hora is None or not tarifa:
            decision = 'Registre la tarifa de alquiler del mercado en la ficha del equipo (Flota)' if not tarifa \
                else 'Sin horas: registre los partes de equipo'
        else:
            decision = 'Conviene tenerlo (propio)' if costo_hora <= tarifa else 'Conviene alquilarlo'
        equilibrio = r2(dep_mes / (tarifa - variable_hora)) if tarifa and tarifa > variable_hora and dep_mes else None
        filas.append({'equipo': e, 'horas': horas, 'horas_mes': r2(horas / meses) if horas else D0,
                      'depreciacion_mes': r2(dep_mes), 'fuente_dep': fuente_dep, 'combustible': combustible,
                      'gastos': gastos, 'mantenimiento': mantenimiento, 'costo_hora': costo_hora, 'tarifa': tarifa,
                      'fuente_tarifa': fuente_tarifa, 'ahorro_hora': r2(tarifa - costo_hora)
                      if costo_hora is not None and tarifa else None,
                      'ahorro_periodo': r2((tarifa - costo_hora) * horas) if costo_hora is not None and tarifa and horas
                      else None, 'equilibrio': equilibrio, 'decision': decision})
    alquilados = defaultdict(lambda: {'horas': D0, 'costo': D0, 'meses': set(), 'proveedores': set()})
    for x in partes.filter(propiedad='ALQUILADO').select_related('equipo', 'proveedor'):
        a = alquilados[x.nombre_equipo or 'Sin nombre']
        a['horas'] += x.horas
        a['costo'] += x.costo
        a['meses'].add(_periodo(x.fecha))
        if x.proveedor_id:
            a['proveedores'].add(x.proveedor.nombre)
    return {'meses': meses, 'filas': filas,
            'alquilados': [{'equipo': k, 'horas': v['horas'], 'costo': v['costo'], 'meses': len(v['meses']),
                            'tarifa': _div(v['costo'], v['horas']), 'gasto_mes': r2(v['costo'] / meses),
                            'proveedores': ', '.join(sorted(v['proveedores']))}
                           for k, v in sorted(alquilados.items(), key=lambda kv: -kv[1]['costo'])]}


# ---------------------------------------------------------------- HR-11: evaluación de subcontratistas
def evaluar_subcontrato(subcontrato, plazo, calidad, seguridad, usuario, comentario=''):
    notas = []
    for nombre, valor in (('plazo', plazo), ('calidad', calidad), ('seguridad', seguridad)):
        try:
            v = int(valor)
        except (TypeError, ValueError):
            v = 0
        if not 1 <= v <= 5:
            raise ErrorGestion(f'Califique {nombre} de 1 a 5.')
        notas.append(v)
    if subcontrato.estado not in ('VIGENTE', 'LIQUIDADO'):
        raise ErrorGestion('Se evalúa un subcontrato vigente o liquidado.')
    ev, _ = EvaluacionSubcontrato.objects.update_or_create(subcontrato=subcontrato, defaults={
        'plazo': notas[0], 'calidad': notas[1], 'seguridad': notas[2], 'comentario': (comentario or '')[:300],
        'evaluado_por': _usuario(usuario), 'fecha': timezone.localdate()})
    return ev


def historial_subcontratistas():
    """Por subcontratista: subcontratos, monto, evaluaciones y promedios (plazo, calidad, seguridad y general)."""
    from .models import Subcontrato
    por = {}
    for s in Subcontrato.objects.exclude(estado='ANULADO').select_related('proveedor', 'obra').prefetch_related(
            'lineas'):
        p = por.setdefault(s.proveedor_id, {'proveedor': s.proveedor, 'subcontratos': 0, 'monto': D0,
                                            'evaluaciones': [], 'obras': set()})
        p['subcontratos'] += 1
        p['monto'] += s.monto
        p['obras'].add(s.obra.codigo)
    for e in EvaluacionSubcontrato.objects.select_related('subcontrato__proveedor', 'subcontrato__obra'):
        if e.subcontrato.proveedor_id in por:
            por[e.subcontrato.proveedor_id]['evaluaciones'].append(e)
    filas = []
    for p in por.values():
        ev = p['evaluaciones']
        n = len(ev)
        for campo in ('plazo', 'calidad', 'seguridad'):
            p[campo] = (Decimal(sum(getattr(e, campo) for e in ev)) / n).quantize(Decimal('0.1')) if n else None
        p['puntaje'] = (sum((e.puntaje for e in ev), D0) / n).quantize(Decimal('0.1')) if n else None
        p['n'] = n
        p['ultima'] = max(ev, key=lambda e: e.fecha) if ev else None
        p['obras'] = ', '.join(sorted(p['obras']))
        filas.append(p)
    return sorted(filas, key=lambda p: (p['puntaje'] is None, -(p['puntaje'] or 0), p['proveedor'].nombre))


# ---------------------------------------------------------------- HR-13: rentabilidad por cliente y tipo
def resultado_obra(obra):
    """(ingreso sin IGV, costo real, fuente): lo valorizado y el costo operativo (o contable si no hay)."""
    from .control import costo_contable
    ingreso = sum((v.subtotal for v in obra.valorizaciones.filter(estado__in=VIGENTES)), D0)
    if not ingreso and obra.centro_costo_id:
        from django.db.models import Sum

        from contabilidad.models import AsientoLinea
        agg = AsientoLinea.objects.filter(centro_costo_id=obra.centro_costo_id, es_destino=False,
                                          cuenta__codigo__startswith='7').aggregate(d=Sum('debe'), h=Sum('haber'))
        ingreso = (agg['h'] or D0) - (agg['d'] or D0)
    # OBR-27: el mismo costo real que el valor ganado y la proyección (registros_costo); el contable va aparte
    obra.costo_contable_ = costo_contable(obra) if obra.centro_costo_id else D0
    return ingreso, costo_real(obra)


def rentabilidad(tipo=None, cliente=None):
    """Por obra (con su margen real y el proyectado al cierre), agrupado por cliente y por tipo de proyecto."""
    qs = Obra.objects.exclude(estado__in=('LICITACION', 'NO_ADJUDICADA')).select_related('cliente')
    if tipo:
        qs = qs.filter(tipo=tipo)
    if cliente:
        qs = qs.filter(cliente_id=cliente)
    obras, por_cliente, por_tipo = [], {}, {}
    tipos = dict(Obra.TIPOS)
    for o in qs:
        ingreso, costo = resultado_obra(o)
        c = control_partidas(o) if o.tipo != 'SERVICIO' else None
        f = {'obra': o, 'ingreso': ingreso, 'costo': costo, 'margen': ingreso - costo, 'contable': o.costo_contable_,
             'margen_pct': _pct(ingreso - costo, ingreso), 'proyectado_pct': c['margen_pct'] if c else None}
        obras.append(f)
        for grupo, clave in ((por_cliente, o.cliente.nombre if o.cliente_id else 'Sin cliente'),
                             (por_tipo, tipos.get(o.tipo, o.tipo))):
            g = grupo.setdefault(clave, {'clave': clave, 'obras': 0, 'ingreso': D0, 'costo': D0})
            g['obras'] += 1
            g['ingreso'] += ingreso
            g['costo'] += costo
    for grupo in (por_cliente, por_tipo):
        for g in grupo.values():
            g['margen'] = g['ingreso'] - g['costo']
            g['margen_pct'] = _pct(g['margen'], g['ingreso'])
    orden = lambda g: (g['margen_pct'] is None, -(g['margen_pct'] or 0))
    return {'obras': sorted(obras, key=lambda f: (f['margen_pct'] is None, -(f['margen_pct'] or 0))),
            'clientes': sorted(por_cliente.values(), key=orden), 'tipos': sorted(por_tipo.values(), key=orden)}


# ---------------------------------------------------------------- SRV-05: consorcios
def consorcio(obra, desde=None, hasta=None):
    """Atribución por socio (participación %) de las ventas, compras e IGV con el centro de costo de la obra (y sus
    facturas de valorizaciones y adelantos) y del costo real: base para la contabilidad o la declaración de cada
    socio (confirme el tratamiento con su contador: contabilidad independiente o atribución)."""
    from django.db.models import Sum

    from compras.models import Compra
    socios = list(obra.socios.select_related('tercero'))
    ventas = _documentos_de_obra(obra).filter(estado='REGISTRADO')
    compras = Compra.objects.filter(centro_costo_id=obra.centro_costo_id, estado='REGISTRADO') \
        if obra.centro_costo_id else Compra.objects.none()
    if desde:
        ventas, compras = ventas.filter(fecha_emision__gte=desde), compras.filter(fecha_emision__gte=desde)
    if hasta:
        ventas, compras = ventas.filter(fecha_emision__lte=hasta), compras.filter(fecha_emision__lte=hasta)

    def totales(qs):
        signo = {}
        for campo in ('base_pen', 'igv_pen', 'total_pen'):
            mas = qs.exclude(tipo_comprobante='07').aggregate(s=Sum(campo))['s'] or D0
            menos = qs.filter(tipo_comprobante='07').aggregate(s=Sum(campo))['s'] or D0
            signo[campo] = mas - menos
        return signo
    v, c = totales(ventas), totales(compras)
    costo = costo_real(obra)
    suma = sum((s.participacion_pct for s in socios), D0)
    filas = []
    for s in socios:
        f = s.participacion_pct / 100
        filas.append({'socio': s, 'pct': s.participacion_pct, 'ventas': r2(v['base_pen'] * f),
                      'igv_ventas': r2(v['igv_pen'] * f), 'compras': r2(c['base_pen'] * f),
                      'igv_compras': r2(c['igv_pen'] * f), 'costo': r2(costo * f),
                      'igv_neto': r2((v['igv_pen'] - c['igv_pen']) * f),
                      'resultado': r2((v['base_pen'] - costo) * f)})
    return {'socios': socios, 'filas': filas, 'suma': suma, 'ventas': v, 'compras': c, 'costo': costo,
            'docs_ventas': ventas.count(), 'docs_compras': compras.count()}


# ---------------------------------------------------------------- HR-15 y HR-17: valorizaciones proyectadas y flujo
def _fin_de_mes(per):
    a, m = int(per[:4]), int(per[4:])
    return date(a + (m == 12), 1 if m == 12 else m + 1, 1) - timedelta(days=1)


def valorizaciones_proyectadas(obra, hoy=None):
    """Valorizaciones que faltan según el cronograma del contractual: costo directo programado de cada mes × (1 + gastos
    generales y utilidad), hasta lo que falta valorizar; menos la amortización de los adelantos y la retención de
    garantía, con IGV. Se cobran a fin de mes + la aprobación + los días de crédito del cliente.
    [{'periodo', 'valorizacion', 'neto', 'fecha_cobro'}]"""
    from .valorizaciones import saldo_adelanto
    hoy = hoy or timezone.localdate()
    p = presupuesto_contractual(obra)
    if p is None or not p.costo_directo:
        return []
    actual = _periodo(hoy)
    valorizados = set(obra.valorizaciones.filter(estado__in=VIGENTES).values_list('periodo', flat=True))
    programado = defaultdict(lambda: D0)
    for prog in ProgramacionPartida.objects.filter(partida__presupuesto=p, periodo__gte=actual).select_related(
            'partida'):
        if prog.periodo not in valorizados:
            programado[prog.periodo] += prog.metrado * prog.partida.precio
    if not programado:
        return []
    factor = p.subtotal / p.costo_directo
    valorizado = sum((v.subtotal for v in obra.valorizaciones.filter(estado__in=VIGENTES)), D0)
    restante = max(p.subtotal - valorizado, D0)
    adelantos = sum((saldo_adelanto(obra, t) for t in ('DIRECTO', 'MATERIALES')), D0)
    total_programado = sum(programado.values(), D0) * factor
    igv = UNO + p.igv_pct / 100
    dias_credito = obra.cliente.dias_credito if obra.cliente_id else 0
    salida = []
    for per in sorted(programado):
        monto = min(r2(programado[per] * factor), restante)
        if monto <= 0:
            break
        restante -= monto
        amort = r2(adelantos * monto / total_programado) if total_programado else D0
        bruto = r2((monto - amort) * igv)
        neto = r2(bruto * (1 - obra.retencion_garantia_pct / 100))
        salida.append({'periodo': per, 'valorizacion': monto, 'amortizacion': amort, 'neto': neto,
                       'fecha_cobro': _fin_de_mes(per) + timedelta(days=DIAS_APROBACION + (dias_credito or 0))})
    return salida


def _documentos_de_obra(obra):
    """Ventas de la obra: facturas de valorizaciones y adelantos, y las emitidas con su centro de costo."""
    from django.db.models import Q

    from ventas.models import Venta
    ids = set(obra.valorizaciones.filter(factura__isnull=False).values_list('factura_id', flat=True))
    ids |= set(obra.adelantos.values_list('factura_id', flat=True))
    filtro = Q(pk__in=ids)
    if obra.centro_costo_id:
        filtro |= Q(centro_costo_id=obra.centro_costo_id)
    return Venta.objects.filter(filtro)


def flujo_proyecto(obra, meses=6, hoy=None):
    """Flujo de caja del proyecto por mes: real (cobros de sus facturas y pagos con su centro de costo o de sus
    compras y planillas) y proyectado (facturas por cobrar y por pagar, valorizaciones del cronograma y el costo
    programado que falta a precio de la meta)."""
    from django.db.models import Q

    from compras.models import Compra
    from finanzas.models import Movimiento
    hoy = hoy or timezone.localdate()
    actual = _periodo(hoy)
    ventas = _documentos_de_obra(obra)
    real = defaultdict(lambda: {'ingresos': D0, 'egresos': D0})
    filtro_egreso = Q(pk__in=[])
    if obra.centro_costo_id:
        filtro_egreso = Q(centro_costo_id=obra.centro_costo_id) | Q(compra__centro_costo_id=obra.centro_costo_id)
    planillas = obra.planillas_civil.filter(movimiento_pago__isnull=False).values_list('movimiento_pago_id', flat=True)
    filtro_egreso |= Q(pk__in=list(planillas))
    for fecha, tipo, monto in Movimiento.objects.filter(estado='VIGENTE').filter(
            Q(tipo='INGRESO', venta__in=ventas) | Q(tipo='EGRESO') & filtro_egreso).exclude(
            concepto='TRANSFERENCIA').values_list('fecha', 'tipo', 'monto'):
        real[_periodo(fecha)]['ingresos' if tipo == 'INGRESO' else 'egresos'] += monto
    proyectado = defaultdict(lambda: {'ingresos': D0, 'egresos': D0})
    columnas = _meses(date(hoy.year, hoy.month, 1), date(hoy.year + (hoy.month + meses - 2) // 12,
                                                          (hoy.month + meses - 2) % 12 + 1, 1))
    fin = _fin_de_mes(columnas[-1])

    def sumar(fecha, campo, monto):
        per = max(_periodo(fecha), actual)
        if fecha <= fin and monto:
            proyectado[per][campo] += monto

    for v in ventas.model.objects.con_saldos().cobrables().filter(pk__in=ventas.values('pk'), estado='REGISTRADO'):
        if v.saldo > 0:
            sumar(v.fecha_vencimiento or v.fecha_emision, 'ingresos', v.saldo_pen)
    if obra.centro_costo_id:
        for c in Compra.objects.con_saldos().cobrables().filter(centro_costo_id=obra.centro_costo_id,
                                                                estado='REGISTRADO'):
            if c.saldo > 0:
                sumar(c.fecha_vencimiento or c.fecha_emision, 'egresos', c.saldo_pen)
    for x in valorizaciones_proyectadas(obra, hoy):
        sumar(x['fecha_cobro'], 'ingresos', x['neto'])
    p, precios = _precios_costo(obra)
    if p is not None:
        avance_mes = defaultdict(lambda: D0)
        for a in AvancePartida.objects.filter(obra=obra, fecha__gte=date(hoy.year, hoy.month, 1)).select_related(
                'partida__subpresupuesto'):
            avance_mes[a.partida.clave] += a.metrado
        for prog in ProgramacionPartida.objects.filter(partida__presupuesto=p, periodo__in=columnas).select_related(
                'partida__subpresupuesto'):
            metrado = prog.metrado
            if prog.periodo == actual:
                metrado = max(metrado - avance_mes.get(prog.partida.clave, D0), D0)
            # el costo se paga en el mes (planillas semanales, proveedores a 30 días en promedio)
            proyectado[prog.periodo]['egresos'] += r2(metrado * precios.get(prog.partida.clave, D0))
    filas, saldo = [], D0
    for per in sorted(real):
        if per >= actual:
            continue
        r = real[per]
        saldo += r['ingresos'] - r['egresos']
        filas.append({'periodo': per, 'tipo': 'Real', 'ingresos': r2(r['ingresos']), 'egresos': r2(r['egresos']),
                      'neto': r2(r['ingresos'] - r['egresos']), 'acumulado': r2(saldo)})
    for per in columnas:
        r, x = real.get(per, {'ingresos': D0, 'egresos': D0}), proyectado.get(per, {'ingresos': D0, 'egresos': D0})
        ing, egr = r['ingresos'] + x['ingresos'], r['egresos'] + x['egresos']
        saldo += ing - egr
        filas.append({'periodo': per, 'tipo': 'Real + proyectado' if per == actual else 'Proyectado',
                      'ingresos': r2(ing), 'egresos': r2(egr), 'neto': r2(ing - egr), 'acumulado': r2(saldo)})
    minimo = min(filas, key=lambda f: f['acumulado']) if filas else None
    return {'filas': filas, 'valorizaciones': valorizaciones_proyectadas(obra, hoy),
            'minimo': minimo if minimo and minimo['acumulado'] < 0 else None}


# ---------------------------------------------------------------- HR-18: cartera de proyectos
def cartera(estados=None, tipo=None, cliente=None):
    """Una fila por proyecto activo con contrato vigente, avance físico y programado, SPI, CPI, costo real, margen
    proyectado y alertas (atraso, partidas sobre costo, margen bajo la meta)."""
    from .ejecucion import atrasadas
    qs = Obra.objects.filter(estado__in=estados or ACTIVAS).select_related('cliente')
    if tipo:
        qs = qs.filter(tipo=tipo)
    if cliente:
        qs = qs.filter(cliente_id=cliente)
    filas = []
    for o in qs.order_by('codigo'):
        ev = valor_ganado(o) if o.tipo != 'SERVICIO' else None
        c = control_partidas(o) if o.tipo != 'SERVICIO' else None
        ingreso, costo = resultado_obra(o)
        f = {'obra': o, 'contrato': o.monto_contratado, 'valorizado': ingreso, 'costo': costo,
             'avance': ev['avance_pct'] if ev else None,
             'programado': _pct(ev['pv'], ev['bac']) if ev and ev['bac'] else None,
             'spi': ev['spi'] if ev else None, 'cpi': ev['cpi'] if ev else None,
             'margen_pct': c['margen_pct'] if c else _pct(ingreso - costo, ingreso),
             'margen_meta': margen_meta(o), 'atrasadas': len(atrasadas(o)) if ev else 0,
             'sobre_costo': len(c['alertas']) if c else 0, 'bajo_meta': c['bajo_meta'] if c else False,
             'termino_estimado': ev['termino_estimado'] if ev else None}
        f['semaforo'] = ('danger' if f['bajo_meta'] or (f['spi'] is not None and f['spi'] < Decimal('0.85'))
                         or (f['cpi'] is not None and f['cpi'] < Decimal('0.85'))
                         else 'warning' if f['atrasadas'] or f['sobre_costo'] else 'success')
        filas.append(f)
    total = {'contrato': sum((f['contrato'] for f in filas), D0), 'valorizado': sum((f['valorizado'] for f in filas), D0),
             'costo': sum((f['costo'] for f in filas), D0)}
    return {'filas': filas, 'total': total, 'rojo': sum(1 for f in filas if f['semaforo'] == 'danger'),
            'ambar': sum(1 for f in filas if f['semaforo'] == 'warning')}


# ---------------------------------------------------------------- HR-20: modelos predictivos simples
def _historia():
    """Obras terminadas con su atraso (%) y sobrecosto (%) reales."""
    salida = []
    for o in Obra.objects.filter(estado__in=TERMINADAS).exclude(tipo='SERVICIO'):
        c = control_partidas(o)
        if c is None or not c['totales'].get('bac'):
            continue
        sobrecosto = _pct(c['totales']['ac'] - c['totales']['bac'], c['totales']['bac'])
        atraso = None
        fin = o.fecha_terminacion or AvancePartida.objects.filter(obra=o).order_by('-fecha').values_list(
            'fecha', flat=True).first()
        if o.fecha_inicio and o.plazo_dias and fin:
            atraso = _pct((fin - o.fecha_inicio).days - o.plazo_dias, o.plazo_dias)
        salida.append({'obra': o, 'sobrecosto': sobrecosto, 'atraso': atraso})
    return salida


def _estadistica(valores):
    valores = [v for v in valores if v is not None]
    if not valores:
        return None, None, 0
    media = sum(valores, D0) / len(valores)
    desvio = (sum(((v - media) ** 2 for v in valores), D0) / len(valores)).sqrt()
    return media.quantize(Decimal('0.1')), desvio.quantize(Decimal('0.1')), len(valores)


def prediccion(hoy=None):
    """Sobrecosto y atraso esperados de cada obra activa: el promedio de las obras terminadas del mismo tipo (o de
    todas) combinado con sus propios índices (1/CPI − 1 y 1/SPI − 1) según su avance. Con menos de MIN_HISTORIA
    obras terminadas y poco avance, «datos insuficientes»."""
    historia = _historia()
    por_tipo = defaultdict(list)
    for h in historia:
        por_tipo[h['obra'].tipo].append(h)
    modelos = {}
    for t, hs in list(por_tipo.items()) + [('TODAS', historia)]:
        modelos[t] = {'sobrecosto': _estadistica([h['sobrecosto'] for h in hs]),
                      'atraso': _estadistica([h['atraso'] for h in hs]),
                      'probabilidad': _pct(sum(1 for h in hs if (h['sobrecosto'] or 0) > 0), len(hs)) if hs else None}
    filas = []
    for o in Obra.objects.filter(estado__in=('EJECUCION', 'PARALIZADA')).exclude(tipo='SERVICIO').order_by('codigo'):
        ev = valor_ganado(o, hoy)
        if ev is None:
            continue
        avance = (ev['avance_pct'] or D0) / 100
        modelo = modelos.get(o.tipo) if len(por_tipo.get(o.tipo, [])) >= MIN_HISTORIA else (
            modelos['TODAS'] if len(historia) >= MIN_HISTORIA else None)
        propio_costo = (_pct(UNO / ev['cpi'] - 1, 1) if ev['cpi'] else None)
        propio_plazo = (_pct(UNO / ev['spi'] - 1, 1) if ev['spi'] else None)

        def mezclar(base, propio):
            if base is None and (propio is None or avance < Decimal('0.2')):
                return None
            if base is None:
                return propio
            if propio is None:
                return base
            return (base * (1 - avance) + propio * avance).quantize(Decimal('0.1'))

        sobre = mezclar(modelo['sobrecosto'][0] if modelo else None, propio_costo)
        atraso = mezclar(modelo['atraso'][0] if modelo else None, propio_plazo)
        filas.append({'obra': o, 'avance': ev['avance_pct'], 'cpi': ev['cpi'], 'spi': ev['spi'],
                      'sobrecosto': sobre, 'atraso': atraso,
                      'riesgo': None if sobre is None and atraso is None else
                      'Alto' if (sobre or 0) > 10 or (atraso or 0) > 20 else
                      'Medio' if (sobre or 0) > 3 or (atraso or 0) > 5 else 'Bajo',
                      'base': ('obras terminadas del mismo tipo' if modelo is modelos.get(o.tipo) and modelo else
                               'todas las obras terminadas' if modelo else 'solo sus índices (sin historia)')
                      if sobre is not None or atraso is not None else 'datos insuficientes'})
    return {'historia': historia, 'modelos': modelos, 'filas': filas, 'suficiente': len(historia) >= MIN_HISTORIA,
            'min_historia': MIN_HISTORIA}
