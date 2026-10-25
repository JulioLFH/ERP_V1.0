"""Firma electrónica (v1.84, CAL-03): para liberar un lote, cerrar un OOS o aprobar una especificación o un documento
el usuario vuelve a escribir su contraseña; queda su nombre, la fecha y hora y el significado de la firma."""


class ErrorFirma(Exception):
    pass


def firmar(usuario, clave, significado, obj, ip=''):
    """Verifica la contraseña del usuario en el momento y registra la firma. Devuelve la FirmaElectronica."""
    from .models import FirmaElectronica
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        raise ErrorFirma('Ingrese al sistema para firmar.')
    if not usuario.has_usable_password():
        raise ErrorFirma('Su usuario ingresa con Microsoft o Google y no tiene contraseña de Ceiba: pida al '
                         'administrador una contraseña para firmar electrónicamente.')
    if not clave or not usuario.check_password(clave):
        raise ErrorFirma('Firma electrónica: la contraseña no es correcta.')
    return FirmaElectronica.objects.create(
        usuario=usuario, nombre=(usuario.get_full_name() or usuario.username)[:150], significado=significado[:120],
        modelo=obj._meta.label, objeto_id=str(obj.pk), objeto=str(obj)[:200], ip=(ip or '')[:45])


def de(obj):
    from .models import FirmaElectronica
    return FirmaElectronica.objects.filter(modelo=obj._meta.label, objeto_id=str(obj.pk))


def ip_de(request):
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', ''))
