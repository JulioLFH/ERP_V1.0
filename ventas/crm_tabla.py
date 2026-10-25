"""Tablas genéricas del CRM: cada celda puede ser un valor, un dict {texto, url, clase} o {texto, post, campo, valor,
extra, confirmar} (botón), o una lista de ellos. Para la pantalla se normaliza a una lista de dicts y para el Excel
se deja el texto."""
from decimal import Decimal

from django.shortcuts import render

from core.utils import excel_response


def _texto(v):
    if v is None:
        return ''
    if isinstance(v, Decimal):
        return f'{v:,.2f}'
    if isinstance(v, float):
        return f'{v:,.2f}'
    return str(v)


def _celda(v):
    if isinstance(v, (list, tuple)):
        return [x for c in v if c for x in _celda(c)]
    if isinstance(v, dict):
        return [{**v, 'texto': _texto(v.get('texto'))}]
    return [{'texto': _texto(v), 'clase': 'num' if isinstance(v, (int, Decimal, float)) and not isinstance(v, bool)
             else ''}]


def _plano(v):
    if isinstance(v, (list, tuple)):
        return ' '.join(_plano(x) for x in v if x and not (isinstance(x, dict) and 'post' in x))
    if isinstance(v, dict):
        return '' if 'post' in v else v.get('texto', '')  # botón (post vacío = la misma pantalla)
    return v


def responder(request, titulo, secciones, por_seccion=False, **extra):
    """Página con una o varias tablas; ?formato=excel (&seccion=N) descarga la tabla."""
    if request.GET.get('formato') == 'excel':
        i = int(request.GET.get('seccion') or 0) if por_seccion else 0
        s = secciones[min(i, len(secciones) - 1)]
        nombre = titulo if not s.get('titulo') else f'{titulo} – {s["titulo"]}'
        return excel_response(''.join(ch for ch in titulo if ch.isalnum())[:30] or 'CRM', nombre, s['columnas'],
                              [[_plano(c) for c in f] for f in s['filas']])
    pantalla = [{**s, 'filas': [[_celda(c) for c in f] for f in s['filas']]} for s in secciones]
    return render(request, 'ventas/crm_tabla.html', {'titulo': titulo, 'secciones': pantalla, 'excel': True,
                                                     'por_seccion': por_seccion, **extra})
