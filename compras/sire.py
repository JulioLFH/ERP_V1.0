"""Cruce del Registro de Compras con la propuesta SIRE (RCE) que SUNAT arma con los comprobantes electrónicos.

El archivo de la propuesta (TXT separado por «|», o el .zip que lo contiene; también CSV o Excel) se lee por los
nombres de sus columnas: Tipo CP/Doc., Serie del CDP, Nro CP o Doc. Nro Inicial, Nro Doc Identidad (del proveedor),
Apellidos Nombres/Razón Social, Fecha de emisión, BI Gravado DG, IGV / IPM DG y Total CP.
"""
import csv
import io
import re
import unicodedata
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

D0 = Decimal('0')
TOLERANCIA = Decimal('1.00')


class ErrorSIRE(Exception):
    pass


def _norm(texto):
    texto = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', texto.strip().lower())


def _dec(valor):
    if valor in (None, ''):
        return D0
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor))
    try:
        return Decimal(str(valor).replace(',', '').strip() or '0')
    except InvalidOperation:
        return D0


def _fecha(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(valor).strip()[:10], fmt).date()
        except ValueError:
            pass
    return None


def _filas(archivo):
    nombre = (getattr(archivo, 'name', '') or '').lower()
    datos = archivo.read()
    if nombre.endswith('.zip') or datos[:2] == b'PK' and not nombre.endswith('.xlsx'):
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                txt = [n for n in z.namelist() if n.lower().endswith(('.txt', '.csv'))]
                if not txt:
                    raise ErrorSIRE('El .zip no contiene el TXT de la propuesta.')
                datos, nombre = z.read(txt[0]), txt[0].lower()
        except zipfile.BadZipFile as exc:
            raise ErrorSIRE('El archivo .zip está dañado.') from exc
    if nombre.endswith('.xlsx'):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(datos), data_only=True, read_only=True)
        return [list(f) for f in wb.active.iter_rows(values_only=True)]
    for codificacion in ('utf-8-sig', 'latin-1'):
        try:
            texto = datos.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    primera = texto.splitlines()[0] if texto else ''
    sep = max('|;,\t', key=primera.count)
    return [list(f) for f in csv.reader(io.StringIO(texto), delimiter=sep)]


def _crudo(valor):
    """Valor de una celda de la propuesta tal como vino, guardable en JSON (sesión)."""
    if isinstance(valor, datetime):
        return valor.strftime('%d/%m/%Y %H:%M:%S') if valor.time() != datetime.min.time() else f'{valor:%d/%m/%Y}'
    if isinstance(valor, date):
        return f'{valor:%d/%m/%Y}'
    if isinstance(valor, Decimal):
        return str(valor)
    return valor


def leer(archivo):
    """[{'ruc', 'nombre', 'tipo', 'serie', 'numero', 'fecha', 'base', 'igv', 'total'}] de la propuesta."""
    return [{k: v for k, v in r.items() if k != 'sunat'} for r in leer_completo(archivo)[1]]


