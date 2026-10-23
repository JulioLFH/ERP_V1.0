"""Importación de presupuestos desde Excel: el que exporta S10 (hoja de presupuesto + hoja de análisis de precios
unitarios) o la plantilla de Ceiba. Las columnas se reconocen por su nombre, no por su posición.

S10 · Presupuesto:  Ítem | Descripción | Und. | Metrado | Precio S/. | Parcial S/.  (títulos sin unidad ni metrado)
S10 · Análisis:     bloques «Partida 01.01.01 TRAZO…» · «Rendimiento m2/DIA MO. 500.0000 EQ. 500.0000» ·
                    encabezado «Código | Descripción Recurso | Unidad | Cuadrilla | Cantidad | Precio | Parcial» ·
                    grupos «Mano de Obra», «Materiales», «Equipos», «Subcontratos» con sus recursos
Plantilla Ceiba:    hoja Presupuesto (Ítem, Descripción, Unidad, Metrado, Precio) y hoja APU (Ítem, Rendimiento,
                    Jornada, Tipo, Código, Recurso, Unidad, Cuadrilla, Cantidad, Precio), una fila por recurso.
"""
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

ITEM = re.compile(r'^\d{1,3}(\.\d{1,4})*\.?$')
GRUPOS = {'mano de obra': 'MO', 'materiales': 'MAT', 'material': 'MAT', 'equipos': 'EQ', 'equipo': 'EQ',
          'equipos y herramientas': 'EQ', 'subcontratos': 'SUB', 'subcontrato': 'SUB', 'subpartidas': 'SUB',
          'servicios': 'SUB'}
TIPO_POR_CODIGO = {'01': 'MO', '02': 'MAT', '03': 'EQ', '04': 'SUB'}  # codificación habitual de S10


class ErrorImportacion(Exception):
    pass


def _norm(texto):
    texto = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', texto).strip(' .:')


def _num(valor):
    if valor is None or valor == '':
        return None
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor))
    texto = str(valor).strip().replace(',', '')
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def _texto(valor):
    if valor is None:
        return ''
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor).strip()


def _item(valor):
    t = _texto(valor).rstrip('.')
    return t if t and ITEM.match(t) else ''


def _hojas(archivo):
    nombre = (getattr(archivo, 'name', '') or '').lower()
    if nombre.endswith('.xls'):
        raise ErrorImportacion('El archivo .xls (Excel antiguo) no se puede leer: ábralo en Excel y guárdelo como .xlsx.')
    from openpyxl import load_workbook
    try:
        libro = load_workbook(io.BytesIO(archivo.read()), data_only=True, read_only=True)
    except Exception as exc:
        raise ErrorImportacion(f'No se pudo leer el Excel: {exc}') from exc
    return [(h.title, [list(f) for f in h.iter_rows(values_only=True)]) for h in libro.worksheets]


def _encabezado(filas, *requeridos, limite=40):
    """(índice de la fila, {campo: columna}) de la primera fila que tiene todas las columnas requeridas."""
    for i, fila in enumerate(filas[:limite]):
        celdas = [_norm(c) for c in fila]
        cols = {}
        for j, c in enumerate(celdas):
            for campo, pruebas in _CAMPOS.items():
                if campo not in cols and c and any(c == p or c.startswith(p) for p in pruebas):
                    cols[campo] = j
        if all(r in cols for r in requeridos):
            return i, cols
    return None, None


_CAMPOS = {
    'item': ('item', 'it.'),
    'descripcion': ('descripcion', 'descripción', 'recurso', 'nombre', 'concepto'),
    'unidad': ('und', 'unidad', 'u.m', 'um'),
    'metrado': ('metrado', 'cantidad presupuestada'),
    'cantidad': ('cantidad', 'aporte'),
    'cuadrilla': ('cuadrilla', 'cuad'),
    'precio': ('precio', 'p.u', 'costo unitario', 'pu'),
    'parcial': ('parcial', 'total', 'importe'),
    'codigo': ('codigo', 'cod'),
    'rendimiento': ('rendimiento', 'rend'),
    'jornada': ('jornada',),
    'tipo': ('tipo',),
}


def _celda(fila, cols, campo):
    j = cols.get(campo)
    return fila[j] if j is not None and j < len(fila) else None


