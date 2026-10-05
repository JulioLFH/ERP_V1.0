from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render


def excel_response(nombre, titulo, encabezados, filas):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = ''.join(c for c in titulo if c not in '[]:*?/\\')[:30] or 'Hoja1'
    ws.append([titulo])
    ws['A1'].font = Font(bold=True, size=13)
    ws.append([])
    ws.append(encabezados)
    for cell in ws[3]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1F4E79')
    for fila in filas:
        ws.append([float(v) if isinstance(v, Decimal) else v for v in fila])
    for col in ws.columns:
        ancho = max(len(str(c.value or '')) for c in col[2:]) if len(col) > 2 else 10
        ws.column_dimensions[col[0].column_letter].width = min(max(ancho + 2, 10), 45)
    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{nombre}.xlsx"'
    wb.save(resp)
    return resp


def txt_response(nombre, lineas):
    contenido = '\r\n'.join(lineas) + ('\r\n' if lineas else '')
    resp = HttpResponse(contenido, content_type='text/plain; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{nombre}"'
    return resp


def leer_excel(archivo):
    """Devuelve lista de dicts usando la primera fila como encabezados (en minúsculas)."""
    from openpyxl import load_workbook

    wb = load_workbook(archivo, data_only=True, read_only=True)
    ws = wb.active
    filas = list(ws.iter_rows(values_only=True))
    if not filas:
        return []
    enc = [str(c or '').strip().lower() for c in filas[0]]
    return [dict(zip(enc, f)) for f in filas[1:] if any(v not in (None, '') for v in f)]


def a_fecha(valor):
    if isinstance(valor, date):
        return valor if not hasattr(valor, 'date') else valor.date()
    from datetime import datetime
    texto = str(valor).strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Fecha inválida: {valor}')


def periodo_actual(request, qs=None):
    """Periodo pedido; si no hay, el actual; y si el actual está vacío, el último con registros."""
    if request.GET.get('periodo'):
        return request.GET['periodo']
    actual = date.today().strftime('%Y%m')
    if qs is not None and not qs.filter(periodo=actual).exists():
        ultimo = qs.exclude(periodo='').order_by('-periodo').values_list('periodo', flat=True).first()
        if ultimo:
            return ultimo
    return actual


def _fin_de_mes(d):
    siguiente = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return siguiente - timedelta(days=1)


def rango_por_defecto(request, qs=None, campo='fecha'):
    """(desde, hasta) ISO: lo pedido por GET, si no el mes en curso completo; si ese mes no tiene
    registros, el mes del último registro."""
    if request.GET.get('desde') and request.GET.get('hasta'):
        return request.GET['desde'], request.GET['hasta']
    hoy = date.today()
    desde, hasta = hoy.replace(day=1), _fin_de_mes(hoy)
    if qs is not None and not qs.filter(**{f'{campo}__range': [desde, hasta]}).exists():
        ultima = qs.order_by(f'-{campo}').values_list(campo, flat=True).first()
        if ultima:
            desde, hasta = ultima.replace(day=1), _fin_de_mes(ultima)
    return desde.isoformat(), hasta.isoformat()


def fmt_fecha(f):
    return f.strftime('%d/%m/%Y') if f else ''


def lineas_formset(formset):
    """[(producto, cantidad)] de las filas válidas y no eliminadas de un formset de ítems."""
    lineas = []
    for f in formset.forms:
        cd = getattr(f, 'cleaned_data', None) or {}
        if cd and not cd.get('DELETE') and cd.get('cantidad'):
            lineas.append((cd.get('producto'), cd['cantidad']))
    return lineas


def faltantes_stock(lineas, almacen, devolver=None):
    """Mensajes por cada producto cuya salida supera el stock del almacén.

    devolver: {producto_id: cantidad} que el mismo documento ya había descontado (al editar).
    """
    from collections import defaultdict

    from .models import Almacen, Empresa
    if Empresa.actual().permitir_stock_negativo:
        return []
    almacen = almacen or Almacen.principal()
    pedido, productos = defaultdict(Decimal), {}
    for producto, cantidad in lineas:
        if producto and producto.es_inventariable:
            pedido[producto.pk] += Decimal(cantidad)
            productos[producto.pk] = producto
    mensajes = []
    for pk, cantidad in pedido.items():
        disponible = productos[pk].stock_en(almacen) + (devolver or {}).get(pk, Decimal('0'))
        if cantidad > disponible:
            mensajes.append(f'Stock insuficiente de "{productos[pk].nombre}" en {almacen}: hay {disponible:,.2f} y '
                            f'se necesitan {cantidad:,.2f}.')
    return mensajes


def procesar_documento(data, form_class, formset_class, instance, al_guardar=None, validar=None):
    """Valida y guarda cabecera + ítems (pantallas y API). Devuelve (doc o None si hay errores, form, formset)."""
    form = form_class(data, instance=instance)
    formset = formset_class(data, instance=form.instance)
    if form.is_valid() and formset.is_valid() and validar:
        for mensaje in validar(form, formset):
            form.add_error(None, mensaje)
    if not (form.is_valid() and formset.is_valid()):
        return None, form, formset
    with transaction.atomic():
        if instance.pk and getattr(instance, 'stock_aplicado', False):
            # revertir con los datos guardados (almacén e ítems anteriores); al_guardar vuelve a aplicar
            type(instance).objects.get(pk=instance.pk).revertir_stock()
            instance.stock_aplicado = False
        if getattr(instance, 'moneda', None) == 'USD' and instance.tipo_cambio in (None, 0, 1):
            from .tipo_cambio import venta_del_dia
            fecha = getattr(instance, 'fecha_emision', None) or getattr(instance, 'fecha', None)
            instance.tipo_cambio = venta_del_dia(fecha)
        doc = form.save()
        formset.instance = doc
        formset.save()
        doc.calcular_totales()
        doc.save()
        if al_guardar:
            al_guardar(doc)
    return doc, form, formset


def guardar_documento(request, form_class, formset_class, instance, template, contexto, al_guardar=None,
                      initial=None, items_iniciales=None, validar=None):
    """Alta/edición de un documento con detalle de ítems (compras, ventas, OC, cotizaciones, guías).

    validar(form, formset) -> [mensajes]: validaciones de negocio antes de guardar (ej. stock).
    """
    if request.method == 'POST':
        doc, form, formset = procesar_documento(request.POST, form_class, formset_class, instance, al_guardar,
                                                validar)
        if doc is not None:
            return redirect(doc_detalle_url(doc))
    else:
        form = form_class(instance=instance, initial=initial)
        if items_iniciales:
            fs_cls = type('FormsetInicial', (formset_class,), {'extra': max(len(items_iniciales) - 1, 0)})
            formset = fs_cls(instance=form.instance, initial=items_iniciales)
        else:
            formset = formset_class(instance=form.instance)
    from .models import Producto
    productos = list(Producto.objects.filter(activo=True).values('id', 'nombre', 'unidad', 'precio_venta', 'costo_promedio',
                                                                 'precio_compra', 'afectacion_igv'))
    from .modulos import puede_ver_costos
    costos = puede_ver_costos(request.user)
    for p in productos:  # compras: precio de compra referencial o, si no hay (y puede ver costos), el costo promedio
        p['precio_compra'] = p['precio_compra'] or (p['costo_promedio'] if costos else 0)
        if not costos:
            p.pop('costo_promedio')  # el costo no viaja al navegador de quien no puede verlo
    contexto.update(form=form, formset=formset, productos=productos)
    return render(request, template, contexto)


def doc_detalle_url(doc):
    from django.urls import reverse
    nombres = {
        'compra': 'compras:detalle', 'ordencompra': 'compras:oc_detalle',
        'venta': 'ventas:detalle', 'cotizacion': 'ventas:cot_detalle',
        'guiaremision': 'logistica:detalle',
    }
    return reverse(nombres[doc._meta.model_name], args=[doc.pk])
