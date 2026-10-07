from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db.models import F, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, UpdateView

from compras.models import Compra
from finanzas.models import Cuenta
from ventas.models import Venta

from . import tipo_cambio
from .forms import EmpresaForm, FacturacionConfigForm, ProductoForm, SerieForm, TerceroForm, TipoCambioForm
from .models import Empresa, FacturacionConfig, Producto, Serie, Tercero, TipoCambio

D0 = Decimal('0')


def _total_pen(qs):
    """Suma total en soles (los mismos importes de la contabilidad), con signo negativo para NC."""
    agg = qs.aggregate(t=Sum('total_pen', filter=~Q(tipo_comprobante='07')),
                       nc=Sum('total_pen', filter=Q(tipo_comprobante='07')))
    return (agg['t'] or D0) - (agg['nc'] or D0)


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
    ventas_mov, compras_mov = ventas.de_gestion(), compras.de_gestion()

    # una sola consulta por lista (pagos y notas anotados), sin consultas por documento
    por_cobrar = [v for v in ventas.con_saldos().cobrables().exclude(tipo_comprobante='07').select_related('tercero')
                  if v.saldo > 0]
    por_pagar = [c for c in compras.con_saldos().cobrables().exclude(tipo_comprobante='07').select_related('tercero')
                 if c.saldo > 0]

    meses, serie_v, serie_c = [], [], []
    for i in range(5, -1, -1):
        a, m = _mes_anterior(hoy.year, hoy.month, i)
        p = f'{a}{m:02d}'
        meses.append(f'{m:02d}/{a}')
        serie_v.append(float(_total_pen(ventas_mov.filter(periodo=p))))
        serie_c.append(float(_total_pen(compras_mov.filter(periodo=p))))

    top_clientes = (ventas_mov.filter(periodo=periodo).exclude(tipo_comprobante='07')
                    .values('tercero__nombre').annotate(t=Sum('total')).order_by('-t')[:5])
    cuentas = list(Cuenta.objects.filter(activo=True))
    tc = tipo_cambio.obtener(hoy)
    tc_venta = tc.venta if tc else Decimal('1')
    saldo_pen = sum((c.saldo for c in cuentas if c.moneda == 'PEN'), D0)
    saldo_usd = sum((c.saldo for c in cuentas if c.moneda == 'USD'), D0)

    ctx = {
        'tc': tc,
        'saldo_pen': saldo_pen,
        'saldo_usd': saldo_usd,
        'saldo_consolidado': saldo_pen + saldo_usd * tc_venta,
        'ventas_mes': _total_pen(ventas_mov.filter(periodo=periodo)),
        'compras_mes': _total_pen(compras_mov.filter(periodo=periodo)),
        'total_cobrar': sum((v.saldo_pen for v in por_cobrar), D0),
        'total_pagar': sum((c.saldo_pen for c in por_pagar), D0),
        'cuentas': cuentas,
        'vencidos_cobrar': sorted([v for v in por_cobrar if v.dias_vencido > 0], key=lambda d: -d.dias_vencido)[:6],
        'vencidos_pagar': sorted([c for c in por_pagar if c.dias_vencido >= -7],
                                 key=lambda d: d.fecha_vencimiento or d.fecha_emision)[:6],
        'stock_bajo': Producto.objects.filter(activo=True, tipo='BIEN', es_plantilla=False,
                                              stock__lte=F('stock_minimo'))[:6],
        'top_clientes': top_clientes,
        'chart': {'meses': meses, 'ventas': serie_v, 'compras': serie_c},
    }
    return render(request, 'core/dashboard.html', ctx)


@login_required
def home(request):
    """Pantalla de aplicaciones (como el inicio de Odoo)."""
    request.session.pop('modulo', None)
    return render(request, 'core/home.html')


@login_required
def empresa_config(request):
    empresa = Empresa.actual()
    form = EmpresaForm(request.POST or None, instance=empresa)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Datos de la empresa actualizados.')
        return redirect('empresa')
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Datos de la empresa'})


