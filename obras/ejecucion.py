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
    periodo = None
    if re.fullmatch(r'\d{6}', texto):
        periodo = texto
    elif m := re.fullmatch(r'(\d{4})[-/](\d{1,2})', texto):
        periodo = f'{m.group(1)}{int(m.group(2)):02d}'
    elif m := re.fullmatch(r'(\d{1,2})[-/](\d{4})', texto):
        periodo = f'{m.group(2)}{int(m.group(1)):02d}'
    elif (m := re.fullmatch(r'([a-zñ]{3})[a-zñ]*[\s\-./]*(\d{2,4})', texto)) and m.group(1) in MESES:
        anio = int(m.group(2))
        periodo = f'{anio + 2000 if anio < 100 else anio}{MESES[m.group(1)]:02d}'
    # OBR-35: «13/2026» o «2026-00» no son meses
    if periodo and 1 <= int(periodo[4:]) <= 12 and 1900 <= int(periodo[:4]) <= 2200:
        return periodo
    return None


def _indice_partidas(presupuesto):
    """({clave_item: [partidas]}, {descripción: [partidas]}) del presupuesto (sin títulos)."""
    por_item, por_desc = defaultdict(list), defaultdict(list)
    for x in presupuesto.partidas.filter(es_titulo=False).select_related('subpresupuesto'):
        por_item[clave_item(x.item)].append(x)
        por_desc[x.descripcion.strip().lower()].append(x)
    return por_item, por_desc


def _mismo_sub(x, sub):
    return bool(x.subpresupuesto_id) and clave_item(x.subpresupuesto.codigo) == clave_item(sub)


def _candidatas(indices, item, sub=''):
    """Partidas con ese ítem; con el subpresupuesto (columna o prefijo del ítem: 001.01.01.01) si viene."""
    por_item, _ = indices
    candidatas = por_item.get(clave_item(item), []) if item else []
    if sub:
        return [x for x in candidatas if _mismo_sub(x, sub)]
    if not candidatas and item and '.' in item:  # OBR-28: ítem con el código del subpresupuesto delante
        prefijo, resto = item.split('.', 1)
        return [x for x in por_item.get(clave_item(resto), []) if _mismo_sub(x, prefijo)]
    return candidatas


def _elegir(indices, item, descripcion, sub=''):
    """(partida, ambigua): por subpresupuesto + ítem; si el ítem está en varios subpresupuestos, la de igual
    descripción o la del costo directo (no la de gastos generales); sin ítem conocido, por descripción."""
    _, por_desc = indices
    desc = (descripcion or '').strip().lower()
    candidatas = _candidatas(indices, item, sub)
    if len(candidatas) > 1 and desc:
        candidatas = [x for x in candidatas if x.descripcion.strip().lower() == desc] or candidatas
    if len(candidatas) > 1:
        directas = [x for x in candidatas if not (x.subpresupuesto_id and x.subpresupuesto.es_gastos_generales)]
        candidatas = directas or candidatas
    if len(candidatas) == 1:
        return candidatas[0], False
    if candidatas:
        return None, True
    porde = por_desc.get(desc, []) if desc else []
    if len(porde) == 1:
        return porde[0], False
    return None, len(porde) > 1


def _buscar(indices, item, descripcion, sub=''):
    return _elegir(indices, item, descripcion, sub)[0]


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


