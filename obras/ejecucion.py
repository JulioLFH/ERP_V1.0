"""Planeamiento y control de la ejecución (OBR-17 y OBR-22): cronograma por partida (del S10 o de MS Project, en Excel
o XML), avance físico con foto y responsable, curva S programado vs ejecutado vs valorizado, partidas atrasadas y el
reporte por partida (contractual, meta, avance, valorizado, costo real por tipo y margen) en el orden del S10."""
import calendar
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (AvancePartida, LineaPlanillaCivil, LineaValSubcontrato, ParteEquipo, ProgramacionPartida,
                     RecursoAPU, clave_item)
from .valorizaciones import VIGENTES, presupuesto_ejecucion, presupuesto_meta, r2, valorizado_acumulado

D0 = Decimal('0')
TOLERANCIA_ATRASO = Decimal('10')  # % del programado acumulado
MESES = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'ago': 8, 'set': 9, 'sep': 9,
         'oct': 10, 'nov': 11, 'dic': 12, 'jan': 1, 'apr': 4, 'aug': 8, 'dec': 12}


class ErrorEjecucion(Exception):
    pass


def _periodo(d):
    return f'{d.year}{d.month:02d}'


def _meses(desde, hasta):
    """Periodos AAAAMM entre dos fechas (inclusive)."""
    salida, a, m = [], desde.year, desde.month
    while (a, m) <= (hasta.year, hasta.month):
        salida.append(f'{a}{m:02d}')
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return salida


def repartir_lineal(metrado, inicio, fin):
    """{periodo: metrado} repartido por días calendario entre inicio y fin."""
    if fin < inicio:
        inicio, fin = fin, inicio
    total = (fin - inicio).days + 1
    salida, resto = OrderedDict(), Decimal(metrado)
    meses = _meses(inicio, fin)
    for n, per in enumerate(meses):
        a, m = int(per[:4]), int(per[4:])
        desde = max(inicio, date(a, m, 1))
        hasta = min(fin, date(a, m, calendar.monthrange(a, m)[1]))
        if n == len(meses) - 1:
            salida[per] = resto
        else:
            parte = (Decimal(metrado) * ((hasta - desde).days + 1) / total).quantize(Decimal('0.0001'))
            salida[per] = parte
            resto -= parte
    return salida


# ---------------------------------------------------------------- cronograma
def _dec(v):
    if v in (None, ''):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    try:
        return Decimal(str(v).replace(',', '').replace('%', '').strip())
    except InvalidOperation:
        return None