@login_required
def facturacion_config(request):
    cfg = FacturacionConfig.actual()
    if request.method == 'POST' and request.POST.get('accion') == 'probar':
        from .sunat import probar_conexion
        ok, mensaje = probar_conexion()
        (messages.success if ok else messages.error)(request, mensaje)
        return redirect('facturacion')
    form = FacturacionConfigForm(request.POST or None, instance=cfg)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Configuración de facturación electrónica guardada.')
        return redirect('facturacion')
    return render(request, 'core/facturacion.html', {'form': form, 'cfg': cfg})


@login_required
def correo_config(request):
    from .correo import ErrorCorreo, enviar
    from .forms import CorreoConfigForm
    from .models import CorreoConfig
    cfg = CorreoConfig.actual()
    if request.method == 'POST' and request.POST.get('accion') == 'probar':
        destino = request.POST.get('destino', '').strip() or request.user.email
        try:
            enviar([destino], f'Prueba de correo - {settings.ERP_NOMBRE}', 'proveedores/correo_prueba.html',
                   {'usuario': request.user})
            messages.success(request, f'Correo de prueba enviado a {destino}.')
        except ErrorCorreo as exc:
            messages.error(request, str(exc))
        return redirect('correo')
    form = CorreoConfigForm(request.POST or None, instance=cfg)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Configuración de correo guardada.')
        return redirect('correo')
    return render(request, 'core/correo.html', {'form': form, 'cfg': cfg})


# ---------------------------------------------------------------- auditoría
@login_required
def auditoria(request):
    from django.contrib.auth.models import User
    from .models import Bitacora
    qs = Bitacora.objects.select_related('usuario')
    f = request.GET
    if f.get('usuario'):
        qs = qs.filter(usuario_id=f['usuario'])
    if f.get('accion'):
        qs = qs.filter(accion=f['accion'])
    if f.get('modelo'):
        qs = qs.filter(modelo=f['modelo'])
    if f.get('desde'):
        qs = qs.filter(fecha__date__gte=f['desde'])
    if f.get('hasta'):
        qs = qs.filter(fecha__date__lte=f['hasta'])
    q = f.get('q', '').strip()
    if q:
        qs = qs.filter(Q(objeto__icontains=q) | Q(motivo__icontains=q) | Q(objeto_id=q))
    if f.get('formato') == 'excel':
        from .utils import excel_response
        filas = [[timezone.localtime(b.fecha).strftime('%d/%m/%Y %H:%M'),
                  b.usuario.username if b.usuario else 'Sistema', b.get_accion_display(), b.modelo_nombre, b.objeto,
                  b.motivo, '; '.join(f'{k}: {v[0]} → {v[1]}' for k, v in b.cambios.items()
                                      if isinstance(v, list) and len(v) == 2), b.ip] for b in qs[:20000]]
        return excel_response('Auditoria', 'Bitácora de auditoría',
                              ['Fecha', 'Usuario', 'Acción', 'Tipo', 'Registro', 'Motivo', 'Cambios', 'IP'], filas)
    modelos = Bitacora.objects.values_list('modelo', 'modelo_nombre').distinct().order_by('modelo_nombre')
    return render(request, 'core/auditoria.html', {
        'page_obj': Paginator(qs, 100).get_page(f.get('page')), 'usuarios': User.objects.order_by('username'),
        'acciones': Bitacora.ACCIONES, 'modelos': sorted(set(modelos), key=lambda m: m[1]), 'q': q})


