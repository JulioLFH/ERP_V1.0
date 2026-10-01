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
def es_numero(valor):
    return isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool)


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Mantiene los parámetros GET actuales reemplazando los indicados."""
    params = context['request'].GET.copy()
    for k, v in kwargs.items():
        params[k] = v
    return params.urlencode()