def leer_excel_cronograma(filas, en_porcentaje=False, errores=None, avisos=None):
    """[(ítem, descripción, subpresupuesto, {periodo: valor} | (inicio, fin))] de una hoja con la columna Ítem y las
    columnas de cada mes, o con las columnas Inicio y Fin. OBR-35: anota en `errores` las celdas que no son números
    y las fechas que faltan, y en `avisos` las columnas que parecen meses pero no se reconocen."""
    errores = [] if errores is None else errores
    avisos = [] if avisos is None else avisos
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
    conocidas = (c_item, c_desc, c_sub, c_ini, c_fin)
    meses = [(i, _periodo_de_cabecera(c)) for i, c in enumerate(cab)]
    ignoradas = [str(cab[i]).strip() for i, p in meses
                 if not p and i not in conocidas and re.search(r'\d', str(cab[i] or ''))
                 and not isinstance(cab[i], (int, float, Decimal))]
    meses = [(i, p) for i, p in meses if p and i not in conocidas]
    if not meses and (c_ini is None or c_fin is None):
        raise ErrorEjecucion('La hoja no tiene columnas de meses (Ene-26, 2026-01, 01/2026…) ni Inicio y Fin.')
    if ignoradas:
        avisos.append(f'Columnas que no son un mes válido y no se leyeron: {", ".join(ignoradas[:8])}.')
    salida = []
    for n_fila, fila in enumerate(filas[cab_n + 1:], cab_n + 2):
        fila = list(fila) + [None] * len(cab)
        item = str(fila[c_item] or '').strip()
        if item.endswith('.0'):
            item = item[:-2]
        if not item:
            continue
        desc = str(fila[c_desc] or '').strip() if c_desc is not None else ''
        sub = str(fila[c_sub] or '').strip() if c_sub is not None else ''
        if meses:
            valores = {}
            for i, p in meses:
                if fila[i] in (None, ''):
                    continue
                v = _dec(fila[i])
                if v is None or not v.is_finite():
                    errores.append(f'Fila {n_fila} ({item}): «{fila[i]}» en {p[4:]}/{p[:4]} no es un número.')
                elif v:
                    valores[p] = v
            if valores:
                salida.append((item, desc, sub, valores))
        else:
            inicio, fin = _fecha(fila[c_ini]), _fecha(fila[c_fin])
            if inicio and fin:
                salida.append((item, desc, sub, (inicio, fin)))
            elif fila[c_ini] not in (None, '') or fila[c_fin] not in (None, ''):
                errores.append(f'Fila {n_fila} ({item}): Inicio o Fin no es una fecha.')
    return salida


def _validar_programa(p, valores, en_porcentaje, desde, hasta):
    """OBR-31 y OBR-35: errores de la programación de una partida (negativos, meses fuera de la obra, más del 100 %
    o del metrado, valores enormes) y el aviso si no llega al total."""
    errores, avisos = [], []
    etiqueta = f'{p.item} {p.descripcion[:40]}'.strip()
    tope = Decimal('100') if en_porcentaje else p.metrado
    unidad = '%' if en_porcentaje else (p.unidad or '')
    for per, v in valores.items():
        mes = f'{per[4:]}/{per[:4]}'
        if v < 0:
            errores.append(f'{etiqueta}: {v:,.2f} en {mes} es negativo.')
        elif v > tope:
            errores.append(f'{etiqueta}: {v:,.2f} {unidad} en {mes} supera el total de la partida '
                           f'({tope:,.2f} {unidad}).')
        if desde and per < desde:
            errores.append(f'{etiqueta}: {mes} es anterior al inicio de la obra ({desde[4:]}/{desde[:4]}).')
        elif hasta and per > hasta:
            avisos.append(f'{etiqueta}: {mes} es posterior al fin del plazo ({hasta[4:]}/{hasta[:4]}).')
    total = sum(valores.values(), D0)
    tolerancia = tope * Decimal('0.005')
    if total > tope + tolerancia:
        errores.append(f'{etiqueta}: el total programado ({total:,.2f} {unidad}) supera '
                       f'{"el 100 %" if en_porcentaje else f"el metrado de la partida ({tope:,.2f} {unidad})"}.')
    elif total < tope - tolerancia:
        avisos.append(f'{etiqueta}: se programó {total:,.2f} de {tope:,.2f} {unidad}.')
    return errores, avisos


def _meses_de_la_obra(obra):
    """(AAAAMM del inicio, AAAAMM del fin del plazo) de la obra; None si no tiene fechas."""
    inicio = obra.fecha_inicio or obra.fecha_contrato
    if inicio is None:
        return None, None
    from datetime import timedelta
    fin = inicio + timedelta(days=obra.plazo_dias) if obra.plazo_dias else None
    return _periodo(inicio), _periodo(fin) if fin else None