# ---------------------------------------------------------------- respaldo
@login_required
def respaldo(request):
    """Copia completa de los datos (JSON comprimido) para guardarla fuera del servidor. Solo administradores."""
    if not request.user.is_superuser:
        return render(request, 'core/sin_acceso.html', {'motivo': 'Solo un administrador puede descargar el respaldo.'},
                      status=403)
    if request.method == 'POST':
        import gzip
        import io
        from django.core.management import call_command
        from django.http import HttpResponse
        salida = io.StringIO()
        call_command('dumpdata', '--natural-foreign', '--natural-primary', '--indent', '0',
                     '--exclude', 'contenttypes', '--exclude', 'auth.permission', '--exclude', 'sessions',
                     '--exclude', 'admin.logentry', stdout=salida)
        datos = gzip.compress(salida.getvalue().encode('utf-8'))
        resp = HttpResponse(datos, content_type='application/gzip')
        nombre = f'respaldo_ceiba_{timezone.localtime():%Y%m%d_%H%M}.json.gz'
        resp['Content-Disposition'] = f'attachment; filename="{nombre}"'
        return resp
    return render(request, 'core/respaldo.html')


# ---------------------------------------------------------------- carga masiva
@login_required
def carga_masiva(request):
    from . import carga_masiva as cm
    from .modulos import modulos_del_usuario, puede_ver_costos
    mods = modulos_del_usuario(request.user)
    permitidos = OrderedDict((k, d) for k, d in cm.DEFINICIONES.items()
                             if (set(d['modulos']) & mods or 'ajustes' in mods)
                             and (not d.get('costos') or puede_ver_costos(request.user)))
    if not permitidos:
        messages.error(request, 'No tiene permiso para cargas masivas.')
        return redirect('home')
    tipo = request.POST.get('tipo') or request.GET.get('tipo') or next(iter(permitidos))
    if tipo not in permitidos:
        if tipo == 'saldos':
            messages.info(request, 'Los saldos iniciales requieren el permiso "Ver costos de inventario".')
        tipo = next(iter(permitidos))
    if request.GET.get('plantilla'):
        from django.http import HttpResponse
        resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="plantilla_{tipo}.xlsx"'
        cm.plantilla(tipo).save(resp)
        return resp
    ctx = {'definiciones': permitidos, 'tipo': tipo, 'definicion': permitidos[tipo]}
    accion = request.POST.get('accion')
    from .permisos import puede
    if tipo == 'listas_precios' and request.method == 'POST' and not puede(request.user, 'ventas.precios'):
        messages.error(request, 'Acción no permitida. Su usuario no tiene permiso para mantener listas de precios y '
                                'descuentos. Pida al administrador que se lo asigne en Ajustes > Usuarios y permisos.')
        return redirect(f"{request.path}?tipo={tipo}")
    if request.method == 'POST' and accion == 'validar':
        archivo = request.FILES.get('archivo')
        if not archivo:
            messages.error(request, 'Seleccione el archivo Excel.')
        else:
            actualizar = bool(request.POST.get('actualizar'))
            try:
                filas, errores = cm.validar(archivo, tipo, actualizar)
            except cm.ErrorFila as exc:
                messages.error(request, str(exc))
            else:
                if not errores and filas:
                    import base64
                    archivo.seek(0)
                    # el Excel queda como sustento de los saldos que se carguen
                    request.session['carga_masiva'] = {'tipo': tipo, 'filas': filas, 'archivo': archivo.name,
                                                       'datos': base64.b64encode(archivo.read()).decode()}
                else:
                    request.session.pop('carga_masiva', None)
                # archivos grandes: se muestran los errores y una muestra de las filas válidas
                muestra = filas if len(filas) <= 1000 else (
                    [f for f in filas if f.get('error')][:1000] + [f for f in filas if not f.get('error')][:300])
                ctx.update(filas=muestra, total_filas=len(filas), errores=errores, validas=len(filas) - errores,
                           archivo=archivo.name, actualizar=actualizar)
    elif request.method == 'POST' and accion == 'confirmar':
        pendiente = request.session.pop('carga_masiva', None)
        if not pendiente or pendiente['tipo'] != tipo:
            messages.error(request, 'Vuelva a validar el archivo antes de cargarlo.')
        else:
            try:
                import base64
                adjunto = (base64.b64decode(pendiente['datos']), pendiente['archivo']) if pendiente.get('datos') else None
                messages.success(request, f'Carga completada ({pendiente["archivo"]}): '
                                          f'{cm.cargar(tipo, pendiente["filas"], request.user, adjunto)}')
            except Exception as exc:  # p. ej. kardex cerrado o datos cambiados desde la validación
                messages.error(request, f'No se cargó nada: {exc}')
        return redirect(f"{request.path}?tipo={tipo}")
    return render(request, 'core/carga_masiva.html', ctx)


