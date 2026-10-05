"""Variantes de producto (talla, color, presentación...) e imagen del producto.

El producto plantilla agrupa las variantes y no se usa en documentos; cada combinación de atributos es un producto
normal (código, stock, kardex, precio y cuentas propios) que hereda los datos de la plantilla al crearse.
"""
import itertools
import re
import unicodedata

from django.db import transaction

from .models import Producto

MAX_VARIANTES = 200
MAX_IMAGEN = 2 * 1024 * 1024
FIRMAS_IMAGEN = [(b'\x89PNG\r\n\x1a\n', 'image/png'), (b'\xff\xd8\xff', 'image/jpeg'), (b'GIF8', 'image/gif')]
NO_COPIAR = {'id', 'codigo', 'nombre', 'codigo_barras', 'stock', 'costo_promedio', 'es_plantilla', 'plantilla',
             'atributos'}


class ErrorVariantes(Exception):
    pass


def leer_atributos(texto):
    """'Talla: S, M, L' (una línea por atributo) -> {'Talla': ['S', 'M', 'L']} conservando el orden."""
    atributos = {}
    for n, linea in enumerate((texto or '').splitlines(), 1):
        if not linea.strip():
            continue
        if ':' not in linea:
            raise ErrorVariantes(f'Línea {n}: use el formato «Atributo: valor1, valor2».')
        nombre, valores = linea.split(':', 1)
        nombre = nombre.strip()[:30]
        lista = list(dict.fromkeys(v.strip()[:30] for v in valores.split(',') if v.strip()))
        if not nombre or not lista:
            raise ErrorVariantes(f'Línea {n}: indique el atributo y al menos un valor.')
        if nombre in atributos:
            raise ErrorVariantes(f'El atributo «{nombre}» está repetido.')
        atributos[nombre] = lista
    if not atributos:
        raise ErrorVariantes('Indique al menos un atributo con sus valores.')
    return atributos


def _sufijo(valor):
    texto = unicodedata.normalize('NFKD', valor).encode('ascii', 'ignore').decode().upper()
    return re.sub(r'[^A-Z0-9]', '', texto)[:4] or 'X'


def puede_ser_plantilla(producto):
    """Motivo por el que el producto no puede agrupar variantes ('' si puede)."""
    if producto.plantilla_id:
        return 'Es una variante: las variantes se generan desde su plantilla.'
    if producto.stock:
        return 'Tiene stock: cree un producto nuevo como plantilla (el stock va en cada variante).'
    from .models import Kardex
    if Kardex.objects.filter(producto=producto).exists():
        return 'Ya tiene movimientos de almacén: cree un producto nuevo como plantilla.'
    return ''


@transaction.atomic
def generar(plantilla, atributos):
    """Crea las combinaciones que falten. Devuelve la lista de variantes nuevas."""
    motivo = puede_ser_plantilla(plantilla)
    if motivo:
        raise ErrorVariantes(motivo)
    nombres = list(atributos)
    combinaciones = list(itertools.product(*(atributos[n] for n in nombres)))
    existentes = {tuple(sorted(v.atributos.items())) for v in plantilla.variantes.all()}
    if len(existentes) + len(combinaciones) > MAX_VARIANTES:
        raise ErrorVariantes(f'Serían más de {MAX_VARIANTES} variantes: reduzca los valores.')
    if not plantilla.es_plantilla:
        plantilla.es_plantilla = True
        plantilla.save(update_fields=['es_plantilla'])
    base = {f.attname: getattr(plantilla, f.attname) for f in Producto._meta.concrete_fields
            if f.name not in NO_COPIAR}
    nuevas = []
    for combo in combinaciones:
        attrs = dict(zip(nombres, combo))
        if tuple(sorted(attrs.items())) in existentes:
            continue
        codigo = f'{plantilla.codigo}-' + '-'.join(_sufijo(v) for v in combo)
        sufijo, n = codigo, 1
        while Producto.objects.filter(codigo=codigo).exists():
            n += 1
            codigo = f'{sufijo}{n}'
        v = Producto(**base, codigo=codigo[:30], nombre=f'{plantilla.nombre} ({", ".join(combo)})'[:200],
                     plantilla=plantilla, atributos=attrs)
        v.save()
        nuevas.append(v)
    return nuevas


def tipo_imagen(datos):
    for firma, tipo in FIRMAS_IMAGEN:
        if datos.startswith(firma):
            return tipo
    if datos[:4] == b'RIFF' and datos[8:12] == b'WEBP':
        return 'image/webp'
    return None


def guardar_imagen(producto, archivo, usuario=None):
    """Valida (PNG, JPG, GIF o WEBP de hasta 2 MB por su contenido, no por la extensión) y asigna la imagen."""
    from .sustentos import guardar_archivo
    datos = archivo.read()
    if len(datos) > MAX_IMAGEN:
        raise ErrorVariantes('La imagen supera 2 MB.')
    tipo = tipo_imagen(datos)
    if tipo is None:
        raise ErrorVariantes('La imagen debe ser PNG, JPG, GIF o WEBP.')
    producto.imagen = guardar_archivo(None, usuario, datos=datos, nombre=archivo.name, tipo=tipo)
    producto.save(update_fields=['imagen'])
