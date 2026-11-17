"""«Agregar productos a compras de SUNAT» (v1.116.1): completa el detalle de las compras creadas desde la propuesta del
SIRE (que solo trae totales), comprobante por comprobante y con barra de avance.

Dos formas:
1. Desde los XML (descargados del portal SOL › Consulta de comprobantes de pago, o enviados por el proveedor): se suben
   todos de una vez; la pantalla los procesa de a uno. Se guardan los productos solo si CUADRAN con lo registrado
   (valor de las líneas = base + no gravado de la compra, y total del XML = total de la compra, ±0,10). Si no cuadra,
   se informa y la compra no se toca.
2. Sin XML: una línea por compra con la descripción del comprobante y su importe exacto (cuadra siempre). Para gastos
   donde no se necesita el detalle.
Los totales registrados nunca cambian (vienen de SUNAT) y no se mueve almacén.
"""
import secrets
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.comprobantes import normalizar_periodo
from proveedores.xml_ubl import ErrorXML, leer

from . import xml_masivo
from .models import Compra, CompraItem

TOLERANCIA = Decimal('0.10')
DURACION = 2 * 3600  # los XML subidos esperan en el caché mientras se procesan


def pendientes(periodo):
    """Compras del periodo creadas desde la propuesta SIRE (o importadas) que aún no tienen productos."""
    return (Compra.objects.filter(periodo=periodo, items__isnull=True).exclude(estado='ANULADO')
            .exclude(es_historico=True).select_related('tercero').order_by('fecha_emision', 'serie', 'numero'))


def _clave(token, i=None):
    return f'productos-sunat:{token}' + ('' if i is None else f':{i}')


def cuadra(compra, xml):
    """(cuadra, detalle) del XML frente a lo registrado: valor de las líneas y total."""
    valor_lineas = sum((l['valor'] or Decimal('0')) for l in xml['lineas'])
    base = (compra.base_imponible or 0) + (compra.no_gravado or 0)
    dif_total = abs((xml['total'] or 0) - compra.total)
    dif_base = abs(valor_lineas - base)
    ok = dif_total <= TOLERANCIA and dif_base <= TOLERANCIA
    detalle = (f'Líneas {valor_lineas:,.2f} vs base {base:,.2f} · total XML {xml["total"]:,.2f} vs '
               f'compra {compra.total:,.2f}')
    return ok, detalle


def _resumen_lineas(compra):
    return [{'descripcion': i.descripcion, 'cantidad': str(i.cantidad.normalize()),
             'precio': f'{i.precio_unitario:,.2f}', 'importe': f'{i.cantidad * i.precio_unitario:,.2f}',
             'producto': i.producto.codigo if i.producto_id else ''} for i in compra.items.order_by('pk')]


@login_required
def pagina(request):
    periodo = normalizar_periodo(request.GET.get('periodo', '')) or ''
    if not periodo:
        ultimo = Compra.objects.filter(items__isnull=True).exclude(estado='ANULADO').order_by('-periodo').first()
        periodo = ultimo.periodo if ultimo else ''
    qs = pendientes(periodo) if periodo else Compra.objects.none()
    una = request.GET.get('compra', '')
    if una.isdigit():  # v1.116.3: desde el botón de la factura de compra: solo esa compra
        qs = qs.filter(pk=una)
    lista = list(qs)
    return render(request, 'compras/productos_sunat.html', {'periodo': periodo, 'compras': lista, 'una': una.isdigit(),
                                                            'total': sum((c.total_pen for c in lista), Decimal('0'))})


@login_required
@require_POST
def subir(request):
    """Recibe los XML (sueltos o en ZIP), los deja en el caché y devuelve la lista para procesarlos de a uno."""
    token = secrets.token_urlsafe(12)
    lista = []
    for i, (nombre, datos) in enumerate(xml_masivo.archivos_xml(request.FILES.getlist('archivos'))):
        cache.set(_clave(token, i), (nombre, datos), DURACION)
        lista.append({'i': i, 'archivo': nombre})
    cache.set(_clave(token), len(lista), DURACION)
    return JsonResponse({'ok': True, 'token': token, 'archivos': lista})


