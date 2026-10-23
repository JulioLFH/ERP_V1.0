"""Tolerancias de la factura del proveedor frente a la orden de compra (n.º 47): en importe (S/) y en %, de la
empresa (Ajustes › Empresa) o propias del proveedor (su ficha en Contactos), y el bloqueo de pago cuando un precio
facturado queda fuera de la tolerancia (lo libera Compras con su motivo)."""
from decimal import Decimal

from django.utils import timezone

from core.models import Empresa, r2

D0 = Decimal('0')


def _valores(proveedor, campo):
    """(importe, %) del proveedor si los tiene; si no, los de la empresa."""
    e = Empresa.actual()
    importe = getattr(proveedor, f'tolerancia_{campo}', None) if proveedor is not None else None
    pct = getattr(proveedor, f'tolerancia_{campo}_pct', None) if proveedor is not None else None
    if importe is None and pct is None:
        return getattr(e, f'tolerancia_{campo}'), getattr(e, f'tolerancia_{campo}_pct')
    return (importe if importe is not None else getattr(e, f'tolerancia_{campo}'),
            pct if pct is not None else getattr(e, f'tolerancia_{campo}_pct'))


def permitida(proveedor, campo, base):
    """Diferencia permitida sobre la base: el importe y, si hay %, el menor de ambos (el más estricto)."""
    importe, pct = _valores(proveedor, campo)
    importe = importe or D0
    if pct:
        por_pct = r2(abs(base) * pct / 100) if campo == 'total' else (abs(base) * pct / 100).quantize(Decimal('0.0001'))
        return min(importe, por_pct) if importe else por_pct
    return importe


def texto(proveedor, campo):
    importe, pct = _valores(proveedor, campo)
    partes = [f'±{importe:,.4f}' if campo == 'precio' else f'±{importe:,.2f}'] if importe else []
    if pct:
        partes.append(f'±{pct:,.2f}%')
    return ' y '.join(partes) or '0'


def bloquear_pago(compra, motivo):
    compra.pago_bloqueado, compra.motivo_bloqueo_pago = True, motivo[:300]
    compra.save(update_fields=['pago_bloqueado', 'motivo_bloqueo_pago'])


def liberar_pago(compra, usuario, comentario):
    if not compra.pago_bloqueado:
        raise ValueError('El pago de esta factura no está bloqueado.')
    if len((comentario or '').strip()) < 10:
        raise ValueError('Indique por qué se libera el pago (mínimo 10 caracteres).')
    nombre = (usuario.get_full_name() or usuario.username) if usuario else ''
    compra.pago_bloqueado = False
    compra.motivo_bloqueo_pago = (f'Liberado por {nombre} el {timezone.localtime():%d/%m/%Y %H:%M}: '
                                  f'{comentario.strip()} (antes: {compra.motivo_bloqueo_pago})')[:300]
    compra.save(update_fields=['pago_bloqueado', 'motivo_bloqueo_pago'])
    from core.auditoria import registrar
    registrar('MODIFICAR', compra, {'pago_bloqueado': 'liberado'}, comentario.strip()[:200])  # acción de la bitácora
