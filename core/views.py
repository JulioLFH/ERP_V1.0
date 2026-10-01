from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from compras.models import Compra
from finanzas.models import Cuenta
from ventas.models import Venta

from .forms import EmpresaForm, ProductoForm, SerieForm, TerceroForm
from .models import Empresa, Producto, Serie, Tercero

D0 = Decimal('0')


def _total_pen(qs):
    """Suma total en soles considerando signo de NC."""
    total = D0
    for tipo, monto, tc in qs.values_list('tipo_comprobante', 'total', 'tipo_cambio'):
        total += (-1 if tipo == '07' else 1) * monto * tc
    return total


def _mes_anterior(anio, mes, n):
    mes -= n
    while mes <= 0:
        mes += 12
        anio -= 1
    return anio, mes


@login_required
def dashboard(request):
    hoy = date.today()
    periodo = hoy.strftime('%Y%m')
    ventas = Venta.objects.filter(estado='REGISTRADO')
    compras = Compra.objects.filter(estado='REGISTRADO')

    por_cobrar = [v for v in ventas.exclude(tipo_comprobante__in=['07', '08']).select_related('tercero') if v.saldo > 0]
    por_pagar = [c for c in compras.exclude(tipo_comprobante__in=['07', '08']).select_related('tercero') if c.saldo > 0]

    meses, serie_v, serie_c = [], [], []
    for i in range(5, -1, -1):
        a, m = _mes_anterior(hoy.year, hoy.month, i)
        p = f'{a}{m:02d}'
        meses.append(f'{m:02d}/{a}')
        serie_v.append(float(_total_pen(ventas.filter(periodo=p))))
        serie_c.append(float(_total_pen(compras.filter(periodo=p))))

    top_clientes = (ventas.filter(periodo=periodo).exclude(tipo_comprobante='07')
                    .values('tercero__nombre').annotate(t=Sum('total')).order_by('-t')[:5])
    cuentas = Cuenta.objects.filter(activo=True)

    ctx = {
        'ventas_mes': _total_pen(ventas.filter(periodo=periodo)),
        'compras_mes': _total_pen(compras.filter(periodo=periodo)),
        'total_cobrar': sum((v.saldo * v.tipo_cambio for v in por_cobrar), D0),
        'total_pagar': sum((c.saldo * c.tipo_cambio for c in por_pagar), D0),
        'cuentas': cuentas,
        'saldo_cuentas': sum((c.saldo for c in cuentas if c.moneda == 'PEN'), D0),
        'vencidos_cobrar': sorted([v for v in por_cobrar if v.dias_vencido > 0], key=lambda d: -d.dias_vencido)[:6],
        'vencidos_pagar': sorted([c for c in por_pagar if c.dias_vencido >= -7], key=lambda d: d.fecha_vencimiento)[:6],
        'stock_bajo': Producto.objects.filter(activo=True, tipo='BIEN', stock__lte=F('stock_minimo'))[:6],
        'top_clientes': top_clientes,
        'chart': {'meses': meses, 'ventas': serie_v, 'compras': serie_c},
    }
    return render(request, 'core/dashboard.html', ctx)


@login_required
def empresa_config(request):
    empresa = Empresa.actual()
    form = EmpresaForm(request.POST or None, instance=empresa)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Datos de la empresa actualizados.')
        return redirect('empresa')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Datos de la empresa'})


# ---------------------------------------------------------------- CRUD genérico
class ListaGenerica(LoginRequiredMixin, ListView):
    template_name = 'core/lista.html'
    paginate_by = 50
    titulo = ''
    columnas = []
    url_nuevo = url_editar = ''
    buscar_en = []
    filtros = {}

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get('q', '').strip()
        if q and self.buscar_en:
            cond = Q()
            for campo in self.buscar_en:
                cond |= Q(**{f'{campo}__icontains': q})
            qs = qs.filter(cond)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(titulo=self.titulo, columnas=self.columnas, url_nuevo=self.url_nuevo,
                   url_editar=self.url_editar, q=self.request.GET.get('q', ''))
        return ctx


class FormGenerico(LoginRequiredMixin, SuccessMessageMixin):
    template_name = 'core/form.html'
    titulo = ''
    success_message = 'Registro guardado correctamente.'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = self.titulo
        return ctx


class TerceroLista(ListaGenerica):
    model = Tercero
    titulo = 'Clientes y proveedores'
    columnas = [('Tipo', 'get_tipo_display'), ('Doc.', 'get_tipo_doc_display'), ('Número', 'numero_doc'),
                ('Nombre / Razón social', 'nombre'), ('Teléfono', 'telefono'), ('Email', 'email'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'tercero_nuevo', 'tercero_editar'
    buscar_en = ['nombre', 'numero_doc']
    template_name = 'core/terceros.html'

    def get_queryset(self):
        qs = super().get_queryset()
        tipo = self.request.GET.get('tipo')
        if tipo in ('CLIENTE', 'PROVEEDOR'):
            qs = qs.filter(tipo__in=[tipo, 'AMBOS'])
        return qs


class TerceroNuevo(FormGenerico, CreateView):
    model, form_class, titulo = Tercero, TerceroForm, 'Nuevo cliente / proveedor'
    success_url = reverse_lazy('terceros')

    def get_initial(self):
        return {'tipo': self.request.GET.get('tipo', 'CLIENTE')}


class TerceroEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Tercero, TerceroForm, 'Editar cliente / proveedor'
    success_url = reverse_lazy('terceros')


class ProductoLista(ListaGenerica):
    model = Producto
    titulo = 'Productos y servicios (almacén)'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Tipo', 'get_tipo_display'), ('U.M.', 'unidad'),
                ('P. venta', 'precio_venta'), ('Costo prom.', 'costo_promedio'), ('Stock', 'stock'),
                ('Valorizado', 'valorizado')]
    url_nuevo, url_editar = 'producto_nuevo', 'producto_editar'
    buscar_en = ['codigo', 'nombre']
    template_name = 'core/productos.html'


class ProductoNuevo(FormGenerico, CreateView):
    model, form_class, titulo = Producto, ProductoForm, 'Nuevo producto / servicio'
    success_url = reverse_lazy('productos')


class ProductoEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Producto, ProductoForm, 'Editar producto / servicio'
    success_url = reverse_lazy('productos')


@login_required
def kardex(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    return render(request, 'core/kardex.html', {'producto': producto, 'movs': producto.kardex.all()[:300]})


class SerieLista(ListaGenerica):
    model = Serie
    titulo = 'Correlativos (series de comprobantes, OC, cotizaciones y vouchers)'
    columnas = [('Tipo', 'get_tipo_display'), ('Serie', 'serie'), ('Último correlativo', 'correlativo'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'serie_nueva', 'serie_editar'


class SerieNueva(FormGenerico, CreateView):
    model, form_class, titulo = Serie, SerieForm, 'Nuevo correlativo'
    success_url = reverse_lazy('series')


class SerieEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Serie, SerieForm, 'Editar correlativo'
    success_url = reverse_lazy('series')