# ---------------------------------------------------------------- tipo de cambio
@login_required
def tipos_cambio(request):
    form = TipoCambioForm(request.POST or None, initial={'fecha': date.today()})
    if request.method == 'POST':
        if 'actualizar' in request.POST:
            dias = int(request.POST.get('dias') or 7)
            ok, fuentes = 0, set()
            for i in range(min(dias, 60)):
                f = date.today() - timedelta(days=i)
                valores = tipo_cambio.consultar_sunat(f)
                if valores:
                    fuente = valores[2] if len(valores) > 2 else 'SUNAT'
                    TipoCambio.objects.update_or_create(fecha=f, defaults={
                        'compra': valores[0], 'venta': valores[1], 'fuente': fuente})
                    fuentes.add(fuente)
                    ok += 1
            if ok:
                messages.success(request, f'{ok} días actualizados desde {" / ".join(sorted(fuentes))}.')
                if tipo_cambio.fuente_actual()[0] == 'SBS' and fuentes == {'SUNAT'}:
                    messages.warning(request, 'La SBS (Decolecta) no respondió: se usó SUNAT. Revise el token en '
                                              'Ajustes > Empresa.')
            else:
                messages.error(request, 'No se pudo consultar el tipo de cambio (sin conexión o servicio no '
                                        'disponible). Puede registrarlo manualmente.')
            return redirect('tipos_cambio')
        if form.is_valid():
            TipoCambio.objects.update_or_create(fecha=form.cleaned_data['fecha'], defaults={
                'compra': form.cleaned_data['compra'], 'venta': form.cleaned_data['venta'], 'fuente': 'MANUAL'})
            messages.success(request, 'Tipo de cambio guardado.')
            return redirect('tipos_cambio')
    return render(request, 'core/tipos_cambio.html', {
        'form': form, 'tipos': TipoCambio.objects.all()[:90], 'hoy': tipo_cambio.obtener(date.today()),
        'fuente': tipo_cambio.fuente_actual()[0]})


@login_required
def tipo_cambio_api(request):
    """JSON para autocompletar el T.C. en los formularios (?fecha=AAAA-MM-DD)."""
    try:
        fecha = date.fromisoformat(request.GET.get('fecha', ''))
    except ValueError:
        fecha = date.today()
    tc = tipo_cambio.obtener(fecha)
    if not tc:
        return JsonResponse({'ok': False, 'mensaje': 'Sin tipo de cambio registrado'}, status=404)
    return JsonResponse({'ok': True, 'fecha': tc.fecha.isoformat(), 'compra': str(tc.compra),
                         'venta': str(tc.venta), 'fuente': tc.fuente})


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
        ctx.update(titulo=self.titulo, columnas=self.get_columnas(), url_nuevo=self.url_nuevo,
                   url_editar=self.url_editar, q=self.request.GET.get('q', ''))
        return ctx

    def get_columnas(self):
        return self.columnas

    def get(self, request, *args, **kwargs):
        if request.GET.get('formato') == 'excel':  # la lista completa con los filtros aplicados
            from .utils import excel_response
            columnas = self.get_columnas()
            filas = []
            for obj in self.get_queryset():
                fila = []
                for _, campo in columnas:
                    v = obj
                    for parte in campo.split('.'):
                        v = getattr(v, parte, '')
                        v = v() if callable(v) else v
                    if isinstance(v, bool):
                        v = 'Sí' if v else 'No'
                    elif v is not None and not isinstance(v, (int, float, Decimal, str)):
                        v = str(v)
                    fila.append(v)
                filas.append(fila)
            return excel_response(self.titulo[:40], self.titulo, [c for c, _ in columnas], filas)
        return super().get(request, *args, **kwargs)


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
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Tipo', 'get_clase_display'), ('U.M.', 'unidad'),
                ('Precio', 'precio_referencia'), ('Costo prom.', 'costo_promedio'), ('Stock', 'stock'),
                ('Valorizado', 'valorizado')]
    url_nuevo, url_editar = 'producto_nuevo', 'producto_editar'
    buscar_en = ['codigo', 'nombre', 'marca', 'codigo_barras']
    template_name = 'core/productos.html'

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.GET.get('clase'):
            qs = qs.filter(clase=self.request.GET['clase'])
        return qs

    def get_columnas(self):
        from .modulos import puede_ver_costos
        if puede_ver_costos(self.request.user):
            return self.columnas
        return [c for c in self.columnas if c[1] not in ('costo_promedio', 'valorizado')]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['clases'] = Producto.CLASES
        return ctx