def _fecha(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    texto = str(v or '').strip()
    for fmt, largo in (('%Y-%m-%dT%H:%M:%S', 19), ('%Y-%m-%d', 10), ('%d/%m/%Y', 10), ('%d-%m-%Y', 10),
                       ('%d/%m/%y', 8)):
        try:
            return datetime.strptime(texto[:largo], fmt).date()
        except ValueError:
            continue
    return None


def hojas(archivo):
    """[(nombre, filas)] de un Excel (.xlsx o .xls); errores como ErrorEjecucion."""
    from .importar import ErrorImportacion, _hojas
    try:
        return _hojas(archivo)
    except ErrorImportacion as exc:
        raise ErrorEjecucion(str(exc)) from exc


def _periodo_de_cabecera(v):
    """Encabezado de columna de mes: fecha, AAAAMM, AAAA-MM, mm/aaaa o «Ene-26» / «Enero 2026»."""
    if isinstance(v, (datetime, date)):
        return _periodo(v)
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    texto = str(v or '').strip().lower()
    if re.fullmatch(r'\d{6}', texto):
        return texto
    m = re.fullmatch(r'(\d{4})[-/](\d{1,2})', texto)
    if m:
        return f'{m.group(1)}{int(m.group(2)):02d}'
    m = re.fullmatch(r'(\d{1,2})[-/](\d{4})', texto)
    if m:
        return f'{m.group(2)}{int(m.group(1)):02d}'
    m = re.fullmatch(r'([a-zñ]{3})[a-zñ]*[\s\-./]*(\d{2,4})', texto)
    if m and m.group(1) in MESES:
        anio = int(m.group(2))
        return f'{anio + 2000 if anio < 100 else anio}{MESES[m.group(1)]:02d}'
    return None


def _indice_partidas(presupuesto):
    """({clave_item: [partidas]}, {descripción: [partidas]}) del presupuesto (sin títulos)."""
    por_item, por_desc = defaultdict(list), defaultdict(list)
    for x in presupuesto.partidas.filter(es_titulo=False).select_related('subpresupuesto'):
        por_item[clave_item(x.item)].append(x)
        por_desc[x.descripcion.strip().lower()].append(x)
    return por_item, por_desc


def _buscar(indices, item, descripcion, sub=''):
    por_item, por_desc = indices
    candidatas = por_item.get(clave_item(item), []) if item else []
    if sub and len(candidatas) > 1:
        candidatas = [x for x in candidatas if x.subpresupuesto_id and x.subpresupuesto.codigo == sub] or candidatas
    if len(candidatas) == 1:
        return candidatas[0]
    if descripcion:
        porde = por_desc.get(descripcion.strip().lower(), [])
        if len(porde) == 1:
            return porde[0]
        if candidatas:
            porde = [x for x in candidatas if x.descripcion.strip().lower() == descripcion.strip().lower()]
            if len(porde) == 1:
                return porde[0]
    return candidatas[0] if len(candidatas) == 1 else None


def leer_project_xml(contenido):
    """[(ítem, descripción, inicio, fin)] de las tareas (no resumen) de un XML de MS Project."""
    try:
        raiz = ET.fromstring(contenido)
    except ET.ParseError as exc:
        raise ErrorEjecucion(f'El XML no se puede leer: {exc}')
    ns = raiz.tag.split('}')[0] + '}' if raiz.tag.startswith('{') else ''
    salida = []
    for t in raiz.iter(f'{ns}Task'):
        def campo(nombre):
            e = t.find(f'{ns}{nombre}')
            return e.text if e is not None and e.text else ''
        if campo('Summary') == '1':
            continue
        inicio, fin = _fecha(campo('Start')), _fecha(campo('Finish'))
        if not inicio or not fin:
            continue
        salida.append((campo('WBS') or campo('OutlineNumber'), campo('Name'), inicio, fin,
                       campo('OutlineNumber')))
    if not salida:
        raise ErrorEjecucion('El XML no tiene tareas con inicio y fin (exporte el proyecto como XML de MS Project).')
    return salida


def leer_excel_cronograma(filas, en_porcentaje=False):
    """[(ítem, descripción, subpresupuesto, {periodo: valor} | (inicio, fin))] de una hoja con la columna Ítem y las
    columnas de cada mes, o con las columnas Inicio y Fin."""
    cab_n, cab = None, None
    for n, fila in enumerate(filas[:30]):
        textos = [str(c or '').strip().lower() for c in fila]
        if any(t in ('ítem', 'item', 'wbs', 'edt') for t in textos):
            cab_n, cab = n, list(fila)
            break
    if cab is None:
        raise ErrorEjecucion('No se encontró la columna «Ítem» en las primeras filas.')
    textos = [str(c or '').strip().lower() for c in cab]

    def col(*nombres):
        for i, t in enumerate(textos):
            if any(t == x or t.startswith(x) for x in nombres):
                return i
        return None
    c_item, c_desc, c_sub = col('ítem', 'item', 'wbs', 'edt'), col('descrip', 'nombre', 'partida'), col('subpres')
    c_ini, c_fin = col('inicio', 'comienzo', 'start'), col('fin', 'término', 'termino', 'finish')
    meses = [(i, _periodo_de_cabecera(c)) for i, c in enumerate(cab)]
    meses = [(i, p) for i, p in meses if p and i not in (c_item, c_desc, c_sub, c_ini, c_fin)]
    if not meses and (c_ini is None or c_fin is None):
        raise ErrorEjecucion('La hoja no tiene columnas de meses (Ene-26, 2026-01, 01/2026…) ni Inicio y Fin.')
    salida = []
    for fila in filas[cab_n + 1:]:
        fila = list(fila) + [None] * len(cab)
        item = str(fila[c_item] or '').strip()
        if item.endswith('.0'):
            item = item[:-2]
        if not item:
            continue
        desc = str(fila[c_desc] or '').strip() if c_desc is not None else ''
        sub = str(fila[c_sub] or '').strip() if c_sub is not None else ''
        if meses:
            valores = {p: _dec(fila[i]) for i, p in meses}
            valores = {p: v for p, v in valores.items() if v}
            if valores:
                salida.append((item, desc, sub, valores))
        else:
            inicio, fin = _fecha(fila[c_ini]), _fecha(fila[c_fin])
            if inicio and fin:
                salida.append((item, desc, sub, (inicio, fin)))
    return salida


@transaction.atomic
def importar_cronograma(presupuesto, archivo, en_porcentaje=False):
    """Carga el cronograma del presupuesto desde el Excel (meses o inicio y fin) o el XML de MS Project; reemplaza la
    programación de las partidas que trae. Devuelve (partidas programadas, [filas sin partida])."""
    indices = _indice_partidas(presupuesto)
    programas, no_encontradas = {}, []
    archivo.seek(0)
    if archivo.name.lower().endswith('.xml'):
        for item, desc, inicio, fin, outline in leer_project_xml(archivo.read()):
            p = _buscar(indices, item, desc) or (_buscar(indices, outline, desc) if outline else None)
            if p is None:
                no_encontradas.append(f'{item} {desc}'.strip())
                continue
            programas[p] = repartir_lineal(p.metrado, inicio, fin)
    else:
        leidas = []
        for _, filas in hojas(archivo):
            try:
                leidas = leer_excel_cronograma(filas, en_porcentaje)
            except ErrorEjecucion:
                continue
            if leidas:
                break
        if not leidas:
            raise ErrorEjecucion('No se encontró el cronograma: una hoja con la columna «Ítem» y las columnas de cada '
                                 'mes (Ene-26, 2026-01…) o de Inicio y Fin.')
        for item, desc, sub, valores in leidas:
            p = _buscar(indices, item, desc, sub)
            if p is None:
                no_encontradas.append(f'{item} {desc}'.strip())
                continue
            if isinstance(valores, tuple):
                programas[p] = repartir_lineal(p.metrado, *valores)
            elif en_porcentaje:
                programas[p] = {per: (p.metrado * v / 100).quantize(Decimal('0.0001')) for per, v in valores.items()}
            else:
                programas[p] = valores
    if not programas:
        raise ErrorEjecucion('Ninguna fila coincide con las partidas del presupuesto (por ítem o descripción).')
    ProgramacionPartida.objects.filter(partida__in=list(programas)).delete()
    ProgramacionPartida.objects.bulk_create([ProgramacionPartida(partida=p, periodo=per, metrado=m)
                                             for p, meses in programas.items() for per, m in meses.items() if m])
    return len(programas), no_encontradas


# ---------------------------------------------------------------- avance físico
@transaction.atomic
def registrar_avance(obra, partida, fecha, metrado, usuario, foto=None, nota=''):
    """Avance físico de la partida; devuelve (avance, aviso) — aviso si el acumulado supera el metrado contratado."""
    if partida.presupuesto.obra_id != obra.pk or partida.es_titulo:
        raise ErrorEjecucion('Elija una partida del presupuesto de la obra.')
    metrado = Decimal(metrado or 0)
    if metrado <= 0:  # OBR-23: un registro errado se anula con su motivo, no se corrige con negativos
        raise ErrorEjecucion('El metrado ejecutado debe ser mayor que cero. Para corregir un avance mal registrado, '
                             'anúlelo en la lista de avances con su motivo.')
    fecha = fecha or timezone.localdate()
    if fecha > timezone.localdate():
        raise ErrorEjecucion('La fecha del avance no puede ser futura.')
    acumulado = (AvancePartida.objects.filter(obra=obra, partida=partida).aggregate(s=Sum('metrado'))['s'] or D0)
    if acumulado + metrado > partida.metrado:
        raise ErrorEjecucion(f'El avance acumulado de {partida.item} ({acumulado + metrado:,.2f}) superaría el metrado '
                             f'de la partida ({partida.metrado:,.2f}; ya ejecutado {acumulado:,.2f}). Si hay mayores '
                             f'metrados, apruebe primero el adicional (Obras › Versiones).')
    a = AvancePartida.objects.create(obra=obra, partida=partida, fecha=fecha, metrado=metrado, nota=nota[:250],
                                     responsable=usuario if usuario and usuario.is_authenticated else None)
    if foto:
        from core.sustentos import adjuntar
        adjuntar(a, foto, usuario, f'Foto del avance {partida.item} {fecha:%d/%m/%Y}')
    return a, ''


@transaction.atomic
def anular_avance(avance, usuario, motivo):
    """OBR-23: anula un avance mal registrado (queda en la lista, ya no cuenta). No si ya está en una valorización
    aprobada o facturada del mes del avance."""
    motivo = (motivo or '').strip()
    if avance.anulado:
        raise ErrorEjecucion('El avance ya está anulado.')
    if len(motivo) < 5:
        raise ErrorEjecucion('Indique el motivo de la anulación.')
    from .models import Valorizacion
    periodo = avance.fecha.strftime('%Y%m')
    cerrada = Valorizacion.objects.filter(obra=avance.obra, periodo=periodo,
                                          estado__in=['APROBADA', 'FACTURADA']).first()
    if cerrada is not None:
        raise ErrorEjecucion(f'El avance ya se valorizó en la valorización N° {cerrada.numero} '
                             f'({cerrada.get_estado_display().lower()}): corríjalo en la valorización del mes siguiente.')
    avance.anulado, avance.motivo_anulacion = True, motivo[:250]
    avance.anulado_por = usuario if usuario and usuario.is_authenticated else None
    avance.anulado_en = timezone.now()
    avance.save(update_fields=['anulado', 'motivo_anulacion', 'anulado_por', 'anulado_en'])
    return avance


# ---------------------------------------------------------------- curva S y atrasos
def _programado_por_mes(presupuesto):
    """{periodo: costo directo programado} y {partida_id: {periodo: metrado}}."""
    por_mes, por_partida = defaultdict(lambda: D0), defaultdict(dict)
    for prog in ProgramacionPartida.objects.filter(partida__presupuesto=presupuesto).select_related('partida'):
        por_mes[prog.periodo] += prog.metrado * prog.partida.precio
        por_partida[prog.partida_id][prog.periodo] = prog.metrado
    return por_mes, por_partida


def curva_s(obra):
    """Series mensuales acumuladas (costo directo) de lo programado, lo ejecutado (avance × precio contractual) y lo
    valorizado, con su % del costo directo del presupuesto."""
    p = presupuesto_ejecucion(obra)
    if p is None:
        return None
    programado, _ = _programado_por_mes(p)
    precios = {x.clave: x.precio for x in p.partidas.filter(es_titulo=False).select_related('subpresupuesto')}
    ejecutado = defaultdict(lambda: D0)
    for a in AvancePartida.objects.filter(obra=obra).select_related('partida__subpresupuesto'):
        ejecutado[_periodo(a.fecha)] += a.metrado * precios.get(a.partida.clave, a.partida.precio)
    valorizado = defaultdict(lambda: D0)
    for per, cd in obra.valorizaciones.filter(estado__in=VIGENTES).values_list('periodo', 'costo_directo'):
        valorizado[per] += cd
    periodos = sorted(set(programado) | set(ejecutado) | set(valorizado))
    if not periodos:
        return {'presupuesto': p, 'periodos': [], 'filas': [], 'total': p.costo_directo}
    todos = _meses(date(int(periodos[0][:4]), int(periodos[0][4:]), 1),
                   date(int(periodos[-1][:4]), int(periodos[-1][4:]), 1))
    total = p.costo_directo or Decimal('1')
    filas, acum = [], {'p': D0, 'e': D0, 'v': D0}
    hoy = _periodo(timezone.localdate())
    for per in todos:
        acum['p'] += programado.get(per, D0)
        acum['e'] += ejecutado.get(per, D0)
        acum['v'] += valorizado.get(per, D0)
        filas.append({'periodo': per, 'etiqueta': f'{per[4:]}/{per[2:4]}', 'programado': r2(acum['p']),
                      'ejecutado': r2(acum['e']) if per <= hoy else None, 'valorizado': r2(acum['v']),
                      'pct_programado': (acum['p'] / total * 100).quantize(Decimal('0.1')),
                      'pct_ejecutado': (acum['e'] / total * 100).quantize(Decimal('0.1')),
                      'pct_valorizado': (acum['v'] / total * 100).quantize(Decimal('0.1'))})
    return {'presupuesto': p, 'periodos': todos, 'filas': filas, 'total': p.costo_directo}


def programado_a_la_fecha(meses, fecha):
    """Metrado programado acumulado a la fecha: los meses cerrados completos y el mes en curso en proporción a sus
    días transcurridos."""
    actual, total = _periodo(fecha), D0
    for per, m in meses.items():
        if per < actual:
            total += m
        elif per == actual:
            dias = calendar.monthrange(fecha.year, fecha.month)[1]
            total += m * fecha.day / dias
    return total


def atrasadas(obra, fecha=None):
    """Partidas cuyo avance acumulado está por debajo de lo programado a la fecha (más allá de la tolerancia)."""
    p = presupuesto_ejecucion(obra)
    if p is None:
        return []
    fecha = fecha or timezone.localdate()
    _, por_partida = _programado_por_mes(p)
    if not por_partida:
        return []
    avance = defaultdict(lambda: D0)
    for a in AvancePartida.objects.filter(obra=obra, fecha__lte=fecha).select_related('partida__subpresupuesto'):
        avance[a.partida.clave] += a.metrado
    salida = []
    for x in p.partidas.filter(pk__in=list(por_partida)).select_related('subpresupuesto'):
        programado = programado_a_la_fecha(por_partida[x.pk], fecha)
        if programado <= 0:
            continue
        real = avance.get(x.clave, D0)
        if real < programado * (1 - TOLERANCIA_ATRASO / 100):
            salida.append({'partida': x, 'programado': programado.quantize(Decimal('0.01')), 'ejecutado': real,
                           'atraso_pct': ((programado - real) / programado * 100).quantize(Decimal('0.1'))})
    return sorted(salida, key=lambda f: -f['atraso_pct'])


# ---------------------------------------------------------------- reporte por partida (OBR-22)
def costo_mano_obra(obra):
    """{partida_id: costo} de las planillas de construcción civil cerradas, por las horas del tareo."""
    salida = defaultdict(lambda: D0)
    for l in LineaPlanillaCivil.objects.filter(planilla__obra=obra, planilla__estado__in=('CERRADA', 'PAGADA')):
        horas = sum((Decimal(str(h)) for h in l.partidas.values()), D0)
        if not horas:
            salida[None] += l.costo
            continue
        for pid, h in l.partidas.items():
            salida[int(pid) if pid not in ('', 'None') else None] += l.costo * Decimal(str(h)) / horas
    return salida


def costo_equipos(obra):
    salida = defaultdict(lambda: D0)
    for pid, costo in ParteEquipo.objects.filter(obra=obra).values_list('partida_id', 'costo'):
        salida[pid] += costo
    return salida


def costo_subcontratos(obra):
    salida = defaultdict(lambda: D0)
    for pid, monto in LineaValSubcontrato.objects.filter(
            valorizacion__subcontrato__obra=obra, valorizacion__estado='APROBADA').values_list(
            'linea__partida_id', 'monto'):
        salida[pid] += monto
    return salida


def costo_materiales(obra, meta, avance):
    """{clave: costo} de los materiales consumidos con el centro de la obra, repartidos entre las partidas que los
    usan según su análisis (aporte × metrado ejecutado o, sin avance, el de la meta). Lo que no se puede repartir
    queda en la clave None."""
    from .control import almacen
    _, consumido = almacen(obra)
    if not consumido or meta is None:
        return {None: sum((v for _, v in consumido.values()), D0)} if consumido else {}
    pesos = defaultdict(dict)  # producto -> {clave: peso}
    for l in RecursoAPU.objects.filter(partida__presupuesto=meta, recurso__producto__isnull=False).select_related(
            'recurso', 'partida__subpresupuesto'):
        x = l.partida
        base = avance.get(x.clave) or x.metrado
        pesos[l.recurso.producto_id][x.clave] = pesos[l.recurso.producto_id].get(x.clave, D0) + l.cantidad * base
    salida = defaultdict(lambda: D0)
    for pid, (_, valor) in consumido.items():
        w = {k: v for k, v in pesos.get(pid, {}).items() if v > 0}
        suma = sum(w.values(), D0)
        if not suma:
            salida[None] += valor
            continue
        for k, v in w.items():
            salida[k] += valor * v / suma
    return salida


def reporte_partidas(obra):
    """Filas por partida del contractual (o la meta) en el orden del S10 con: contractual, meta, avance físico,
    valorizado, costo real por tipo (materiales, mano de obra, equipos, subcontratos, gastos generales asignados) y
    margen; títulos con sus sumas."""
    from contabilidad.reparto import resultado
    p = presupuesto_ejecucion(obra)
    if p is None:
        return None
    meta = presupuesto_meta(obra)
    meta_por_clave = {x.clave: x for x in meta.partidas.select_related('subpresupuesto')} if meta else {}
    avance = defaultdict(lambda: D0)
    for a in AvancePartida.objects.filter(obra=obra).select_related('partida__subpresupuesto'):
        avance[a.partida.clave] += a.metrado
    valorizado = valorizado_acumulado(obra)
    # costos reales registrados contra partidas (de la meta o del contractual) se llevan por clave
    claves = {}
    from .models import Partida
    por_id = {}
    for tipo, costos in (('mo', costo_mano_obra(obra)), ('eq', costo_equipos(obra)),
                         ('sub', costo_subcontratos(obra))):
        ids = [k for k in costos if k]
        for x in Partida.objects.filter(pk__in=ids).select_related('subpresupuesto'):
            claves[x.pk] = x.clave
        por_id[tipo] = defaultdict(lambda: D0)
        for k, v in costos.items():
            por_id[tipo][claves.get(k) if k else None] += v
    por_id['mat'] = costo_materiales(obra, meta, avance)
    # gastos generales asignados por el reparto: en proporción a lo valorizado (o al contractual)
    asignados = D0
    if obra.centro_costo_id:
        filas_r, _ = resultado('190001', '299912', solo_obras=True)
        asignados = sum((f['asignados'] for f in filas_r if f['centro'].pk == obra.centro_costo_id), D0)
    base_gg = {k: m for k, (_, m) in valorizado.items()} or {x.clave: x.parcial for x in p.partidas.filter(
        es_titulo=False).select_related('subpresupuesto')}
    suma_gg = sum(base_gg.values(), D0)
    filas, totales = [], defaultdict(lambda: D0)
    for x in p.partidas.select_related('subpresupuesto').order_by('subpresupuesto__codigo', 'orden'):
        if x.es_titulo:
            filas.append({'partida': x, 'titulo': True})
            continue
        m = meta_por_clave.get(x.clave)
        f = {'partida': x, 'titulo': False, 'contractual': x.parcial, 'meta': m.parcial if m else None,
             'avance': avance.get(x.clave, D0),
             'avance_pct': (avance.get(x.clave, D0) / x.metrado * 100).quantize(Decimal('0.1')) if x.metrado else D0,
             'valorizado': valorizado.get(x.clave, (D0, D0))[1],
             'mat': r2(por_id['mat'].get(x.clave, D0)), 'mo': r2(por_id['mo'].get(x.clave, D0)),
             'eq': r2(por_id['eq'].get(x.clave, D0)), 'sub': r2(por_id['sub'].get(x.clave, D0)),
             'gg': r2(asignados * base_gg.get(x.clave, D0) / suma_gg) if suma_gg else D0}
        f['costo'] = f['mat'] + f['mo'] + f['eq'] + f['sub'] + f['gg']
        f['margen'] = f['valorizado'] - f['costo']
        filas.append(f)
        for k in ('contractual', 'valorizado', 'mat', 'mo', 'eq', 'sub', 'gg', 'costo', 'margen'):
            totales[k] += f[k]
        totales['meta'] += f['meta'] or D0
    sin_partida = {k: r2(por_id[k].get(None, D0)) for k in ('mat', 'mo', 'eq', 'sub')}
    totales['costo'] += sum(sin_partida.values(), D0)
    totales['margen'] -= sum(sin_partida.values(), D0)
    return {'presupuesto': p, 'meta': meta, 'filas': filas, 'totales': totales, 'sin_partida': sin_partida,
            'gg_asignados': asignados}
