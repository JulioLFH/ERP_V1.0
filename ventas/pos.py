"""Punto de venta (POS): venta rápida al contado con cobro en caja.

Emite el comprobante con el mismo formulario y validaciones que la pantalla de ventas (series del usuario, stock,
SUNAT) y registra el cobro en la caja elegida."""
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from core.models import Almacen, Producto, StockAlmacen, Tercero
from finanzas.models import Cuenta, Movimiento

from .models import Venta, VentaItem

VARIOS_DOC = '00000000'


def cliente_varios():
    t, _ = Tercero.objects.get_or_create(tipo_doc='0', numero_doc=VARIOS_DOC,
                                         defaults={'tipo': 'CLIENTE', 'nombre': 'CLIENTES VARIOS'})
    return t


@login_required
def pos(request):
    if request.method == 'POST':
        return _cobrar(request)
    hoy = date.today()
    cajas = Cuenta.objects.filter(activo=True, moneda='PEN').order_by('tipo', 'nombre')  # CAJA primero
    resumen = {}
    for m in Movimiento.objects.filter(fecha=hoy, concepto='COBRANZA', creado_por=request.user,
                                       glosa__startswith='POS ').select_related('cuenta'):
        clave = (m.cuenta.nombre, m.get_medio_pago_display())
        resumen[clave] = resumen.get(clave, Decimal('0')) + m.monto
    return render(request, 'ventas/pos.html', {
        'cajas': cajas, 'almacenes': Almacen.objects.filter(activo=True, uso=''), 'almacen': Almacen.principal(),
        'medios': [m for m in Movimiento.MEDIOS if m[0] != 'CHEQUE'], 'varios': cliente_varios(),
        'resumen': sorted(resumen.items()), 'total_dia': sum(resumen.values(), Decimal('0'))})


@login_required
def pos_productos(request):
    """Búsqueda de productos para el POS: precio con y sin IGV y stock del almacén."""
    q = request.GET.get('q', '').strip()
    almacen = request.GET.get('almacen')
    qs = Producto.objects.filter(activo=True, puede_venderse=True, es_plantilla=False)
    if q:
        qs = qs.filter(Q(codigo__iexact=q) | Q(codigo__icontains=q) | Q(nombre__icontains=q))
    elif almacen:  # sin búsqueda: lo que hay para vender en el almacén
        qs = qs.filter(stocks__almacen_id=almacen, stocks__cantidad__gt=0, precio_venta__gt=0)
    productos = list(qs.order_by('nombre')[:40])
    stocks = dict(StockAlmacen.objects.filter(almacen_id=almacen, producto__in=productos)
                  .values_list('producto_id', 'cantidad')) if almacen else {}
    from core.models import Empresa
    tasa = Empresa.actual().igv_tasa / 100
    datos = []
    for p in productos:
        gravado = (p.afectacion_igv or 'GRAVADA') == 'GRAVADA'
        datos.append({'id': p.pk, 'codigo': p.codigo, 'nombre': p.nombre, 'unidad': p.unidad,
                      'precio': str(p.precio_venta), 'precio_igv': str((p.precio_venta * (1 + (tasa if gravado else 0)))
                                                                        .quantize(Decimal('0.01'))),
                      'gravado': gravado, 'bien': p.es_inventariable,
                      'stock': str(stocks.get(p.pk, 0)) if p.es_inventariable else ''})
    return JsonResponse(datos, safe=False)


def _error(mensaje, status=400):
    return JsonResponse({'ok': False, 'error': mensaje}, status=status)


def _cobrar(request):
    from core.api import _venta_desde_json
    from core.forms import item_formset
    from core.permisos import puede
    from core.utils import procesar_documento

    from .forms import VentaForm
    from .views import ventas_views
    if not puede(request.user, 'ventas.emitir'):
        return _error('No tiene permiso para emitir comprobantes.', 403)
    try:
        datos = json.loads(request.body or b'{}')
    except ValueError:
        return _error('Datos inválidos.')
    items = datos.get('items') or []
    if not items:
        return _error('Agregue productos a la venta.')
    caja = Cuenta.objects.filter(pk=datos.get('caja'), activo=True, moneda='PEN').first()
    if caja is None:
        return _error('Elija la caja donde se cobra.')
    tipo = datos.get('tipo_comprobante') if datos.get('tipo_comprobante') in ('01', '03', '00', '12') else '03'
    cliente = Tercero.objects.filter(pk=datos.get('cliente')).first() or cliente_varios()
    cabecera = {'tipo_comprobante': tipo, 'tercero': cliente.pk, 'forma_pago': 'CONTADO',
                'almacen': datos.get('almacen') or Almacen.principal().pk, 'vendedor': request.user.get_full_name()
                or request.user.get_username(), 'glosa': 'Punto de venta',
                'items': [{'producto_id': i.get('id'), 'cantidad': i.get('cantidad'),
                           'precio_unitario': i.get('precio'), 'descuento_pct': i.get('descuento') or 0}
                          for i in items]}
    medio = datos.get('medio') if datos.get('medio') in dict(Movimiento.MEDIOS) else 'EFECTIVO'
    with transaction.atomic():
        doc, form, formset = procesar_documento(
            _venta_desde_json(cabecera), VentaForm, item_formset(Venta, VentaItem), Venta(), ventas_views.al_guardar,
            ventas_views.validar_stock)
        if doc is None:
            errores = [str(e) for e in form.non_field_errors()] + [
                f'{form.fields[k].label if k in form.fields else k}: {" ".join(v)}'
                for k, v in form.errors.items() if k != '__all__']
            for f in formset.forms:
                errores += [f'{k}: {" ".join(v)}' for k, v in f.errors.items()]
            errores += [str(e) for e in formset.non_form_errors()]
            transaction.set_rollback(True)
            return _error(' '.join(errores) or 'Revise la venta.', 422)
        try:
            recibido = Decimal(str(datos.get('recibido') or doc.total))
        except InvalidOperation:
            recibido = doc.total
        Movimiento.objects.create(
            cuenta=caja, fecha=doc.fecha_emision, tipo='INGRESO', concepto='COBRANZA', medio_pago=medio,
            numero_operacion=str(datos.get('operacion') or '')[:40], tercero=doc.tercero, venta=doc, monto=doc.total,
            monto_doc=doc.total, glosa=f'POS {doc}'[:250], creado_por=request.user)
    return JsonResponse({'ok': True, 'venta': doc.pk, 'numero': str(doc), 'total': str(doc.total),
                         'vuelto': str(max(recibido - doc.total, Decimal('0'))) if medio == 'EFECTIVO' else '0',
                         'imprimir': reverse('ventas:imprimir', args=[doc.pk]),
                         'detalle': reverse('ventas:detalle', args=[doc.pk])})
