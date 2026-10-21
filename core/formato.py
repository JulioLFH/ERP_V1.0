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


def cantidad(valor, unidad=''):
    d = _dec(valor)
    if d is None:
        return '' if valor is None else valor
    if (unidad or '').upper() in CONTABLES and d == d.to_integral_value():
        return f'{d:,.0f}'
    return f'{d:,.2f}'


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