# ---------------------------------------------------------------- presupuesto (partidas)
def leer_presupuesto(filas):
    i, cols = _encabezado(filas, 'item', 'descripcion', 'unidad')
    if i is None or ('metrado' not in cols and 'cantidad' not in cols):
        return None
    metrado = 'metrado' if 'metrado' in cols else 'cantidad'
    partidas = []
    for fila in filas[i + 1:]:
        item = _item(_celda(fila, cols, 'item'))
        descripcion = _texto(_celda(fila, cols, 'descripcion'))
        if not item or not descripcion:
            continue
        unidad = _texto(_celda(fila, cols, 'unidad'))
        cant = _num(_celda(fila, cols, metrado))
        titulo = not unidad and not cant
        partidas.append({'item': item, 'descripcion': descripcion[:400], 'unidad': unidad[:12],
                         'metrado': str(cant or 0), 'precio': str(_num(_celda(fila, cols, 'precio')) or 0),
                         'parcial': str(_num(_celda(fila, cols, 'parcial')) or 0), 'titulo': titulo})
    return partidas


# ---------------------------------------------------------------- análisis de precios unitarios
def leer_apu_tabla(filas):
    """Plantilla de Ceiba: una fila por recurso con el ítem de su partida."""
    i, cols = _encabezado(filas, 'item', 'codigo', 'cantidad')
    if i is None:
        return None
    apus = {}
    for fila in filas[i + 1:]:
        item = _item(_celda(fila, cols, 'item'))
        nombre = _texto(_celda(fila, cols, 'descripcion'))
        if not item or not nombre:
            continue
        a = apus.setdefault(item, {'rendimiento': '0', 'jornada': '8', 'unidad': '', 'recursos': []})
        if _num(_celda(fila, cols, 'rendimiento')):
            a['rendimiento'] = str(_num(_celda(fila, cols, 'rendimiento')))
        if _num(_celda(fila, cols, 'jornada')):
            a['jornada'] = str(_num(_celda(fila, cols, 'jornada')))
        codigo = _texto(_celda(fila, cols, 'codigo'))
        a['recursos'].append(_recurso(codigo, nombre, _texto(_celda(fila, cols, 'unidad')),
                                      _tipo(_texto(_celda(fila, cols, 'tipo')), codigo,
                                            _texto(_celda(fila, cols, 'unidad'))),
                                      _num(_celda(fila, cols, 'cuadrilla')), _num(_celda(fila, cols, 'cantidad')),
                                      _num(_celda(fila, cols, 'precio')), _num(_celda(fila, cols, 'parcial'))))
    return apus


def _tipo(texto, codigo, unidad):
    t = _norm(texto)
    if t in ('mo', 'mano de obra'):
        return 'MO'
    if t in ('mat', 'material', 'materiales'):
        return 'MAT'
    if t in ('eq', 'equipo', 'equipos', 'equipos y herramientas'):
        return 'EQ'
    if t in ('sub', 'subcontrato', 'subcontratos', 'servicios'):
        return 'SUB'
    u = _norm(unidad).replace(' ', '')
    if u == 'hh':
        return 'MO'
    if u in ('hm', '%mo', '%m.o'):
        return 'EQ'
    return TIPO_POR_CODIGO.get(codigo[:2], 'MAT')


def _recurso(codigo, nombre, unidad, tipo, cuadrilla, cantidad, precio, parcial):
    return {'codigo': codigo[:20], 'nombre': nombre[:250], 'unidad': unidad[:12] or 'und', 'tipo': tipo,
            'cuadrilla': str(cuadrilla) if cuadrilla is not None else None, 'cantidad': str(cantidad or 0),
            'precio': str(precio or 0), 'parcial': str(parcial or 0)}


