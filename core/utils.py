from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone


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
    actual = timezone.localdate().strftime('%Y%m')
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
    hoy = timezone.localdate()
    desde, hasta = hoy.replace(day=1), _fin_de_mes(hoy)
    if qs is not None and not qs.filter(**{f'{campo}__range': [desde, hasta]}).exists():
        ultima = qs.order_by(f'-{campo}').values_list(campo, flat=True).first()
        if ultima:
            desde, hasta = ultima.replace(day=1), _fin_de_mes(ultima)
    return desde.isoformat(), hasta.isoformat()


def fmt_fecha(f):
    return f.strftime('%d/%m/%Y') if f else ''


def leer_numero(texto):
    """Decimal escrito a mano: 1,109.90 / 1109.90 / 287,50 (coma decimal) / 1.109,90. Con punto y coma, el último
    es el decimal; con solo comas, es decimal si hay una sola y no le siguen exactamente 3 dígitos (1,110 = mil
    ciento diez). Lanza decimal.InvalidOperation si no es un número."""
    t = str(texto or '').strip().replace(' ', '').replace(' ', '')
    if not t:
        return Decimal('0')
    if ',' in t and '.' in t:
        decimal_sep = ',' if t.rfind(',') > t.rfind('.') else '.'
        miles = '.' if decimal_sep == ',' else ','
        t = t.replace(miles, '').replace(decimal_sep, '.')
    elif ',' in t:
        partes = t.split(',')
        t = f'{partes[0]}.{partes[1]}' if len(partes) == 2 and len(partes[1]) != 3 else t.replace(',', '')
    return Decimal(t)


def lineas_formset(formset):
    """[(producto, cantidad)] de las filas válidas y no eliminadas de un formset de ítems."""
    lineas = []
    for f in formset.forms:
        cd = getattr(f, 'cleaned_data', None) or {}
        if cd and not cd.get('DELETE') and cd.get('cantidad'):
            lineas.append((cd.get('producto'), cd['cantidad']))
    return lineas


def saldo_minimo_desde(producto, almacen, fecha):
    """(saldo, fecha) más bajo del producto en el almacén desde `fecha` en adelante: el saldo al cierre de ese día y
    después de cada movimiento posterior. Es lo máximo que puede salir con esa fecha sin dejar el kardex en negativo
    en ningún momento (p. ej. un consumo del 05/10 no puede usar un ingreso del 10/10). Se calcula desde el stock
    actual descontando los movimientos posteriores, así no depende de los saldos migrados."""
    from .models import Kardex
    actual = producto.stock_en(almacen)
    posteriores = list(Kardex.objects.filter(producto=producto, almacen=almacen, fecha__gt=fecha)
                       .order_by('fecha', 'id').values_list('fecha', 'tipo', 'cantidad'))
    if not posteriores:
        return actual, fecha
    neto = sum((c if t == 'ENTRADA' else -c for _, t, c in posteriores), Decimal('0'))
    saldo = actual - neto  # al cierre del día `fecha`
    minimo = (saldo, fecha)
    for dia, tipo, cantidad in posteriores:
        saldo += cantidad if tipo == 'ENTRADA' else -cantidad
        if saldo < minimo[0]:
            minimo = (saldo, dia)
    return minimo


def faltantes_stock(lineas, almacen, devolver=None, fecha=None):
    """Mensajes por cada producto cuya salida supera el stock del almacén.

    devolver: {producto_id: cantidad} que el mismo documento ya había descontado (al editar).
    fecha: fecha del movimiento; si es anterior a hoy, la salida no puede dejar el kardex en negativo en esa fecha
    ni en ningún movimiento posterior.
    """
    from collections import defaultdict

    from django.utils import timezone

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
        extra = (devolver or {}).get(pk, Decimal('0'))
        if fecha and fecha < timezone.localdate():
            minimo, dia = saldo_minimo_desde(productos[pk], almacen, fecha)
            disponible = minimo + extra
            if cantidad > disponible:
                cuando = f'al {fecha:%d/%m/%Y}' if dia == fecha else f'el {dia:%d/%m/%Y}'
                mensajes.append(
                    f'Stock insuficiente de "{productos[pk].nombre}" en {almacen} {cuando}: con fecha '
                    f'{fecha:%d/%m/%Y} pueden salir {max(disponible, Decimal("0")):,.2f} y se necesitan '
                    f'{cantidad:,.2f}. Registre primero el ingreso con una fecha anterior o use la fecha de hoy.')
            continue
        disponible = productos[pk].stock_en(almacen) + extra
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
    # solo los productos que ya están en el documento: los demás se piden uno a uno al elegirlos (antes viajaba
    # todo el catálogo, más de 1 MB, en cada formulario)
    from .opciones import datos_productos
    ids = set()
    for f in formset.forms:
        if 'producto' in f.fields:
            valor = f['producto'].value()
            if str(valor or '').isdigit():
                ids.add(int(valor))
    contexto.update(form=form, formset=formset, productos=datos_productos(request.user, ids))
    return render(request, template, contexto)


def doc_detalle_url(doc):
    from django.urls import reverse
    nombres = {
        'compra': 'compras:detalle', 'ordencompra': 'compras:oc_detalle',
        'venta': 'ventas:detalle', 'cotizacion': 'ventas:cot_detalle',
        'guiaremision': 'logistica:detalle',
    }
    return reverse(nombres[doc._meta.model_name], args=[doc.pk])
