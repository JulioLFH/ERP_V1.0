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


def leer(archivo):
    """[{'ruc', 'nombre', 'tipo', 'serie', 'numero', 'fecha', 'base', 'igv', 'total'}] de la propuesta."""
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
         'fecha': col('fecha de emision', 'fecha emision'), 'base': col('bi gravado dg', 'base imponible'),
         'igv': col('igv / ipm dg', 'igv'), 'total': col('total cp', 'importe total', 'total')}
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
                          'total': _dec(valor(fila, 'total'))})
    return registros


def _clave(ruc, tipo, serie, numero):
    return (ruc, tipo, serie.upper(), str(numero).lstrip('0') or '0')


def comparar(registros, periodo):
    """Cruza la propuesta con las compras registradas del periodo (vigentes)."""
    from .models import Compra
    compras = Compra.objects.filter(periodo=periodo).exclude(estado='ANULADO').select_related('tercero')
    erp = {_clave(c.tercero.numero_doc, c.tipo_comprobante, c.serie, c.numero): c for c in compras}
    sunat = {_clave(r['ruc'], r['tipo'], r['serie'], r['numero']): r for r in registros}
    coinciden, diferencias, solo_sunat, solo_erp = [], [], [], []
    for clave, r in sunat.items():
        c = erp.get(clave)
        if c is None:
            # registrada en otro periodo (SUNAT permite anotarla dentro de los 12 meses siguientes)
            otra = Compra.objects.filter(tercero__numero_doc=r['ruc'], tipo_comprobante=r['tipo'],
                                         serie=r['serie'], numero__in=[r['numero'], r['numero'].zfill(8)]) \
                .exclude(estado='ANULADO').first()
            solo_sunat.append({**r, 'otro_periodo': otra})
        elif abs(c.total - r['total']) > TOLERANCIA:
            diferencias.append({**r, 'compra': c, 'dif': c.total - r['total']})
        else:
            coinciden.append({**r, 'compra': c})
    for clave, c in erp.items():
        if clave not in sunat:
            solo_erp.append(c)
    return {'coinciden': coinciden, 'diferencias': diferencias, 'solo_sunat': solo_sunat, 'solo_erp': solo_erp}