@transaction.atomic
def importar_cronograma(presupuesto, archivo, en_porcentaje=False, forzar=False, avisos=None):
    """Carga el cronograma del presupuesto desde el Excel (meses o inicio y fin) o el XML de MS Project; reemplaza la
    programación de las partidas que trae. Devuelve (partidas programadas, [filas sin partida]). Si una fila puede ser
    de varias partidas (el mismo ítem en dos subpresupuestos, OBR-28) no reemplaza nada hasta que se confirme
    (forzar=True: esas filas quedan sin programar). OBR-31 y OBR-35: valida todo el archivo antes de grabar (números,
    negativos, más del 100 % o del metrado, meses antes del inicio, inicio después del fin, ítems repetidos); si una
    fila falla no se graba nada y el mensaje dice cuál. Los avisos (meses después del plazo, partidas que no llegan al
    total, columnas ignoradas) van a la lista `avisos`."""
    avisos = [] if avisos is None else avisos
    indices = _indice_partidas(presupuesto)
    programas, no_encontradas, ambiguas, errores = {}, [], [], []
    desde, hasta = _meses_de_la_obra(presupuesto.obra)

    def programar(p, valores, origen):
        if p in programas:
            errores.append(f'{p.item} {p.descripcion[:40]} está más de una vez en el archivo ({origen}).')
            return
        programas[p] = valores

    def programar_fechas(p, inicio, fin, item):
        valores = repartir_lineal(p.metrado, inicio, fin)
        e, a = _validar_programa(p, valores, False, desde, hasta)
        errores.extend(e)
        avisos.extend(a)
        if not e:
            programar(p, valores, f'ítem {item}')

    def buscar(indices, item, desc, sub=''):  # como _buscar, y anota las filas ambiguas
        p, dudosa = _elegir(indices, item, desc, sub)
        if dudosa:
            ambiguas.append(f'{item} {desc}'.strip())
        return p
    archivo.seek(0)
    if archivo.name.lower().endswith('.xml'):
        for item, desc, inicio, fin, outline in leer_project_xml(archivo.read()):
            p = buscar(indices, item, desc) or (buscar(indices, outline, desc) if outline else None)
            if p is None:
                no_encontradas.append(f'{item} {desc}'.strip())
                continue
            if inicio > fin:
                errores.append(f'{item} {desc}: el inicio ({inicio:%d/%m/%Y}) es posterior al fin ({fin:%d/%m/%Y}).')
                continue
            programar_fechas(p, inicio, fin, item)
    else:
        leidas, errores_hoja, avisos_hoja = [], [], []
        for _, filas in hojas(archivo):
            errores_hoja, avisos_hoja = [], []
            try:
                leidas = leer_excel_cronograma(filas, en_porcentaje, errores_hoja, avisos_hoja)
            except ErrorEjecucion:
                continue
            if leidas or errores_hoja:
                break
        if errores_hoja:  # OBR-35: «abc» en un mes es un error de la fila, no «no se encontró el cronograma»
            errores += errores_hoja
        elif not leidas:
            raise ErrorEjecucion('No se encontró el cronograma: una hoja con la columna «Ítem» y las columnas de cada '
                                 'mes (Ene-26, 2026-01…) o de Inicio y Fin.')
        avisos += avisos_hoja
        for item, desc, sub, valores in leidas:
            p = buscar(indices, item, desc, sub)
            if p is None:
                if f'{item} {desc}'.strip() not in ambiguas:
                    no_encontradas.append(f'{item} {desc}'.strip())
                continue
            if isinstance(valores, tuple):
                inicio, fin = valores
                if inicio > fin:
                    errores.append(f'{item} {desc}'.strip() + f': el inicio ({inicio:%d/%m/%Y}) es posterior al fin '
                                                              f'({fin:%d/%m/%Y}).')
                    continue
                programar_fechas(p, inicio, fin, item)
                continue
            e, a = _validar_programa(p, valores, en_porcentaje, desde, hasta)
            errores += e
            avisos += a
            if e:
                continue
            if en_porcentaje:
                valores = {per: (p.metrado * v / 100).quantize(Decimal('0.0001')) for per, v in valores.items()}
            programar(p, valores, f'ítem {item}')
    if errores:
        raise ErrorEjecucion(f'No se grabó el cronograma: {len(errores)} error(es) en el archivo. '
                             + ' '.join(errores[:8]) + (' …' if len(errores) > 8 else ''))
    if ambiguas and not forzar:
        raise ErrorEjecucion(
            f'No se reemplazó el cronograma: {len(ambiguas)} fila(s) pueden ser de más de una partida (el mismo ítem '
            f'en varios subpresupuestos): {"; ".join(ambiguas[:6])}. Agregue la columna «Subpresupuesto» o escriba el '
            f'ítem con su prefijo (001.01.01.01), o marque «Importar igual» (esas filas quedan sin programar).')
    if not programas:
        raise ErrorEjecucion('Ninguna fila coincide con las partidas del presupuesto (por ítem o descripción).')
    no_encontradas += [f'{x} (ambigua)' for x in ambiguas]
    ProgramacionPartida.objects.filter(partida__in=list(programas)).delete()
    ProgramacionPartida.objects.bulk_create([ProgramacionPartida(partida=p, periodo=per, metrado=m)
                                             for p, meses in programas.items() for per, m in meses.items() if m])
    return len(programas), no_encontradas


# ---------------------------------------------------------------- avance físico
LARGO_NOTA = 250