def leer_apu_bloques(filas):
    """Formato de S10: bloques por partida."""
    apus, actual, tipo, cols = {}, None, None, None
    for fila in filas:
        textos = [_texto(c) for c in fila]
        normas = [_norm(t) for t in textos]
        no_vacias = [(j, t) for j, t in enumerate(textos) if t]
        if not no_vacias:
            continue
        primera = normas[no_vacias[0][0]]
        if primera.startswith('partida'):
            item = next((_item(t) for t in textos if _item(t)), '')
            if not item:  # «Partida 01.01.01 TRAZO…» en una sola celda
                m = re.search(r'(\d{1,3}(?:\.\d{1,4})+)', no_vacias[0][1])
                item = m.group(1) if m else ''
            if item:
                actual = apus.setdefault(item, {'rendimiento': '0', 'jornada': '8', 'unidad': '', 'recursos': []})
                tipo, cols = None, None
            continue
        if actual is None:
            continue
        if primera.startswith('rendimiento'):
            linea = ' '.join(textos)
            m = re.search(r'M\.?O\.?\s*[:=]?\s*([\d.,]+)', linea, re.I)
            numeros = [_num(c) for c in fila if _num(c) is not None]
            rendimiento = _num(m.group(1)) if m else (numeros[0] if numeros else None)
            if rendimiento:
                actual['rendimiento'] = str(rendimiento)
            u = re.search(r'([A-Za-z0-9³²]+)\s*/\s*D[IÍ]A', linea, re.I)
            if u:
                actual['unidad'] = u.group(1)
            j = re.search(r'jornada\s*[:=]?\s*([\d.,]+)', linea, re.I)
            if j:
                actual['jornada'] = str(_num(j.group(1)))
            continue
        if any(n.startswith('codigo') for n in normas) and any(n.startswith('cantidad') for n in normas):
            _, cols = _encabezado([fila], 'codigo', 'cantidad', limite=1)
            continue
        grupo = next((GRUPOS[n] for n in normas if n in GRUPOS), None)
        if grupo and len(no_vacias) <= 2:
            tipo = grupo
            continue
        if cols is None:
            continue
        codigo = _texto(_celda(fila, cols, 'codigo'))
        nombre = _texto(_celda(fila, cols, 'descripcion'))
        cantidad = _num(_celda(fila, cols, 'cantidad'))
        if not codigo or not nombre or cantidad is None or not re.search(r'\d', codigo):
            continue
        unidad = _texto(_celda(fila, cols, 'unidad'))
        actual['recursos'].append(_recurso(
            codigo, nombre, unidad, tipo or _tipo('', codigo, unidad), _num(_celda(fila, cols, 'cuadrilla')), cantidad,
            _num(_celda(fila, cols, 'precio')), _num(_celda(fila, cols, 'parcial'))))
    return apus or None


def leer(archivo):
    """{'partidas': [...], 'apu': {item: {...}}, 'avisos': [...]} del Excel."""
    partidas, apus, avisos = None, {}, []
    for nombre, filas in _hojas(archivo):
        if partidas is None:
            p = leer_presupuesto(filas)
            if p:
                partidas = p
                continue
        a = leer_apu_tabla(filas) or leer_apu_bloques(filas)
        if a:
            apus.update(a)
    if not partidas:
        raise ErrorImportacion('No se encontró la hoja del presupuesto (columnas Ítem, Descripción, Unidad y Metrado).')
    items = {p['item'] for p in partidas}
    sin_partida = [i for i in apus if i not in items]
    if sin_partida:
        avisos.append(f'{len(sin_partida)} análisis sin partida en el presupuesto (se ignoran): '
                      + ', '.join(sin_partida[:10]))
    sin_apu = [p['item'] for p in partidas if not p['titulo'] and p['item'] not in apus]
    if sin_apu:
        avisos.append(f'{len(sin_apu)} partida(s) sin análisis: se importan con su precio del archivo (sin APU).')
    # títulos: una partida con hijos es título aunque traiga unidad (subpresupuestos)
    for p in partidas:
        if any(o['item'].startswith(p['item'] + '.') for o in partidas):
            p['titulo'] = True
    return {'partidas': partidas, 'apu': {i: a for i, a in apus.items() if i in items}, 'avisos': avisos}


# ---------------------------------------------------------------- grabar
def grabar(datos, obra, tipo, nombre, usuario=None, origen='Excel'):
    """Crea el presupuesto con sus partidas, análisis, recursos (los nuevos) y precios; devuelve (presupuesto,
    diferencias [(item, precio del archivo, precio calculado)])."""
    from django.db import transaction

    from . import calculos
    from .models import Partida, PrecioPresupuesto, Presupuesto, Recurso, RecursoAPU
    with transaction.atomic():
        pres = Presupuesto.objects.create(obra=obra, tipo=tipo, nombre=nombre[:250], origen=f'Importado de {origen}'[:40],
                                          gastos_generales_pct=obra.gastos_generales_pct,
                                          utilidad_pct=obra.utilidad_pct)
        recursos, precios = {}, {}
        por_nombre = {(_norm(r.nombre), _norm(r.unidad)): r for r in Recurso.objects.all()}
        for a in datos['apu'].values():
            for r in a['recursos']:
                clave = r['codigo'] or f'{r["nombre"]}|{r["unidad"]}'
                if clave in recursos:
                    continue
                obj = (Recurso.objects.filter(codigo=r['codigo']).first() if r['codigo'] else None) or \
                    por_nombre.get((_norm(r['nombre']), _norm(r['unidad'])))
                if obj is None:
                    obj = Recurso.objects.create(codigo=r['codigo'], nombre=r['nombre'], unidad=r['unidad'],
                                                 tipo=r['tipo'], precio=Decimal('0') if _es_pct(r['unidad'])
                                                 else Decimal(r['precio']))
                    por_nombre[(_norm(obj.nombre), _norm(obj.unidad))] = obj
                recursos[clave] = obj
                if not obj.es_porcentaje_mo:
                    precios.setdefault(obj.pk, Decimal(r['precio']))
        PrecioPresupuesto.objects.bulk_create([PrecioPresupuesto(presupuesto=pres, recurso_id=k, precio=v)
                                               for k, v in precios.items()])
        for p in datos['partidas']:
            a = datos['apu'].get(p['item'])
            partida = Partida.objects.create(
                presupuesto=pres, item=p['item'], descripcion=p['descripcion'], unidad=p['unidad'] or (a or {}).get(
                    'unidad', ''), metrado=Decimal(p['metrado']), es_titulo=p['titulo'],
                precio_fijo=not p['titulo'] and not (a and a['recursos']), precio=Decimal(p['precio']),
                rendimiento=Decimal(a['rendimiento']) if a else Decimal('0'),
                jornada=Decimal(a['jornada']) if a else Decimal('8'))
            if a and not p['titulo']:
                RecursoAPU.objects.bulk_create([RecursoAPU(
                    partida=partida, recurso=recursos[r['codigo'] or f'{r["nombre"]}|{r["unidad"]}'],
                    cuadrilla=Decimal(r['cuadrilla']) if r['cuadrilla'] is not None else None,
                    cantidad=Decimal(r['cantidad']), orden=n) for n, r in enumerate(a['recursos'])])
        calculos.recalcular(pres)
    archivo = {p['item']: Decimal(p['precio']) for p in datos['partidas'] if not p['titulo']}
    diferencias = [(p.item, archivo[p.item], p.precio) for p in pres.partidas.filter(es_titulo=False)
                   if p.item in archivo and abs(archivo[p.item] - p.precio) > Decimal('0.01')]
    return pres, diferencias