@login_required
@require_POST
def procesar(request):
    """Procesa UN XML subido: busca su compra, comprueba que cuadre y guarda sus productos."""
    token, i = request.POST.get('token', ''), request.POST.get('i', '')
    guardado = cache.get(_clave(token, i))
    if guardado is None:
        return JsonResponse({'ok': False, 'estado': 'error', 'mensaje': 'El archivo ya no está: vuelva a subirlo.'})
    nombre, datos = guardado
    respuesta = {'archivo': nombre, 'comprobante': '', 'proveedor': '', 'total': '', 'lineas': []}
    try:
        xml = leer(datos, nombre)
    except ErrorXML as exc:
        return JsonResponse({**respuesta, 'ok': False, 'estado': 'error', 'mensaje': str(exc)})
    respuesta.update(comprobante=f'{xml["tipo"]} {xml["serie"]}-{xml["numero"]}',
                     proveedor=f'{xml["ruc_emisor"]} {xml["nombre_emisor"]}'.strip(), total=f'{xml["total"]:,.2f}')
    _, compra = xml_masivo._compra_registrada(xml)
    if compra is None:
        return JsonResponse({**respuesta, 'ok': False, 'estado': 'sin_compra',
                             'mensaje': 'No está registrada en el ERP: créela desde la propuesta SIRE o con «Registrar '
                                        'desde XML».'})
    respuesta['compra'] = compra.pk
    if compra.items.exists():
        return JsonResponse({**respuesta, 'ok': True, 'estado': 'ya_tenia', 'mensaje': 'Ya tenía productos.',
                             'lineas': _resumen_lineas(compra)})
    ok, detalle = cuadra(compra, xml)
    if not ok:
        return JsonResponse({**respuesta, 'ok': False, 'estado': 'no_cuadra',
                             'mensaje': f'No cuadra, no se guardó. {detalle}'})
    xml_masivo.completar_productos(compra, xml)
    from core.sustentos import guardar_archivo, vincular
    vincular(compra, guardar_archivo(None, request.user, datos=xml['xml'].encode('utf-8'), nombre=nombre,
                                     tipo='text/xml'), request.user, 'XML de la factura electrónica')
    cache.delete(_clave(token, i))
    return JsonResponse({**respuesta, 'ok': True, 'estado': 'guardada', 'mensaje': f'Cuadra. {detalle}',
                         'lineas': _resumen_lineas(compra)})


@transaction.atomic
def linea_unica(compra):
    """Una línea por afectación con el importe exacto de la compra (sin cambiar sus totales)."""
    texto = f'Según comprobante {compra.serie}-{compra.numero} de {compra.tercero.nombre}'[:250]
    lineas = []
    if compra.base_imponible:
        lineas.append(CompraItem(documento=compra, descripcion=texto, cantidad=1,
                                 precio_unitario=compra.base_imponible, afectacion=''))
    if compra.no_gravado:
        lineas.append(CompraItem(documento=compra, descripcion=f'{texto} (no gravado)'[:250], cantidad=1,
                                 precio_unitario=compra.no_gravado,
                                 afectacion='' if compra.tipo_operacion != 'GRAVADA' else 'EXONERADA'))
    CompraItem.objects.bulk_create(lineas)  # sin save(): los totales de la compra no cambian
    from core.auditoria import registrar
    registrar('MODIFICAR', compra, {'Productos': ['', f'{len(lineas)} línea(s) según el comprobante']},
              'Línea por compra (sin XML)')


@login_required
@require_POST
def una_linea(request):
    """Completa UNA compra con su línea única (la pantalla las recorre de a una)."""
    compra = Compra.objects.filter(pk=request.POST.get('compra')).select_related('tercero').first()
    if compra is None:
        return JsonResponse({'ok': False, 'estado': 'error', 'mensaje': 'La compra no existe.'})
    base = {'comprobante': f'{compra.tipo_comprobante} {compra.serie}-{compra.numero}',
            'proveedor': f'{compra.tercero.numero_doc} {compra.tercero.nombre}', 'total': f'{compra.total:,.2f}',
            'compra': compra.pk}
    if compra.items.exists():
        return JsonResponse({**base, 'ok': True, 'estado': 'ya_tenia', 'mensaje': 'Ya tenía productos.',
                             'lineas': _resumen_lineas(compra)})
    linea_unica(compra)
    return JsonResponse({**base, 'ok': True, 'estado': 'guardada', 'mensaje': 'Cuadra (importe del comprobante).',
                         'lineas': _resumen_lineas(compra)})
