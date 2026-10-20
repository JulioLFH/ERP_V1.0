"""Importe en letras para los comprobantes (leyenda 1000 de SUNAT): «SON CIENTO DIECIOCHO CON 00/100 SOLES»."""
from decimal import ROUND_HALF_UP, Decimal

UNIDADES = ['', 'UNO', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE', 'OCHO', 'NUEVE', 'DIEZ', 'ONCE', 'DOCE',
            'TRECE', 'CATORCE', 'QUINCE', 'DIECISEIS', 'DIECISIETE', 'DIECIOCHO', 'DIECINUEVE', 'VEINTE',
            'VEINTIUNO', 'VEINTIDOS', 'VEINTITRES', 'VEINTICUATRO', 'VEINTICINCO', 'VEINTISEIS', 'VEINTISIETE',
            'VEINTIOCHO', 'VEINTINUEVE']
DECENAS = ['', '', '', 'TREINTA', 'CUARENTA', 'CINCUENTA', 'SESENTA', 'SETENTA', 'OCHENTA', 'NOVENTA']
CENTENAS = ['', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS', 'QUINIENTOS', 'SEISCIENTOS', 'SETECIENTOS',
            'OCHOCIENTOS', 'NOVECIENTOS']
MONEDAS = {'PEN': 'SOLES', 'USD': 'DÓLARES AMERICANOS'}


def _menor_mil(n):
    if n == 0:
        return ''
    if n == 100:
        return 'CIEN'
    c, resto = divmod(n, 100)
    partes = [CENTENAS[c]] if c else []
    if resto < 30:
        partes.append(UNIDADES[resto])
    else:
        d, u = divmod(resto, 10)
        partes.append(DECENAS[d] + (f' Y {UNIDADES[u]}' if u else ''))
    return ' '.join(p for p in partes if p)


def entero_en_letras(n):
    if n == 0:
        return 'CERO'
    millones, resto = divmod(n, 1_000_000)
    miles, unidades = divmod(resto, 1000)
    partes = []
    if millones:
        partes.append('UN MILLON' if millones == 1 else f'{entero_en_letras(millones)} MILLONES')
    if miles:
        partes.append('MIL' if miles == 1 else f'{_menor_mil(miles)} MIL')
    if unidades:
        partes.append(_menor_mil(unidades))
    return ' '.join(partes).replace('UNO MIL', 'UN MIL').replace('VEINTIUNO MIL', 'VEINTIUN MIL')


def monto_en_letras(importe, moneda='PEN'):
    importe = Decimal(importe).quantize(Decimal('0.01'), ROUND_HALF_UP)
    entero = int(importe)
    centimos = int((importe - entero) * 100)
    return f'SON {entero_en_letras(entero)} CON {centimos:02d}/100 {MONEDAS.get(moneda, moneda)}'
