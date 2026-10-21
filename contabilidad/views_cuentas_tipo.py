"""Contabilidad › Configuración › Cuentas por tipo de producto (INV-11): la clase de valoración editable. Una fila por
tipo con sus cuentas de existencias, compra, variación, venta, costo de ventas, producción y diferencia de precio. Al
guardar se puede aplicar a los productos del tipo que siguen la cuenta anterior (las excepciones no se tocan)."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.forms import modelformset_factory
from django.shortcuts import redirect, render

from core.forms import BootstrapMixin
from core.models import CAMPOS_CUENTA, CLASES_PRODUCTO, CuentasTipoProducto, Producto, cuentas_de_clase

from .models import CuentaContable

CAMPOS = ['cuenta_existencias', 'cuenta_compra', 'cuenta_variacion', 'cuenta_venta', 'cuenta_costo',
          'cuenta_produccion', 'cuenta_diferencia_precio']
# clases de cuenta que acepta cada columna (no se puede elegir «101 Caja» como existencias)
PREFIJOS = {'cuenta_existencias': ('20', '21', '22', '23', '24', '25', '26', '27', '28'),
            'cuenta_compra': ('60', '33', '63'), 'cuenta_variacion': ('61',), 'cuenta_venta': ('70', '75'),
            'cuenta_costo': ('69', '65'), 'cuenta_produccion': ('71', '72'),
            'cuenta_diferencia_precio': ('60', '61', '65', '69', '75')}
# variación / producción por defecto según la cuenta de existencias (lo que usa la centralización si no se configura)
VARIACION = {'24': '6121', '25': '6132', '26': '6141', '20': '6111'}
PRODUCCION = {'23': '7131', '21': '7111'}


def _plan(prefijos):
    from django.db.models import Q
    q = Q()
    for p in prefijos:
        q |= Q(codigo__startswith=p)
    return CuentaContable.objects.filter(q, imputable=True).order_by('codigo')


class FilaForm(BootstrapMixin, forms.ModelForm):
    aplicar = forms.BooleanField(required=False, label='Aplicar a los productos del tipo')

    class Meta:
        model = CuentasTipoProducto
        fields = CAMPOS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in CAMPOS:
            self.fields[campo].queryset = _plan(PREFIJOS[campo])
            self.fields[campo].label_from_instance = lambda c: f'{c.codigo} {c.nombre[:40]}'


def _asegurar_filas():
    """Una fila por tipo, con el juego por defecto del PCGE la primera vez."""
    from contabilidad import pcge
    plan = pcge.plan()
    for clase, _ in CLASES_PRODUCTO:
        if CuentasTipoProducto.objects.filter(clase=clase).exists():
            continue
        cuentas = cuentas_de_clase(clase)
        fila = CuentasTipoProducto(clase=clase, **cuentas)
        ex = cuentas.get('cuenta_existencias')
        if ex is not None:
            var, prod = VARIACION.get(ex.codigo[:2]), PRODUCCION.get(ex.codigo[:2])
            fila.cuenta_variacion = CuentaContable.objects.filter(codigo=pcge.codigo(var, plan)).first() if var else None
            fila.cuenta_produccion = (CuentaContable.objects.filter(codigo=pcge.codigo(prod, plan)).first()
                                      if prod else None)
        fila.save()


@login_required
def cuentas_tipo(request):
    _asegurar_filas()
    Formset = modelformset_factory(CuentasTipoProducto, form=FilaForm, extra=0)
    qs = CuentasTipoProducto.objects.all()
    anteriores = {f.pk: {c: getattr(f, f'{c}_id') for c in CAMPOS} for f in qs}
    formset = Formset(request.POST or None, queryset=qs)
    if request.method == 'POST' and formset.is_valid():
        aplicados = 0
        for form in formset.forms:
            fila = form.save()
            if not form.cleaned_data.get('aplicar'):
                continue
            # productos del tipo que seguían la cuenta anterior del tipo (o no tenían): pasan a la nueva
            for campo in CAMPOS_CUENTA:
                antes, ahora = anteriores[fila.pk][campo], getattr(fila, f'{campo}_id')
                if ahora and antes != ahora:
                    aplicados += Producto.objects.filter(clase=fila.clase, **{f'{campo}_id__in': [antes, None]}) \
                        .update(**{f'{campo}_id': ahora})
        messages.success(request, 'Cuentas por tipo de producto guardadas.' +
                         (f' Se actualizaron {aplicados} cuenta(s) de productos que seguían las del tipo.'
                          if aplicados else ''))
        return redirect('contabilidad:cuentas_tipo')
    excepciones = {}
    for f in qs:
        n = 0
        for campo in CAMPOS_CUENTA:
            valor = getattr(f, f'{campo}_id')
            if valor:
                n += Producto.objects.filter(clase=f.clase).exclude(**{f'{campo}_id': valor}).exclude(
                    **{f'{campo}_id__isnull': True}).count()
        excepciones[f.clase] = n
    filas = [(form, excepciones.get(form.instance.clase, 0)) for form in formset.forms]
    return render(request, 'contabilidad/cuentas_tipo.html', {'formset': formset, 'filas': filas, 'campos': CAMPOS})