def validar_fecha_obra(obra, fecha, que='El avance'):
    """OBR-36: la fecha de un registro de la obra va entre su inicio (o el contrato) y hoy."""
    if fecha > timezone.localdate():
        raise ErrorEjecucion(f'{que} no puede tener fecha futura.')
    inicio = obra.fecha_inicio or obra.fecha_contrato
    if inicio and fecha < inicio:
        raise ErrorEjecucion(f'{que} no puede ser anterior al inicio de la obra ({inicio:%d/%m/%Y}).')
    if fecha.year < 2000:
        raise ErrorEjecucion(f'Revise la fecha: {fecha:%d/%m/%Y}.')


@transaction.atomic
def registrar_avance(obra, partida, fecha, metrado, usuario, foto=None, nota=''):
    """Avance físico de la partida; devuelve (avance, aviso) — aviso si el acumulado supera el metrado contratado."""
    if partida.presupuesto.obra_id != obra.pk or partida.es_titulo:
        raise ErrorEjecucion('Elija una partida del presupuesto de la obra.')
    vigente = presupuesto_ejecucion(obra)
    if vigente is not None and partida.presupuesto_id != vigente.pk:  # OBR-36: no la de la meta ni otra versión
        raise ErrorEjecucion(f'La partida {partida.item} es del presupuesto {partida.presupuesto}: el avance se '
                             f'registra en las partidas de {vigente}.')
    metrado = Decimal(metrado or 0)
    if metrado <= 0:  # OBR-23: un registro errado se anula con su motivo, no se corrige con negativos
        raise ErrorEjecucion('El metrado ejecutado debe ser mayor que cero. Para corregir un avance mal registrado, '
                             'anúlelo en la lista de avances con su motivo.')
    fecha = fecha or timezone.localdate()
    validar_fecha_obra(obra, fecha)
    if len(nota or '') > LARGO_NOTA:
        raise ErrorEjecucion(f'La nota tiene {len(nota)} caracteres: el máximo es {LARGO_NOTA}.')
    if foto:
        from core.sustentos import ErrorSustento, validar_imagen
        try:
            validar_imagen(foto)
        except ErrorSustento as exc:  # OBR-34: un .exe era un error 500
            raise ErrorEjecucion(str(exc)) from exc
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
    from .models import Valorizacion
    cerrada = Valorizacion.objects.filter(obra=obra, periodo=_periodo(fecha),
                                          estado__in=('APROBADA', 'FACTURADA')).order_by('-numero').first()
    aviso = ''
    if cerrada is not None:  # OBR-33
        aviso = (f'El mes {fecha:%m/%Y} ya está en la valorización N° {cerrada.numero} '
                 f'({cerrada.get_estado_display().lower()}): este avance no está en ella; pasa a la siguiente '
                 f'valorización.')
    return a, aviso


@transaction.atomic
def anular_avance(avance, usuario, motivo):
    """OBR-23: anula un avance mal registrado (queda en la lista, ya no cuenta). No si ya está en una valorización
    aprobada o facturada del mes del avance."""
    motivo = (motivo or '').strip()
    if avance.anulado:
        raise ErrorEjecucion('El avance ya está anulado.')
    if len(motivo) < 5:
        raise ErrorEjecucion('Indique el motivo de la anulación.')
    # OBR-33: solo está «valorizado» si sin él lo ejecutado quedaría por debajo de lo valorizado en valorizaciones
    # aprobadas o facturadas; un avance registrado después (aunque sea del mismo mes) pasa a la siguiente y se anula
    from .models import LineaValorizacion
    clave = avance.partida.clave
    valorizadas = [l for l in LineaValorizacion.objects.filter(
        valorizacion__obra=avance.obra, valorizacion__estado__in=('APROBADA', 'FACTURADA')).select_related(
        'partida__subpresupuesto', 'valorizacion') if l.partida.clave == clave]
    valorizado = sum((l.metrado for l in valorizadas), D0)
    ejecutado = sum((a.metrado for a in AvancePartida.objects.filter(obra=avance.obra).select_related(
        'partida__subpresupuesto') if a.partida.clave == clave), D0)
    if valorizado > ejecutado - avance.metrado + Decimal('0.0001'):
        ultima = max((l.valorizacion for l in valorizadas if l.metrado), key=lambda v: v.numero, default=None)
        raise ErrorEjecucion(
            'El avance ya se valorizó' + (f' en la valorización N° {ultima.numero} '
                                           f'({ultima.get_estado_display().lower()})' if ultima else '') +
            f': sin él lo ejecutado de {avance.partida.item} ({ejecutado - avance.metrado:,.2f}) quedaría por debajo '
            f'de lo valorizado ({valorizado:,.2f}). Corríjalo en la valorización del mes siguiente.')
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