def leer_completo(archivo):
    """(encabezados, registros) de la propuesta con todas sus columnas: cada registro lleva además en 'sunat' los
    valores de la fila tal como los entrega SUNAT, en el orden de los encabezados."""
    filas = _filas(archivo)
    for i, fila in enumerate(filas[:20]):
        enc = [_norm(c) for c in fila]
        if any(e.startswith('serie') for e in enc) and any('total' in e for e in enc):
            break
    else:
        raise ErrorSIRE('No se reconoce el archivo: se espera la propuesta RCE de SIRE (columnas Tipo CP, Serie, '
                        'Nro CP, Nro Doc Identidad y Total CP).')

    def col(*claves, ultima=False):
        hallados = [j for j, e in enumerate(enc) if any(e.startswith(c) for c in claves)]
        if not hallados:
            return None
        return hallados[-1] if ultima else hallados[0]

    c = {'tipo': col('tipo cp', 'tipo de comprobante', 'tipo comprobante'), 'serie': col('serie'),
         'numero': col('nro cp', 'numero', 'nro comprobante', 'nro. cp'),
         'ruc': col('nro doc identidad', 'nro. doc identidad', 'numero de documento de identidad', 'ruc proveedor'),
         'nombre': col('apellidos nombres', 'apellidos y nombres', 'razon social', ultima=True),
         'fecha': col('fecha de emision', 'fecha emision'), 'base': col('bi grav', 'base imponible'),
         'igv': col('igv / ipm', 'igv'), 'total': col('total cp', 'importe total', 'total'),
         'tipo_doc': col('tipo doc identidad', 'tipo de documento de identidad'),
         'vencimiento': col('fecha vcto', 'fecha de vencimiento'), 'moneda': col('moneda', 'cod moneda'),
         'tc': col('tipo cambio', 'tipo de cambio'), 'exonerado': col('mto exonerado', 'valor adq. ng', 'adq no grav'),
         'inafecto': col('mto inafecto')}
    if None in (c['serie'], c['numero'], c['ruc'], c['total']):
        raise ErrorSIRE('Faltan columnas en la propuesta (serie, número, documento del proveedor o total).')
    valor = lambda fila, k: fila[c[k]] if c[k] is not None and c[k] < len(fila) else ''
    registros = []
    for fila in filas[i + 1:]:
        if not any(str(v or '').strip() for v in fila):
            continue
        serie = str(valor(fila, 'serie') or '').strip().upper()
        numero = str(valor(fila, 'numero') or '').strip()
        if numero.endswith('.0'):
            numero = numero[:-2]
        if not serie or not numero:
            continue
        tipo = str(valor(fila, 'tipo') or '01').strip()
        registros.append({'ruc': str(valor(fila, 'ruc') or '').strip(), 'nombre': str(valor(fila, 'nombre') or ''),
                          'tipo': tipo.zfill(2) if tipo.isdigit() else tipo, 'serie': serie,
                          'numero': numero.lstrip('0') or '0', 'fecha': _fecha(valor(fila, 'fecha')),
                          'base': _dec(valor(fila, 'base')), 'igv': _dec(valor(fila, 'igv')),
                          'total': _dec(valor(fila, 'total')),
                          'tipo_doc': str(valor(fila, 'tipo_doc') or '').strip(),
                          'vencimiento': _fecha(valor(fila, 'vencimiento')),
                          'moneda': str(valor(fila, 'moneda') or 'PEN').strip().upper() or 'PEN',
                          'tc': _dec(valor(fila, 'tc')),
                          'no_gravado': _dec(valor(fila, 'exonerado')) + _dec(valor(fila, 'inafecto')),
                          'sunat': [_crudo(v) for v in fila]})
    encabezados = [str(e if e is not None else '').strip() for e in filas[i]]
    return encabezados, registros


_IMPORTE = re.compile(r'^-?\d+\.\d+$')


def _celda(valor):
    """Importes del TXT ('1180.00', '3.750') como número en el Excel; códigos, RUC, series y números como texto."""
    if isinstance(valor, str) and _IMPORTE.match(valor.strip()):
        return Decimal(valor.strip())
    return valor


def _clave_fila(r):
    return (r['tipo'], r['serie'], r['numero'], r['ruc'])


def filas_cruce_completo(encabezados, registros, r, extra=None, rendiciones=False):
    """(encabezados, filas) del cruce con TODAS las columnas de la propuesta SUNAT: el resultado del cruce, las columnas
    de SUNAT en su orden y al final el comprobante del ERP, su total y la diferencia. `extra` = (títulos, función(x))
    para columnas propias de cada pantalla (x es el renglón del cruce o None); `rendiciones` marca las compras que
    nadie rindió ni registró."""
    titulos_extra, valor_extra = extra or ([], lambda x: [])
    if not registros:  # sin el archivo original: las columnas que el ERP leyó de la propuesta
        encabezados = ['Nro Doc Identidad', 'Apellidos Nombres/ Razón Social', 'Tipo CP/Doc.', 'Serie del CDP',
                       'Nro CP', 'Fecha de emisión', 'BI Gravado', 'IGV', 'Total CP', 'Moneda']
        registros = [{**x, 'sunat': [x['ruc'], x['nombre'], x['tipo'], x['serie'], x['numero'], x['fecha'],
                                     x['base'], x['igv'], x['total'], x.get('moneda')]}
                     for x in r['coinciden'] + r['diferencias'] + r['solo_sunat']]
    resultado = {}
    for x in r['coinciden']:
        resultado[_clave_fila(x)] = ('Coincide', x, 0)
    for x in r['diferencias']:
        resultado[_clave_fila(x)] = ('Diferencia de importe', x, x['dif'])
    for x in r['solo_sunat']:
        motivo = (' (registrado en otro periodo)' if x['otro_periodo'] else
                  ' (nadie la rindió)' if rendiciones and x['nadie_rindio'] else '')
        resultado[_clave_fila(x)] = ('Solo en SUNAT' + motivo, x, None)
    ancho = max([len(encabezados)] + [len(g.get('sunat') or []) for g in registros])
    sunat = list(encabezados) + [f'Columna {n + 1}' for n in range(len(encabezados), ancho)]
    erp = ['ERP: Doc. tercero', 'ERP: Nombre', 'ERP: Tipo', 'ERP: Serie', 'ERP: Número', 'ERP: Total',
           'Diferencia (ERP - SUNAT)']
    filas = []
    for g in registros:
        texto, x, dif = resultado.get(_clave_fila(g), ('', None, None))
        d = x.get('compra') if x else None
        valores = [_celda(v) for v in g.get('sunat') or []]
        valores += [None] * (ancho - len(valores))
        filas.append([texto] + valores + _erp(d) + [dif] + list(valor_extra(x)))
    for d in r['solo_erp']:
        filas.append(['Solo en el ERP'] + [None] * ancho + _erp(d) + [None] + list(valor_extra(None)))
    return ['Resultado del cruce'] + sunat + erp + list(titulos_extra), filas


