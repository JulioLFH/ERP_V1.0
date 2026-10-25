"""Códigos de barras: Code 128 (juego B) en SVG para las etiquetas y EAN-13 interno para los productos sin código.
Cualquier lector de códigos de barras USB o del celular lee Code 128; el EAN-13 interno usa el prefijo 2 (de uso
interno, no se confunde con los códigos GS1 de los fabricantes)."""

# anchos de barras y espacios de cada símbolo (0-105) y la parada (106)
PATRONES = (
    '212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312', '132212', '221213',
    '221312', '231212', '112232', '122132', '122231', '113222', '123122', '123221', '223211', '221132',
    '221231', '213212', '223112', '312131', '311222', '321122', '321221', '312212', '322112', '322211',
    '212123', '212321', '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313',
    '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121', '313121', '211331',
    '231131', '213113', '213311', '213131', '311123', '311321', '331121', '312113', '312311', '332111',
    '314111', '221411', '431111', '111224', '111422', '121124', '121421', '141122', '141221', '112214',
    '112412', '122114', '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111',
    '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112', '421211', '212141',
    '214121', '412121', '111143', '111341', '131141', '114113', '114311', '411113', '411311', '113141',
    '114131', '311141', '411131', '211412', '211214', '211232', '2331112',
)
INICIO_B, PARADA = 104, 106


class ErrorBarras(ValueError):
    pass


def code128(texto):
    """Lista de anchos (barra, espacio, barra…) del texto en Code 128 B con su dígito de control."""
    if not texto:
        raise ErrorBarras('Código vacío.')
    valores = []
    for c in texto:
        if not 32 <= ord(c) <= 126:
            raise ErrorBarras(f'El carácter «{c}» no se puede imprimir en el código de barras.')
        valores.append(ord(c) - 32)
    control = (INICIO_B + sum(i * v for i, v in enumerate(valores, 1))) % 103
    simbolos = [INICIO_B, *valores, control, PARADA]
    return [int(a) for s in simbolos for a in PATRONES[s]]


def svg(texto, alto=50, modulo=2, con_texto=True):
    """SVG del código de barras (con 10 módulos de margen a cada lado)."""
    from django.utils.html import escape
    anchos = code128(texto)
    x, barras = 10 * modulo, []
    for i, ancho in enumerate(anchos):
        if i % 2 == 0:  # posiciones pares: barra; impares: espacio
            barras.append(f'<rect x="{x}" y="0" width="{ancho * modulo}" height="{alto}"/>')
        x += ancho * modulo
    total = x + 10 * modulo
    alto_total = alto + (14 if con_texto else 0)
    leyenda = (f'<text x="{total / 2}" y="{alto + 12}" font-family="monospace" font-size="12" '
               f'text-anchor="middle">{escape(texto)}</text>') if con_texto else ''
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {alto_total}" width="{total}" '
            f'height="{alto_total}" role="img" aria-label="Código de barras {escape(texto)}">'
            f'<rect width="{total}" height="{alto_total}" fill="#fff"/><g fill="#000">{"".join(barras)}</g>'
            f'{leyenda}</svg>')


def digito_ean13(doce):
    suma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(doce))
    return str((10 - suma % 10) % 10)


def ean13_valido(codigo):
    return len(codigo) == 13 and codigo.isdigit() and digito_ean13(codigo[:12]) == codigo[12]


def ean13_interno(producto_id):
    """EAN-13 de uso interno para el producto: 2 + 11 dígitos del id + dígito de control."""
    doce = f'2{producto_id:011d}'
    if len(doce) != 12:
        raise ErrorBarras('Id de producto fuera de rango para un EAN-13 interno.')
    return doce + digito_ean13(doce)
