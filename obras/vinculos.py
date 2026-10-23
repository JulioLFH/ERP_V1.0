"""Vínculo de los recursos de obra con el resto de Ceiba: material → producto (para requerirlo, comprarlo, recibirlo en
el almacén de obra y consumirlo), subcontrato → servicio (conformidad de servicio), mano de obra → categoría de
construcción civil, equipo → equipo propio. El producto guarda el código del S10 como código alterno para no duplicarse
en la siguiente importación."""
import re
import unicodedata

from django.db import transaction

# unidad del S10 → unidad de medida de SUNAT (catálogo 03) del producto
UNIDADES = {'und': 'NIU', 'u': 'NIU', 'un': 'NIU', 'unid': 'NIU', 'pza': 'NIU', 'pieza': 'NIU', 'pln': 'NIU',
            'plancha': 'NIU', 'jgo': 'NIU', 'kg': 'KGM', 'kgs': 'KGM', 'kilo': 'KGM', 'l': 'LTR', 'lt': 'LTR',
            'lts': 'LTR', 'litro': 'LTR', 'gal': 'GLL', 'gln': 'GLL', 'galon': 'GLL', 'm': 'MTR', 'ml': 'MTR',
            'mt': 'MTR', 'm2': 'MTK', 'm²': 'MTK', 'm3': 'MTQ', 'm³': 'MTQ', 'p2': 'FTK', 'pie2': 'FTK', 'bls': 'BG',
            'bol': 'BG', 'bolsa': 'BG', 'rll': 'RO', 'rollo': 'RO', 'mll': 'MIL', 'millar': 'MIL', 'cja': 'BX',
            'caja': 'BX', 'pqt': 'PK', 'paquete': 'PK', 't': 'TNE', 'tn': 'TNE', 'ton': 'TNE', 'hh': 'HUR',
            'hm': 'HUR', 'h': 'HUR', 'hr': 'HUR', 'dia': 'DAY', 'glb': 'ZZ', 'est': 'ZZ', 'mes': 'ZZ'}
CATEGORIAS = [('CAPATAZ', 'capataz'), ('OPERARIO', 'operario'), ('OFICIAL', 'oficial'), ('PEON', 'peon'),
              ('OPERADOR', 'operador')]


def _norm(texto):
    texto = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', texto).strip(' .')


def unidad_sunat(unidad):
    return UNIDADES.get(_norm(unidad).replace(' ', ''), 'NIU')


def buscar_producto(recurso):
    """Producto que corresponde al recurso: por código alterno (el del S10), por código o por nombre."""
    from core.models import Producto
    if recurso.codigo:
        p = (Producto.objects.filter(codigo_alterno=recurso.codigo).first()
             or Producto.objects.filter(codigo=recurso.codigo).first())
        if p:
            return p, 'código'
    p = Producto.objects.filter(nombre__iexact=recurso.nombre.strip()).first()
    return (p, 'nombre') if p else (None, '')


def _categoria(recurso):
    nombre = _norm(recurso.nombre)
    return next((c for c, palabra in CATEGORIAS if palabra in nombre), '')


def proponer(recursos):
    """[{recurso, accion, destino, motivo}] de lo que haría «vincular»: enlazar, crear o nada que hacer."""
    from produccion.models import Equipo
    filas = []
    for r in recursos:
        if r.es_porcentaje_mo or r.es_subanalisis:  # el sub-análisis no se compra: se compran sus componentes
            continue
        if r.tipo in ('MAT', 'SUB') and not r.producto_id:
            p, por = buscar_producto(r)
            if p:
                filas.append({'recurso': r, 'accion': 'ENLAZAR', 'destino': p, 'motivo': f'mismo {por}'})
            else:
                clase = 'Servicio' if r.tipo == 'SUB' else 'Suministros y materiales'
                filas.append({'recurso': r, 'accion': 'CREAR', 'destino': None,
                              'motivo': f'producto nuevo ({clase}, {unidad_sunat(r.unidad)})'})
        elif r.tipo == 'MO' and not r.categoria_mo and _categoria(r):
            filas.append({'recurso': r, 'accion': 'CATEGORIA', 'destino': None,
                          'motivo': f'categoría {dict(r.CATEGORIAS)[_categoria(r)]}'})
        elif r.tipo == 'EQ' and not r.equipo_id:
            e = Equipo.objects.filter(nombre__iexact=r.nombre.strip()).first()
            if e:
                filas.append({'recurso': r, 'accion': 'EQUIPO', 'destino': e, 'motivo': 'equipo propio con el mismo nombre'})
    return filas


@transaction.atomic
def vincular(recursos, crear=True):
    """Aplica la propuesta. Devuelve {'enlazados', 'creados', 'categorias', 'equipos'}."""
    from core.models import Producto
    cuenta = {'enlazados': 0, 'creados': 0, 'categorias': 0, 'equipos': 0}
    for f in proponer(recursos):
        r = f['recurso']
        if f['accion'] == 'ENLAZAR':
            r.producto = f['destino']
            if r.codigo and not f['destino'].codigo_alterno:
                Producto.objects.filter(pk=f['destino'].pk).update(codigo_alterno=r.codigo[:30])
            r.save(update_fields=['producto'])
            cuenta['enlazados'] += 1
        elif f['accion'] == 'CREAR' and crear:
            r.producto = Producto.objects.create(
                nombre=r.nombre[:200], codigo_alterno=r.codigo[:30], clase='SERVICIO' if r.tipo == 'SUB' else 'SUMINISTRO',
                unidad='ZZ' if r.tipo == 'SUB' else unidad_sunat(r.unidad), precio_compra=r.precio,
                puede_venderse=False)
            r.save(update_fields=['producto'])
            cuenta['creados'] += 1
        elif f['accion'] == 'CATEGORIA':
            r.categoria_mo = _categoria(r)
            r.save(update_fields=['categoria_mo'])
            cuenta['categorias'] += 1
        elif f['accion'] == 'EQUIPO':
            r.equipo = f['destino']
            r.save(update_fields=['equipo'])
            cuenta['equipos'] += 1
    return cuenta


def recursos_de(presupuesto):
    """Recursos que usa el presupuesto (y los componentes de sus sub-análisis)."""
    from .models import ComponenteRecurso, Recurso, RecursoAPU
    ids = set(RecursoAPU.objects.filter(partida__presupuesto=presupuesto).values_list('recurso_id', flat=True))
    pendientes = set(ids)
    while pendientes:
        nuevos = set(ComponenteRecurso.objects.filter(padre_id__in=pendientes).values_list('recurso_id', flat=True)) - ids
        ids |= nuevos
        pendientes = nuevos
    return Recurso.objects.filter(pk__in=ids)
