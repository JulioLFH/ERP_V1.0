"""Formato de números en pantalla (n.º 46): formato Perú (miles con coma, decimales con punto), cantidades según la
unidad de medida, importes con 2 decimales y costos unitarios con 2 o 4 según Ajustes › Empresa (en «automático»,
4 solo cuando el costo es menor a 1, para no mostrar 0.00 en insumos de céntimos). Lo guardado no cambia."""
from decimal import Decimal, InvalidOperation

# unidades que se cuentan (no se fraccionan normalmente): sin decimales si la cantidad es entera
CONTABLES = {'NIU', 'UND', 'ZZ', 'BX', 'PK', 'DZN', 'MIL', 'C62', 'SET', 'PR', 'BG', 'BO', 'CA', 'CJ', 'PQ'}


def _dec(valor):
    if valor is None or valor == '':
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


class NumeroAmbiguo(ValueError):
    pass


def numero_usuario(valor):
    """OBR-32: número escrito en una casilla con punto o coma decimal (muchos celulares usan la coma). «10,5» y
    «10.5» = 10.5; «1,234.50» y «1.234,50» = 1234.50; «1,234,567» = 1234567. «1,500» (una coma seguida de 3 cifras)
    puede ser mil quinientos o uno y medio: NumeroAmbiguo. Vacío = None; texto que no es número = ValueError."""
    if valor is None:
        return None
    if isinstance(valor, (int, Decimal)):
        return Decimal(valor)
    if isinstance(valor, float):
        return Decimal(str(valor))
    texto = str(valor).strip().replace(' ', '').replace(' ', '').replace("'", '')
    if not texto:
        return None
    if ',' in texto and '.' in texto:
        if texto.rfind(',') > texto.rfind('.'):  # 1.234,50
            texto = texto.replace('.', '').replace(',', '.')
        else:  # 1,234.50
            texto = texto.replace(',', '')
    elif ',' in texto:
        partes = texto.split(',')
        if len(partes) == 2 and len(partes[1]) != 3:
            texto = texto.replace(',', '.')
        elif len(partes) > 2 and all(len(p) == 3 for p in partes[1:]):
            texto = texto.replace(',', '')
        else:
            raise NumeroAmbiguo(f'«{valor}» es ambiguo: escriba {texto.replace(",", "")} o '
                                f'{texto.replace(",", ".")} (con punto decimal).')
    elif texto.count('.') > 1:
        partes = texto.split('.')
        if not all(len(p) == 3 for p in partes[1:]):
            raise ValueError(f'«{valor}» no es un número.')
        texto = texto.replace('.', '')
    try:
        numero = Decimal(texto)
    except InvalidOperation:
        raise ValueError(f'«{valor}» no es un número.') from None
    if not numero.is_finite():
        raise ValueError(f'«{valor}» no es un número.')
    return numero


def cantidad(valor, unidad=''):
    d = _dec(valor)
    if d is None:
        return '' if valor is None else valor
    if (unidad or '').upper() in CONTABLES and d == d.to_integral_value():
        return f'{d:,.0f}'
    return f'{d:,.2f}'


def editable(valor):
    """PLAT-06: número para una casilla editable, sin los ceros que sobran (5.0000 → 5.00; 0.927000 → 0.927) y sin
    separador de miles; mínimo 2 decimales. Lo guardado no cambia."""
    d = _dec(valor)
    if d is None:
        return '' if valor is None else valor
    texto = f'{d.normalize():f}'
    entero, _, dec = texto.partition('.')
    return f'{entero}.{dec.ljust(2, "0")}'


def importe(valor):
    d = _dec(valor)
    return f'{d:,.2f}' if d is not None else ('' if valor is None else valor)


def decimales_costo():
    from .models import Empresa
    e = Empresa.objects.only('decimales_costo').first()
    return e.decimales_costo if e else 'AUTO'


def costo(valor, modo=None):
    d = _dec(valor)
    if d is None:
        return '' if valor is None else valor
    modo = modo or decimales_costo()
    cifras = 4 if modo == '4' or (modo == 'AUTO' and d != 0 and abs(d) < 1) else 2
    return f'{d:,.{cifras}f}'


def celda(valor, encabezado='', unidad='', modo=None):
    """Celda de un reporte: según el encabezado de la columna (costo, cantidad o importe)."""
    if isinstance(valor, bool) or not isinstance(valor, (Decimal, float, int)):
        return valor
    nombre = encabezado.lower()
    if isinstance(valor, int) and not any(p in nombre for p in ('cant', 'costo', 'valor', 's/', 'stock', 'saldo')):
        return valor  # días, número de movimientos, etc.
    if 'costo' in nombre or 'precio' in nombre or 'tarifa' in nombre:
        return costo(valor, modo)
    if 'cant' in nombre or 'stock' in nombre or 'saldo' in nombre or 'entrada' in nombre or 'salida' in nombre:
        return cantidad(valor, unidad)
    return importe(valor)
