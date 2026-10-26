from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(valor):
    try:
        return f'{Decimal(valor or 0):,.2f}'
    except (InvalidOperation, TypeError, ValueError):
        return valor


@register.filter
def cant(valor, unidad=''):
    """Cantidad según la unidad: sin decimales en unidades contables si es entera; si no, 2 (core/formato.py)."""
    from core.formato import cantidad
    return cantidad(valor, unidad)


@register.filter
def campo_etiqueta(form, nombre):
    """Etiqueta de un campo del formulario (para listar sus errores)."""
    campo = getattr(form, 'fields', {}).get(nombre)
    return campo.label if campo is not None and campo.label else nombre


@register.filter
def errores_lineas(formset):
    """«Línea 2 · Cantidad: …» por cada error de las líneas de un detalle (COM-20, INV-32: antes solo decía «Revise
    las líneas» sin decir cuál ni por qué)."""
    if not formset or not getattr(formset, 'is_bound', False):
        return []
    salida = []
    for n, f in enumerate(formset.forms, 1):
        for nombre, errores in (f.errors or {}).items():
            etiqueta = '' if nombre == '__all__' else f'{campo_etiqueta(f, nombre)}: '
            salida += [f'Línea {n} · {etiqueta}{e}' for e in errores]
    return salida + list(formset.non_form_errors())


@register.filter
def editable(valor):
    """Número para una casilla editable sin ceros de más (PLAT-06): 0.927000 → 0.927."""
    from core.formato import editable as fmt
    if isinstance(valor, Decimal):
        return fmt(valor)
    return '' if valor is None else str(valor)  # ids y textos quedan igual (como stringformat:'s')


@register.filter
def costo(valor):
    """Costo unitario con los decimales de Ajustes › Empresa (2, 4 o automático)."""
    from core.formato import costo as fmt
    return fmt(valor)


@register.filter
def sumar(valor, otro):
    """Suma exacta de importes (el filtro add de Django convierte a entero: 940.59 + 0 daba 940)."""
    try:
        return Decimal(valor or 0) + Decimal(otro or 0)
    except (InvalidOperation, TypeError, ValueError):
        return valor


@register.filter
def attr(obj, nombre):
    """Resuelve 'a.b', métodos y get_X_display para la lista genérica."""
    valor = obj
    for parte in nombre.split('.'):
        valor = getattr(valor, parte, '')
        if callable(valor):
            valor = valor()
    if isinstance(valor, bool):
        return 'Sí' if valor else 'No'
    if isinstance(valor, Decimal):
        return money(valor)
    return '' if valor is None else valor


@register.filter
def get_item(diccionario, clave):
    """Valor de un diccionario por su clave en la plantilla."""
    return diccionario.get(clave, '') if hasattr(diccionario, 'get') else ''


@register.filter
def es_numero(valor):
    return isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool)


@register.filter
def campos(form, nombres):
    """Campos del formulario indicados por nombre separados por coma (omite los que no existen)."""
    return [form[n.strip()] for n in nombres.split(',') if n.strip() in form.fields]


@register.filter
def sustentos(obj):
    from core.sustentos import de
    return list(de(obj))


@register.filter
def ct_id(obj):
    from django.contrib.contenttypes.models import ContentType
    return ContentType.objects.get_for_model(obj).pk


@register.filter
def bitacora(obj):
    """Últimos 30 registros de auditoría del objeto."""
    from core.models import Bitacora
    if obj is None or not getattr(obj, 'pk', None):
        return []
    return list(Bitacora.objects.filter(modelo=obj._meta.label, objeto_id=str(obj.pk)).select_related('usuario')[:30])


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Mantiene los parámetros GET actuales reemplazando los indicados."""
    params = context['request'].GET.copy()
    for k, v in kwargs.items():
        params[k] = v
    return params.urlencode()