def _erp(d):
    if d is None:
        return [None] * 6
    return [d.tercero.numero_doc, d.tercero.nombre, d.tipo_comprobante, d.serie, d.numero, d.total]


FECHAS, IMPORTES = ('fecha', 'vencimiento'), ('base', 'igv', 'total', 'tc', 'no_gravado')


def a_json(registros):
    """Registros leídos → datos guardables en JSON (sesión o PropuestaSIRE.registros)."""
    return [{**r, **{k: r[k].isoformat() if r.get(k) else None for k in FECHAS if k in r},
             **{k: str(r[k]) for k in IMPORTES if k in r}} for r in registros]


def de_json(datos):
    return [{**r, **{k: date.fromisoformat(r[k]) if r.get(k) else None for k in FECHAS if k in r},
             **{k: Decimal(r[k]) for k in IMPORTES if k in r}} for r in datos]


def _clave(ruc, tipo, serie, numero):
    return (ruc, tipo, serie.upper(), str(numero).lstrip('0') or '0')


def _rendidas(registros):
    """{clave: gasto rendido} de las facturas de la propuesta que llegaron por rendición de gastos."""
    from finanzas.models import LineaGasto
    rucs = {r['ruc'] for r in registros}
    lineas = (LineaGasto.objects.filter(ruc__in=rucs).exclude(estado='RECHAZADA')
              .exclude(rendicion__estado='RECHAZADA').select_related('rendicion__entrega__responsable'))
    return {_clave(l.ruc, l.tipo_comprobante, l.serie, l.numero): l for l in lineas}


def comparar(registros, periodo):
    """Cruza la propuesta con las compras registradas del periodo (vigentes). Marca las que vinieron por rendición
    de gastos y las que nadie rindió ni registró."""
    from .models import Compra
    compras = Compra.objects.filter(periodo=periodo).exclude(estado='ANULADO').select_related('tercero')
    erp = {_clave(c.tercero.numero_doc, c.tipo_comprobante, c.serie, c.numero): c for c in compras}
    sunat = {_clave(r['ruc'], r['tipo'], r['serie'], r['numero']): r for r in registros}
    rendidas = _rendidas(registros)
    coinciden, diferencias, solo_sunat, solo_erp = [], [], [], []
    for clave, r in sunat.items():
        c = erp.get(clave)
        rendida = rendidas.get(clave)
        if c is None:
            # registrada en otro periodo (SUNAT permite anotarla dentro de los 12 meses siguientes)
            otra = Compra.objects.filter(tercero__numero_doc=r['ruc'], tipo_comprobante=r['tipo'],
                                         serie=r['serie'], numero__in=[r['numero'], r['numero'].zfill(8)]) \
                .exclude(estado='ANULADO').first()
            solo_sunat.append({**r, 'otro_periodo': otra, 'rendicion': rendida,
                               'nadie_rindio': otra is None and rendida is None})
        elif abs(c.total - r['total']) > TOLERANCIA:
            diferencias.append({**r, 'compra': c, 'dif': c.total - r['total'], 'rendicion': rendida})
        else:
            coinciden.append({**r, 'compra': c, 'rendicion': rendida})
    for clave, c in erp.items():
        if clave not in sunat:
            solo_erp.append(c)
    return {'coinciden': coinciden, 'diferencias': diferencias, 'solo_sunat': solo_sunat, 'solo_erp': solo_erp,
            'por_rendicion': sum(1 for x in coinciden + diferencias if x['rendicion']),
            'nadie_rindio': [x for x in solo_sunat if x['nadie_rindio']]}


def _clave_venta(tipo, serie, numero):
    return (tipo, serie.upper(), str(numero).lstrip('0') or '0')


