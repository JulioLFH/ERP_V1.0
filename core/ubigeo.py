"""Ubigeos INEI (25 departamentos, 196 provincias, 1874 distritos).

Fuente: tabla INEI publicada en OCA/l10n-peru (l10n_pe_toponym), convertida a core/data/ubigeos.csv.
"""
import csv
from functools import lru_cache
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent / 'data' / 'ubigeos.csv'


@lru_cache(maxsize=1)
def tabla():
    """{ubigeo: (departamento, provincia, distrito)}"""
    with open(ARCHIVO, encoding='utf-8') as f:
        return {fila['ubigeo']: (fila['departamento'], fila['provincia'], fila['distrito'])
                for fila in csv.DictReader(f)}


@lru_cache(maxsize=1)
def arbol():
    """Estructura para los selectores en cascada: [[cod_dep, dep, [[cod_prov, prov, [[ubigeo, distrito]]]]]]"""
    deps = {}
    for codigo, (dep, prov, dist) in sorted(tabla().items()):
        d = deps.setdefault(codigo[:2], [codigo[:2], dep, {}])
        p = d[2].setdefault(codigo[:4], [codigo[:4], prov, []])
        p[2].append([codigo, dist])
    return [[d[0], d[1], list(d[2].values())] for d in deps.values()]


def existe(codigo):
    return codigo in tabla()


def descripcion(codigo):
    """'San Isidro - Lima - Lima' o '' si el código no existe."""
    fila = tabla().get(codigo or '')
    return f'{fila[2]} - {fila[1]} - {fila[0]}' if fila else ''