def _es_pct(unidad):
    return _norm(unidad).replace(' ', '') in ('%mo', '%m.o')


def resumen(datos):
    """Para la vista previa: conteos y el total del archivo."""
    hojas = [p for p in datos['partidas'] if not p['titulo']]
    recursos = {r['codigo'] or r['nombre'] for a in datos['apu'].values() for r in a['recursos']}
    return {'titulos': len(datos['partidas']) - len(hojas), 'partidas': len(hojas), 'apu': len(datos['apu']),
            'recursos': len(recursos), 'total_archivo': sum((Decimal(p['parcial']) for p in hojas), Decimal('0'))}


def plantilla():
    """Excel de ejemplo con el formato de la plantilla de Ceiba."""
    from openpyxl import Workbook
    libro = Workbook()
    h = libro.active
    h.title = 'Presupuesto'
    h.append(['Ítem', 'Descripción', 'Unidad', 'Metrado', 'Precio', 'Parcial'])
    for fila in [['01', 'OBRAS PROVISIONALES', '', '', '', ''],
                 ['01.01', 'TRAZO, NIVELES Y REPLANTEO', 'm2', 250, '', ''],
                 ['02', 'CONCRETO SIMPLE', '', '', '', ''],
                 ['02.01', 'CONCRETO f\'c=210 kg/cm2', 'm3', 35.5, '', '']]:
        h.append(fila)
    a = libro.create_sheet('APU')
    a.append(['Ítem', 'Rendimiento', 'Jornada', 'Tipo', 'Código', 'Recurso', 'Unidad', 'Cuadrilla', 'Cantidad',
              'Precio'])
    for fila in [['01.01', 500, 8, 'MO', '0147010004', 'PEON', 'hh', 2, '', 19.85],
                 ['01.01', 500, 8, 'MO', '0147010003', 'TOPOGRAFO', 'hh', 1, '', 25.50],
                 ['01.01', 500, 8, 'MAT', '0229060005', 'YESO BOLSA 28 kg', 'bls', '', 0.02, 9.50],
                 ['01.01', 500, 8, 'EQ', '0337010001', 'HERRAMIENTAS MANUALES', '%mo', '', 3, ''],
                 ['02.01', 25, 8, 'MO', '0147010002', 'OPERARIO', 'hh', 2, '', 26.10],
                 ['02.01', 25, 8, 'MO', '0147010004', 'PEON', 'hh', 8, '', 19.85],
                 ['02.01', 25, 8, 'MAT', '0221000001', 'CEMENTO PORTLAND TIPO I (42.5 kg)', 'bls', '', 9.73, 28.50],
                 ['02.01', 25, 8, 'MAT', '0238000000', 'HORMIGON', 'm3', '', 1.05, 65.00],
                 ['02.01', 25, 8, 'EQ', '0349100007', 'MEZCLADORA DE CONCRETO 9-11 p3', 'hm', 1, '', 15.00]]:
        a.append(fila)
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()
