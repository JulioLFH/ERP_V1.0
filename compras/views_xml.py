"""Compras desde el XML del proveedor: subir, revisar (proveedor, productos, clasificación) y registrar."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from contabilidad.models import CentroCosto, CuentaContable
from core.forms import BootstrapMixin
from core.models import Almacen, Producto

from . import importar_xml as servicio
from .models import Compra

SESION = 'compra_xml'


class SubirXMLForm(BootstrapMixin, forms.Form):
    archivo = forms.FileField(label='XML de la factura (.xml o .zip)',
                              help_text='El XML que el proveedor envía por correo o que descarga de SUNAT')


class RevisarXMLForm(BootstrapMixin, forms.Form):
    clasificacion = forms.ChoiceField(label='Clasificación', choices=Compra.CLASIFICACION)
    forma_pago = forms.ChoiceField(label='Forma de pago', choices=Compra._meta.get_field('forma_pago').choices)
    ingresar_almacen = forms.BooleanField(label='Ingresar los productos al almacén', required=False, initial=True)
    almacen = forms.ModelChoiceField(Almacen.objects.none(), label='Almacén', required=False)
    centro_costo = forms.ModelChoiceField(CentroCosto.objects.none(), label='Centro de costo', required=False)
    cuenta_contable = forms.ModelChoiceField(CuentaContable.objects.none(), label='Cuenta de gasto / compra',
                                             required=False, help_text='Vacío = según la clasificación')

    def __init__(self, *args, lineas=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['almacen'].queryset = Almacen.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['cuenta_contable'].queryset = CuentaContable.objects.filter(
            imputable=True, activo=True, codigo__regex=r'^(6|3)')
        productos = Producto.objects.filter(activo=True)
        for i, l in enumerate(lineas):
            self.fields[f'producto_{i}'] = forms.ModelChoiceField(
                productos, required=False, label=l['descripcion'], initial=l['producto'],
                widget=forms.Select(attrs={'class': 'form-select form-select-sm'}))

    def lineas(self):
        return [self[f] for f in self.fields if f.startswith('producto_')]


def _xml_de_sesion(request):
    datos = request.session.get(SESION)
    if not datos:
        return None, None
    return datos['xml'].encode('utf-8'), datos['nombre']


@login_required
def importar(request):
    if request.method == 'POST' and 'archivo' in request.FILES:
        form = SubirXMLForm(request.POST, request.FILES)
        if form.is_valid():
            archivo = form.cleaned_data['archivo']
            datos = archivo.read()
            try:
                xml = servicio.analizar(datos, archivo.name)
            except servicio.ErrorImportacion as exc:
                messages.error(request, str(exc))
            else:
                request.session[SESION] = {'xml': xml['xml'], 'nombre': archivo.name[:150]}
                return redirect('compras:importar_xml_revisar')
        return render(request, 'compras/importar_xml.html', {'form': form})
    return render(request, 'compras/importar_xml.html', {'form': SubirXMLForm()})


@login_required
def revisar(request):
    datos, nombre = _xml_de_sesion(request)
    if datos is None:
        return redirect('compras:importar_xml')
    try:
        xml = servicio.analizar(datos, nombre)
    except servicio.ErrorImportacion as exc:
        messages.error(request, str(exc))
        request.session.pop(SESION, None)
        return redirect('compras:importar_xml')
    proveedor = xml['proveedor']
    inicial = {'clasificacion': 'MERCADERIA' if any(l['producto'] for l in xml['lineas']) else 'GASTO',
               'forma_pago': 'CREDITO' if xml['vencimiento'] and xml['vencimiento'] > xml['fecha'].isoformat()
               else 'CONTADO', 'almacen': Almacen.principal()}
    form = RevisarXMLForm(request.POST or None, lineas=xml['lineas'], initial=inicial)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        productos = {i: d.get(f'producto_{i}') for i in range(len(xml['lineas']))}
        try:
            compra = servicio.registrar(xml, productos, d, request.user, nombre)
        except servicio.ErrorImportacion as exc:
            messages.error(request, str(exc))
        else:
            request.session.pop(SESION, None)
            messages.success(request, f'Compra {compra.numero_completo} de {compra.tercero.nombre} registrada desde '
                                      f'el XML (total {compra.simbolo} {compra.total:,.2f}).')
            return redirect('compras:detalle', compra.pk)
    return render(request, 'compras/importar_xml_revisar.html', {
        'xml': xml, 'form': form, 'proveedor': proveedor, 'nombre': nombre,
        'filas': list(zip(xml['lineas'], form.lineas()))})