def comparar_ventas(registros, periodo):
    """Cruza la propuesta RVIE con los comprobantes de venta del periodo (la empresa es la emisora: la clave es tipo,
    serie y número)."""
    from ventas.models import Venta
    ventas = (Venta.objects.filter(periodo=periodo).exclude(estado='ANULADO').exclude(es_historico=True)
              .select_related('tercero'))
    erp = {_clave_venta(v.tipo_comprobante, v.serie, v.numero): v for v in ventas}
    sunat = {_clave_venta(r['tipo'], r['serie'], r['numero']): r for r in registros}
    coinciden, diferencias, solo_sunat = [], [], []
    for clave, r in sunat.items():
        v = erp.get(clave)
        if v is None:
            otra = Venta.objects.filter(tipo_comprobante=r['tipo'], serie=r['serie'],
                                        numero__in=[r['numero'], r['numero'].zfill(8)]).first()
            solo_sunat.append({**r, 'otro_periodo': otra, 'rendicion': None, 'nadie_rindio': otra is None})
        elif abs(v.total - r['total']) > TOLERANCIA:
            diferencias.append({**r, 'compra': v, 'dif': v.total - r['total'], 'rendicion': None})
        else:
            coinciden.append({**r, 'compra': v, 'rendicion': None})
    solo_erp = [v for clave, v in erp.items() if clave not in sunat]
    return {'coinciden': coinciden, 'diferencias': diferencias, 'solo_sunat': solo_sunat, 'solo_erp': solo_erp,
            'por_rendicion': 0, 'nadie_rindio': [x for x in solo_sunat if x['nadie_rindio']]}


def cruzar(libro, registros, periodo):
    return comparar_ventas(registros, periodo) if libro == 'RVIE' else comparar(registros, periodo)


def _tercero(r, tipo_tercero):
    from core.models import Tercero
    doc = (r.get('ruc') or '').strip()
    if not doc or doc in ('0', '-') or set(doc) == {'0'}:  # boleta a clientes varios
        tercero, _ = Tercero.objects.get_or_create(numero_doc='00000000', defaults={
            'nombre': 'CLIENTES VARIOS', 'tipo': tipo_tercero, 'tipo_doc': '0'})
        return tercero
    tipo_doc = r.get('tipo_doc') or ('6' if len(doc) == 11 else '1')
    tercero, creado = Tercero.objects.get_or_create(numero_doc=doc, defaults={
        'nombre': (r.get('nombre') or doc)[:200], 'tipo': tipo_tercero,
        'tipo_doc': tipo_doc if tipo_doc in ('6', '1', '4', '7', '0') else '0'})
    if not creado and tercero.tipo not in (tipo_tercero, 'AMBOS'):
        tercero.tipo = 'AMBOS'
        tercero.save(update_fields=['tipo'])
    return tercero


def crear_faltantes(libro, registros, periodo, claves, usuario=None):
    """Crea en el ERP los comprobantes de la propuesta marcados (los que están solo en SUNAT). Las ventas quedan
    como ya informadas a SUNAT (emitidas fuera del sistema): no se reenvían al OSE ni mueven stock. Devuelve
    (creados, errores)."""
    from django.db import IntegrityError, transaction

    from ventas.models import Venta

    from .models import Compra
    resultado = cruzar(libro, registros, periodo)
    faltan = {f'{r["tipo"]}|{r["serie"]}|{r["numero"]}|{r["ruc"]}': r for r in resultado['solo_sunat']
              if not r['otro_periodo']}
    creados, errores = 0, []
    for clave in claves:
        r = faltan.get(clave)
        if r is None:
            continue
        try:
            with transaction.atomic():
                tercero = _tercero(r, 'CLIENTE' if libro == 'RVIE' else 'PROVEEDOR')
                base, igv, no_grav = r['base'], r['igv'], r.get('no_gravado') or D0
                total = r['total'] or base + igv + no_grav
                datos = dict(tipo_comprobante=r['tipo'], serie=r['serie'], numero=r['numero'],
                             fecha_emision=r['fecha'], fecha_vencimiento=r.get('vencimiento') or r['fecha'],
                             tercero=tercero, moneda=r.get('moneda') or 'PEN',
                             tipo_cambio=r.get('tc') or Decimal('1'), tipo_operacion='GRAVADA' if igv else 'EXONERADA',
                             base_imponible=base, no_gravado=no_grav, exonerado=no_grav, igv=igv, total=total,
                             periodo=periodo, glosa=f'Creado desde la propuesta SIRE {periodo}')
                if libro == 'RVIE':
                    Venta.objects.create(**datos, descontar_stock=False, estado_sunat='ACEPTADO',
                                         sunat_descripcion='Informado en SUNAT (propuesta SIRE): emitido fuera del '
                                                           'sistema')
                else:
                    Compra.objects.create(**datos)
            creados += 1
        except IntegrityError:
            errores.append(f'{r["serie"]}-{r["numero"]}: ya existe en el sistema.')
        except Exception as exc:  # dato de la propuesta que el modelo no acepta: se informa y se sigue
            errores.append(f'{r["serie"]}-{r["numero"]}: {exc}')
    return creados, errores
