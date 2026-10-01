from datetime import date
from decimal import Decimal

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render


def excel_response(nombre, titulo, encabezados, filas):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = titulo[:30]
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


def periodo_actual(request):
    return request.GET.get('periodo') or date.today().strftime('%Y%m')


def fmt_fecha(f):
    return f.strftime('%d/%m/%Y') if f else ''


def guardar_documento(request, form_class, formset_class, instance, template, contexto, al_guardar=None,
                      initial=None, items_iniciales=None):
    """Alta/edición de un documento con detalle de ítems (compras, ventas, OC, cotizaciones)."""
    if request.method == 'POST':
        form = form_class(request.POST, instance=instance)
        formset = formset_class(request.POST, instance=form.instance)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                if instance.pk and getattr(instance, 'stock_aplicado', False):
                    instance.revertir_stock()  # se vuelve a aplicar con los ítems nuevos en al_guardar
                doc = form.save()
                formset.instance = doc
                formset.save()
                doc.calcular_totales()
                doc.save()
                if al_guardar:
                    al_guardar(doc)
            return redirect(doc_detalle_url(doc))
    else:
        form = form_class(instance=instance, initial=initial)
        if items_iniciales:
            fs_cls = type('FormsetInicial', (formset_class,), {'extra': max(len(items_iniciales) - 1, 0)})
            formset = fs_cls(instance=form.instance, initial=items_iniciales)
        else:
            formset = formset_class(instance=form.instance)
    from .models import Producto
    productos = list(Producto.objects.filter(activo=True).values('id', 'nombre', 'precio_venta', 'costo_promedio'))
    contexto.update(form=form, formset=formset, productos=productos)
    return render(request, template, contexto)


def doc_detalle_url(doc):
    from django.urls import reverse
    nombres = {
        'compra': 'compras:detalle', 'ordencompra': 'compras:oc_detalle',
        'venta': 'ventas:detalle', 'cotizacion': 'ventas:cot_detalle',
    }
    return reverse(nombres[doc._meta.model_name], args=[doc.pk])
