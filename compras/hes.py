"""Conformidad de servicio (HES): registro por el área con el informe firmado del proveedor y el documento de trabajo
con imágenes, liberación por niveles con el motor de aprobación (documento HES: jefe del solicitante, responsable
del centro de costo, monto, suplencias y escalamiento) y control de lo pendiente de cada línea de la orden."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core import aprobaciones
from core.models import r2

from .models import AdjuntoHES, ConformidadServicio

D0 = Decimal('0')
MAX_ADJUNTO = 8 * 1024 * 1024
TIPOS_PERMITIDOS = ('application/pdf', 'image/jpeg', 'image/png', 'image/webp', 'image/heic')


class ErrorHES(Exception):
    pass


def conformado(oc_item, excluir=None):
    """Cantidad de la línea ya conformada (HES no rechazadas ni anuladas)."""
    qs = ConformidadServicio.objects.filter(oc_item=oc_item).exclude(estado__in=('RECHAZADA', 'ANULADA'))
    if excluir is not None and excluir.pk:
        qs = qs.exclude(pk=excluir.pk)
    return qs.aggregate(s=Sum('cantidad'))['s'] or D0


def pendiente_linea(oc_item, excluir=None):
    return max(oc_item.cantidad - conformado(oc_item, excluir), D0)


@transaction.atomic
def registrar(hes, usuario, archivos=()):
    """Crea la HES (borrador) con sus adjuntos. archivos: [(tipo, UploadedFile)]."""
    from .solped import _numero
    oc = hes.orden_compra
    if oc.estado not in ('APROBADO', 'ATENDIDO'):
        raise ErrorHES('Solo se conforman servicios de órdenes aprobadas.')
    if hes.oc_item_id is None:
        items = list(oc.items.all())
        if len(items) != 1:
            raise ErrorHES('Indique la línea de la orden que se conforma.')
        hes.oc_item = items[0]
    if hes.oc_item.documento_id != oc.pk:
        raise ErrorHES('La línea no es de esta orden.')
    if not hes.cantidad:
        hes.cantidad = r2(hes.oc_item.cantidad * hes.porcentaje / 100)
    else:
        hes.porcentaje = min(r2(hes.cantidad * 100 / hes.oc_item.cantidad), Decimal('100')) if hes.oc_item.cantidad \
            else Decimal('100')
    pendiente = pendiente_linea(hes.oc_item, hes)
    if hes.cantidad <= 0 or hes.cantidad > pendiente:
        raise ErrorHES(f'La cantidad conforme ({hes.cantidad:,.2f}) supera lo pendiente de la línea '
                       f'({pendiente:,.2f}).')
    if hes.periodo_desde and hes.periodo_hasta and hes.periodo_hasta < hes.periodo_desde:
        raise ErrorHES('El periodo del servicio está invertido.')
    hes.numero = _numero('HES', 'HES')
    hes.conforme_por = usuario
    hes.estado = 'BORRADOR'
    hes.save()
    for tipo, archivo in archivos:
        adjuntar(hes, tipo, archivo, usuario)
    return hes


def adjuntar(hes, tipo, archivo, usuario):
    if hes.estado not in ('BORRADOR', 'OBSERVADA'):
        raise ErrorHES('Solo se adjunta a una HES en borrador u observada.')
    if tipo not in dict(AdjuntoHES.TIPOS):
        raise ErrorHES('Tipo de adjunto no válido.')
    if archivo.size > MAX_ADJUNTO:
        raise ErrorHES(f'{archivo.name}: supera los 8 MB.')
    contenido = archivo.read()
    tipo_contenido = _tipo_contenido(contenido, archivo.name)
    if tipo_contenido not in TIPOS_PERMITIDOS:
        raise ErrorHES(f'{archivo.name}: solo PDF o imágenes (JPG, PNG, WEBP, HEIC).')
    if tipo == 'INFORME' and tipo_contenido != 'application/pdf':
        raise ErrorHES('El informe firmado del proveedor debe ser un PDF.')
    return AdjuntoHES.objects.create(hes=hes, tipo=tipo, nombre=archivo.name[:150], tipo_contenido=tipo_contenido,
                                     contenido=contenido, tamano=len(contenido), subido_por=usuario)


def _tipo_contenido(contenido, nombre):
    """Por la firma del archivo (no por la extensión que manda el navegador)."""
    if contenido[:5] == b'%PDF-':
        return 'application/pdf'
    if contenido[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if contenido[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if contenido[:4] == b'RIFF' and contenido[8:12] == b'WEBP':
        return 'image/webp'
    if contenido[4:12] in (b'ftypheic', b'ftypheix', b'ftypmif1'):
        return 'image/heic'
    return 'application/octet-stream'


@transaction.atomic
def enviar(hes, usuario):
    """A liberación: exige los dos adjuntos y calcula los niveles con las reglas del documento HES."""
    if hes.estado not in ('BORRADOR', 'OBSERVADA'):
        raise ErrorHES('La HES ya fue enviada a liberación.')
    if not hes.tiene_adjuntos:
        raise ErrorHES('Adjunte el informe del proveedor firmado (PDF) y el documento de trabajo con imágenes '
                       'antes de enviarla a liberación.')
    try:
        hes.niveles = aprobaciones.calcular_niveles('HES', hes.conforme_por, hes.valor_pen,
                                                    hes.orden_compra.centro_costo, 'SERVICIO')
    except aprobaciones.ErrorAprobacion as exc:
        raise ErrorHES(str(exc)) from exc
    hes.nivel_aprobado, hes.estado, hes.motivo = 0, 'POR_LIBERAR', ''
    hes.save()
    aprobaciones.registrar('HES', hes, usuario, 'ENVIADA')
    return hes


def puede_liberar(user, hes):
    return hes.estado == 'POR_LIBERAR' and aprobaciones.puede_aprobar(
        user, 'HES', hes.niveles, hes.nivel_aprobado, hes.conforme_por_id)


@transaction.atomic
def resolver(hes, usuario, accion, motivo=''):
    """liberar (el nivel que toca; la última libera la HES), observar (vuelve al registrador) o rechazar."""
    if not puede_liberar(usuario, hes):
        n = aprobaciones.pendiente(hes.niveles, hes.nivel_aprobado)
        raise ErrorHES(f'La HES la libera ahora el nivel {n["nivel"]}: {n["nombre"]}.' if n else
                       'No le corresponde liberar esta HES.')
    motivo = (motivo or '').strip()
    if accion in ('observar', 'rechazar') and len(motivo) < 5:
        raise ErrorHES('Indique el motivo (mínimo 5 caracteres).')
    if accion == 'liberar':
        n = aprobaciones.pendiente(hes.niveles, hes.nivel_aprobado)
        if n is not None:
            hes.nivel_aprobado = n['nivel']
            aprobaciones.registrar('HES', hes, usuario, 'APROBADA', motivo, n['nivel'])
            if aprobaciones.pendiente(hes.niveles, hes.nivel_aprobado) is not None:
                aprobaciones.avanzar(hes.niveles, hes.nivel_aprobado)
                hes.save(update_fields=['niveles', 'nivel_aprobado'])
                return hes
        else:
            aprobaciones.registrar('HES', hes, usuario, 'APROBADA', motivo)
        hes.estado, hes.liberado_por, hes.liberado_en = 'LIBERADA', usuario, timezone.now()
    elif accion == 'observar':
        hes.estado, hes.motivo = 'OBSERVADA', motivo[:300]
        aprobaciones.registrar('HES', hes, usuario, 'OBSERVADA', motivo)
    elif accion == 'rechazar':
        hes.estado, hes.motivo = 'RECHAZADA', motivo[:300]
        aprobaciones.registrar('HES', hes, usuario, 'RECHAZADA', motivo)
    else:
        raise ErrorHES('Acción no válida.')
    hes.save()
    return hes


def anular(hes, usuario, motivo):
    if hes.estado == 'ANULADA':
        raise ErrorHES('La HES ya está anulada.')
    if hes.lineas_factura.exclude(factura__estado='RECHAZADA').exists():
        raise ErrorHES('La HES ya se facturó: anule o rechace primero la factura.')
    if len((motivo or '').strip()) < 5:
        raise ErrorHES('Indique el motivo (mínimo 5 caracteres).')
    hes.estado, hes.motivo = 'ANULADA', motivo.strip()[:300]
    hes.save(update_fields=['estado', 'motivo'])
    aprobaciones.registrar('HES', hes, usuario, 'ANULADA', motivo)
    return hes


def facturado(hes):
    """Cantidad de la HES ya facturada en el portal (facturas no rechazadas)."""
    return (hes.lineas_factura.exclude(factura__estado='RECHAZADA').aggregate(s=Sum('cantidad'))['s'] or D0)


def visibles(user, vista):
    """HES de la vista: mias (registradas por el usuario), liberar (le toca liberar), area (de los centros de costo
    de los que es responsable) y todas."""
    qs = ConformidadServicio.objects.select_related('orden_compra__tercero', 'orden_compra__centro_costo', 'oc_item',
                                                    'conforme_por')
    if vista == 'mias':
        return qs.filter(conforme_por=user)
    if vista == 'liberar':
        return [h for h in qs.filter(estado='POR_LIBERAR') if puede_liberar(user, h)]
    if vista == 'area':
        from contabilidad.models import CentroCosto
        ids = set()
        for c in CentroCosto.objects.filter(responsable_usuario=user):
            ids |= set(c.descendientes_ids())
        return qs.filter(orden_compra__centro_costo_id__in=ids)
    return qs