class ProductoNuevo(FormGenerico, CreateView):
    model, form_class, titulo = Producto, ProductoForm, 'Nuevo producto / servicio'
    template_name = 'core/producto_form.html'
    success_url = reverse_lazy('productos')

    def get_initial(self):
        return {'clase': self.request.GET.get('clase', 'MERCADERIA')}


class ProductoEditar(FormGenerico, UpdateView):
    model, form_class, titulo = Producto, ProductoForm, 'Editar producto / servicio'
    template_name = 'core/producto_form.html'
    success_url = reverse_lazy('productos')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['variantes'] = self.object.variantes.order_by('codigo') if self.object.es_plantilla else []
        return ctx


@login_required
def producto_imagen(request, pk):
    p = get_object_or_404(Producto.objects.select_related('imagen'), pk=pk)
    if p.imagen is None:
        raise Http404
    resp = HttpResponse(bytes(p.imagen.datos), content_type=p.imagen.tipo or 'application/octet-stream')
    resp['Cache-Control'] = 'private, max-age=3600'
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp


@login_required
def producto_variantes(request, pk):
    """Genera las variantes (combinaciones de atributos) de un producto plantilla."""
    from . import variantes
    p = get_object_or_404(Producto, pk=pk)
    motivo = variantes.puede_ser_plantilla(p)
    if request.method == 'POST' and not motivo:
        try:
            nuevas = variantes.generar(p, variantes.leer_atributos(request.POST.get('atributos')))
        except variantes.ErrorVariantes as exc:
            messages.error(request, str(exc))
        else:
            if nuevas:
                messages.success(request, f'{len(nuevas)} variantes creadas. Revise el precio y el código de barras '
                                          f'de cada una.')
            else:
                messages.info(request, 'Todas las combinaciones ya existían.')
            return redirect('producto_variantes', pk)
    nombres = {}
    for v in p.variantes.all():
        for k, val in v.atributos.items():
            nombres.setdefault(k, [])
            if val not in nombres[k]:
                nombres[k].append(val)
    sugerido = '\n'.join(f'{k}: {", ".join(vals)}' for k, vals in nombres.items()) or 'Talla: S, M, L\nColor: Negro, Blanco'
    return render(request, 'core/producto_variantes.html', {
        'p': p, 'motivo': motivo, 'variantes': p.variantes.order_by('codigo'), 'sugerido': sugerido,
        'atributos': list(nombres)})


@login_required
def ubigeos_json(request):
    """Departamentos, provincias y distritos para los selectores en cascada (datos INEI)."""
    from . import ubigeo
    resp = JsonResponse(ubigeo.arbol(), safe=False)
    resp['Cache-Control'] = 'private, max-age=86400'
    return resp


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
