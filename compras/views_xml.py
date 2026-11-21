"""Compras desde el XML del proveedor: subir, revisar (proveedor, productos, clasificación) y registrar."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from contabilidad.models import CentroCosto, CuentaContable
from core.forms import BootstrapMixin
from core.models import Almacen, Producto

from . import importar_xml as servicio
from .models import Compra, OrdenCompra

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
    orden_compra = forms.ModelChoiceField(OrdenCompra.objects.none(), label='Orden de compra', required=False,
                                          help_text='Conecta la factura a su orden: avance, cruce con lo recibido y '
                                                    'vencimiento desde el ingreso')

    def __init__(self, *args, lineas=(), ordenes=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['orden_compra'].queryset = ordenes if ordenes is not None else OrdenCompra.objects.none()
        self.fields['orden_compra'].widget.attrs['data-ir-con'] = 'orden_compra'  # recarga con sus productos
        self.fields['almacen'].queryset = Almacen.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.para_formulario(self)
        self.fields['cuenta_contable'].queryset = CuentaContable.objects.filter(
            imputable=True, activo=True, codigo__regex=r'^(6|3)')
        productos = Producto.objects.filter(activo=True, es_plantilla=False)
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
def importar_varios(request):
    """v1.115.7: varios XML (o un ZIP): completa los productos de las compras registradas o registra las que faltan."""
    from . import xml_masivo
    resultado = None
    if request.method == 'POST' and request.FILES.getlist('archivos'):
        opciones = {'clasificacion': request.POST.get('clasificacion') or 'GASTO',
                    'forma_pago': request.POST.get('forma_pago') or 'CONTADO'}
        resultado = xml_masivo.procesar(request.FILES.getlist('archivos'), opciones, request.user)
        r = resultado['resumen']
        messages.info(request, f'{r["total"]} XML: {r["completada"]} con productos completados, {r["creada"]} '
                               f'compras registradas, {r["ya_tenia"]} ya tenían productos y {r["error"]} con error.')
    return render(request, 'compras/importar_xml_masivo.html', {
        'r': resultado, 'clasificaciones': Compra.CLASIFICACION,
        'formas_pago': Compra._meta.get_field('forma_pago').choices})


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
                oc = request.GET.get('oc') or request.POST.get('oc') or ''
                if oc.isdigit():  # v1.117.0: desde la orden de compra («Factura desde XML»)
                    return redirect(f"{reverse('compras:importar_xml_revisar')}?orden_compra={oc}")
                return redirect('compras:importar_xml_revisar')
        return render(request, 'compras/importar_xml.html', {'form': form, 'oc': _oc_de(request)})
    return render(request, 'compras/importar_xml.html', {'form': SubirXMLForm(), 'oc': _oc_de(request)})


def _oc_de(request):
    oc = request.GET.get('oc') or ''
    return OrdenCompra.objects.filter(pk=oc).select_related('tercero').first() if oc.isdigit() else None


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
    # v1.117.0: la orden de compra elegida (o la que cita el XML) completa los productos de las líneas
    ordenes = servicio.ordenes_candidatas(proveedor)
    elegida = request.POST.get('orden_compra') if request.method == 'POST' else request.GET.get('orden_compra')
    if elegida is not None:
        oc, motivo_oc = (ordenes.filter(pk=elegida).first() if str(elegida).isdigit() else None), 'elegida'
        if str(elegida).isdigit() and oc is None:
            messages.warning(request, 'Esa orden de compra no es una orden aprobada de este proveedor.')
    else:
        oc, motivo_oc = servicio.orden_sugerida(xml, proveedor)
    servicio.aplicar_orden(xml, oc)
    inicial = {'clasificacion': 'MERCADERIA' if any(l['producto'] for l in xml['lineas']) else 'GASTO',
               'forma_pago': 'CREDITO' if xml['vencimiento'] and xml['vencimiento'] > xml['fecha'].isoformat()
               else 'CONTADO', 'almacen': Almacen.principal(), 'orden_compra': oc}
    if oc is not None:
        inicial['clasificacion'] = Compra.clasificacion_de(oc.items.select_related('producto'))
        inicial['centro_costo'] = oc.centro_costo_id
        if oc.dias_credito:
            inicial['forma_pago'] = 'CREDITO'
        from inventario.servicios import tiene_recepciones
        if tiene_recepciones(orden=oc):
            inicial['ingresar_almacen'] = False  # ya se recibió en Inventario
    form = RevisarXMLForm(request.POST or None, lineas=xml['lineas'], initial=inicial, ordenes=ordenes)
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
        'xml': xml, 'form': form, 'proveedor': proveedor, 'nombre': nombre, 'oc': oc, 'motivo_oc': motivo_oc,
        'filas': list(zip(xml['lineas'], form.lineas()))})
