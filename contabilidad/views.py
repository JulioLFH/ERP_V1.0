from collections import OrderedDict
from datetime import date
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView

from compras.models import Compra
from core.models import Empresa
from core.utils import elegidos, excel_response, fmt_fecha, txt_response
from core.views import FormGenerico, ListaGenerica
from finanzas.models import Movimiento
from ventas.models import Venta

from . import automatico, pcge, reportes
from .centralizar import ErrorContable, centralizar_periodo
from .forms import (AsientoForm, CentroBeneficioForm, CentroCostoForm, CuentaContableForm, CuentaDefectoFormSet,
                    lineas_formset)
from .models import (LIBROS, ORIGENES, Asiento, AsientoLinea, CentroBeneficio, CentroCosto, CuentaContable,
                     CuentaDefecto, PeriodoContable, norma_excluida, usar_norma)
from django.utils import timezone
from core.utils import id_o_none


def con_norma(vista):
    """Reportes de libros paralelos: ?norma=TRIBUTARIO lee el libro tributario (por defecto el NIIF, oficial)."""
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        with usar_norma(request.GET.get('norma', 'NIIF')):
            return vista(request, *args, **kwargs)
    return envoltura

D0 = Decimal('0')


def _ultimo_periodo():
    return (Asiento.objects.order_by('-periodo').values_list('periodo', flat=True).first()
            or timezone.localdate().strftime('%Y%m'))


def _periodo(request, clave='periodo', defecto=None):
    valor = (request.GET.get(clave) or '').replace('-', '')
    return valor if len(valor) == 6 and valor.isdigit() else (defecto or _ultimo_periodo())


def _mes_input(periodo):
    return f'{periodo[:4]}-{periodo[4:]}'


def al_dia(vista):
    """Antes de mostrar cualquier pantalla contable, centraliza los periodos con cambios pendientes."""
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if request.method == 'GET' and automatico.periodos_pendientes():
            for error in automatico.actualizar_pendientes(limite_segundos=8):
                messages.warning(request, error)
            quedan = automatico.periodos_pendientes()
            if quedan:
                messages.info(request, f'Contabilizando: faltan {len(quedan)} periodo(s) '
                                       f'({quedan[0][4:]}/{quedan[0][:4]} en adelante). Recargue la página en unos '
                                       f'segundos para continuar.')
        return vista(request, *args, **kwargs)
    return envoltura


# ---------------------------------------------------------------- periodos y centralización
@login_required
@al_dia
def periodos(request):
    if request.method == 'POST':
        periodo = request.POST.get('periodo', '')
        accion = request.POST.get('accion')
        if accion == 'apertura':
            a = automatico.generar_apertura()
            if a:
                messages.success(request, f'Asiento de apertura {a.numero} generado con los saldos iniciales de caja '
                                          'y bancos.')
            else:
                messages.info(request, 'Ninguna cuenta de caja o banco tiene saldo inicial.')
            return redirect('contabilidad:periodos')
        if accion == 'cerrar' and PeriodoContable.objects.filter(periodo=periodo, pendiente=True).exists():
            try:
                centralizar_periodo(periodo)  # se cierra con la contabilidad al día
            except ErrorContable as exc:
                messages.error(request, str(exc))
                return redirect('contabilidad:periodos')
        if accion == 'centralizar':
            try:
                r = centralizar_periodo(periodo)
                messages.success(request, f"Periodo {periodo} centralizado: {r['compras']} compras, {r['ventas']} "
                                          f"ventas, {r['tesoreria']} movimientos de caja/bancos"
                                          f"{', costo de ventas' if r['costo'] else ''}.")
                for e in r['errores']:
                    messages.warning(request, e)
            except ErrorContable as exc:
                messages.error(request, str(exc))
        elif accion == 'cerrar':
            PeriodoContable.objects.update_or_create(periodo=periodo, defaults={'cerrado': True})
            messages.success(request, f'Periodo {periodo} cerrado.')
        elif accion == 'abrir':
            # reapertura controlada: solo administrador, con motivo, queda en la auditoría
            motivo = request.POST.get('motivo', '').strip()
            p = PeriodoContable.objects.filter(periodo=periodo, cerrado=True).first()
            from core.permisos import perfil_de, puede
            # sin perfil configurado solo el administrador reabre (es una acción sensible)
            if not (request.user.is_superuser or (perfil_de(request.user) and
                                                  puede(request.user, 'contabilidad.reabrir'))):
                messages.error(request, 'Su usuario no tiene permiso para reabrir periodos cerrados.')
            elif len(motivo) < 10:
                messages.error(request, 'Indique el motivo de la reapertura (mínimo 10 caracteres).')
            elif p:
                from core.auditoria import registrar
                PeriodoContable.objects.filter(pk=p.pk).update(cerrado=False)
                registrar('MODIFICAR', p, {'Estado': ['Cerrado', 'Reabierto']}, motivo)
                messages.success(request, f'Periodo {periodo} reabierto. Ciérrelo nuevamente al terminar las '
                                          'correcciones.')
        return redirect('contabilidad:periodos')

    meses = set(Compra.objects.values_list('periodo', flat=True)) | set(Venta.objects.values_list('periodo', flat=True))
    meses |= {f.strftime('%Y%m') for f in Movimiento.objects.dates('fecha', 'month')}
    meses |= set(Asiento.objects.values_list('periodo', flat=True))
    meses |= set(PeriodoContable.objects.values_list('periodo', flat=True))  # cerrados aunque no tengan movimientos
    meses.add(timezone.localdate().strftime('%Y%m'))
    estado = {p.periodo: p for p in PeriodoContable.objects.all()}
    asientos = dict(Asiento.objects.values('periodo').annotate(n=Count('id')).values_list('periodo', 'n'))
    filas = []
    from .automatico import periodo_valido
    for p in sorted((m for m in meses if periodo_valido(m)), reverse=True):  # CON-17: «202613» no tumba la pantalla
        filas.append({
            'periodo': p, 'estado': estado.get(p), 'asientos': asientos.get(p, 0),
            'compras': Compra.objects.filter(periodo=p, estado='REGISTRADO').count(),
            'ventas': Venta.objects.filter(periodo=p, estado='REGISTRADO').count(),
            'movs': Movimiento.objects.filter(fecha__year=int(p[:4]), fecha__month=int(p[4:])).count(),
        })
    from core.permisos import perfil_de, puede
    return render(request, 'contabilidad/periodos.html', {
        'filas': filas, 'apertura': Asiento.objects.filter(origen='APERTURA').first(),
        'puede_reabrir': request.user.is_superuser or (perfil_de(request.user) is not None and
                                                       puede(request.user, 'contabilidad.reabrir'))})


# ---------------------------------------------------------------- asientos
@login_required
@al_dia
def asientos(request):
    # CON-13: el total con una subconsulta propia; con Sum('lineas__debe') el buscador por cuenta (que también une las
    # líneas) multiplicaba el importe por el número de líneas que coincidían
    from django.db.models import DecimalField, OuterRef, Subquery
    from django.db.models.functions import Coalesce
    total = (AsientoLinea.objects.filter(asiento=OuterRef('pk')).order_by().values('asiento')
             .annotate(s=Sum('debe')).values('s')[:1])
    qs = Asiento.objects.annotate(total=Coalesce(Subquery(total, output_field=DecimalField(max_digits=16,
                                                                                            decimal_places=2)),
                                                 Decimal('0'), output_field=DecimalField(max_digits=16,
                                                                                         decimal_places=2)))
    periodo = _periodo(request)
    qs = qs.filter(periodo=periodo)
    sel = {campo: elegidos(request, campo) for campo in ('libro', 'origen')}  # PLAT-13: varios valores
    for campo, valores in sel.items():
        if valores:
            qs = qs.filter(**{f'{campo}__in': valores})
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(glosa__icontains=q) | Q(numero__icontains=q) | Q(lineas__cuenta__codigo__startswith=q)).distinct()
    return render(request, 'contabilidad/asientos.html', {
        'page_obj': Paginator(qs.order_by('fecha', 'libro', 'numero'), 50).get_page(request.GET.get('page')),
        'periodo': periodo, 'mes': _mes_input(periodo), 'libros': LIBROS, 'origenes': ORIGENES, 'q': q, 'sel': sel,
        'cerrado': PeriodoContable.esta_cerrado(periodo)})


@login_required
@al_dia
def asiento_detalle(request, pk):
    a = get_object_or_404(Asiento, pk=pk)
    lineas = a.lineas.select_related('cuenta', 'tercero', 'centro_costo')
    d, h = a.totales
    from finanzas.rendiciones import sustentos_de_asiento
    return render(request, 'contabilidad/asiento_detalle.html', {
        'a': a, 'lineas': lineas, 'debe': d, 'haber': h, 'cerrado': PeriodoContable.esta_cerrado(a.periodo),
        'sustentos_origen': sustentos_de_asiento(a)})


def _cuentas_en_dolares(formset):
    return [f.cleaned_data['cuenta'] for f in formset.forms if f.cleaned_data and not f.cleaned_data.get('DELETE')
            and f.cleaned_data.get('cuenta') and f.cleaned_data['cuenta'].moneda == 'USD']


def _tipo_cambio_ok(form, formset):
    """Las cuentas en dólares necesitan el tipo de cambio del asiento para guardar su importe en US$."""
    dolares = _cuentas_en_dolares(formset)
    if dolares and (form.cleaned_data.get('tipo_cambio') or 0) <= 1:
        form.add_error('tipo_cambio', f'La cuenta {dolares[0].codigo} es en dólares: indique el tipo de cambio del '
                                      f'asiento para registrar su importe en US$.')
        return False
    return True


def cuentas_de_tesoreria():
    """Cuentas contables de caja y bancos: las 101/104/106 y las vinculadas a una cuenta de Tesorería."""
    from finanzas.models import Cuenta
    vinculadas = set(Cuenta.objects.exclude(cuenta_contable=None).values_list('cuenta_contable_id', flat=True))
    return vinculadas


def _bancos_ok(request, form, formset):
    """CON-21: el asiento manual no mueve caja ni bancos (eso es Tesorería y la conciliación), salvo que el usuario
    tenga «Asientos manuales a caja y bancos» y explique el motivo en la glosa (queda marcado en la conciliación)."""
    from core.permisos import puede
    vinculadas = cuentas_de_tesoreria()
    tocadas = [f.cleaned_data['cuenta'] for f in formset.forms
               if getattr(f, 'cleaned_data', None) and not f.cleaned_data.get('DELETE') and f.cleaned_data.get('cuenta')
               and (f.cleaned_data['cuenta'].codigo[:3] in ('101', '104', '106')
                    or f.cleaned_data['cuenta'].pk in vinculadas)]
    if not tocadas:
        return True
    codigos = ', '.join(sorted({c.codigo for c in tocadas}))
    if not puede(request.user, 'contabilidad.bancos'):
        form.add_error(None, f'El asiento usa cuentas de caja o bancos ({codigos}): esos movimientos se registran en '
                             f'Tesorería (y se concilian con el banco). Para un ajuste contable a bancos se necesita el '
                             f'permiso «Asientos manuales a caja y bancos».')
        return False
    if len((form.cleaned_data.get('glosa') or '').strip()) < 15:
        form.add_error('glosa', f'Explique en la glosa por qué el asiento mueve caja o bancos ({codigos}).')
        return False
    return True


def _importes_en_dolares(asiento):
    """Líneas de cuentas en dólares: importe en US$ = importe en soles ÷ tipo de cambio del asiento."""
    if not asiento.tipo_cambio or asiento.tipo_cambio <= 1:
        return
    for linea in asiento.lineas.filter(cuenta__moneda='USD'):
        linea.debe_me = (linea.debe / asiento.tipo_cambio).quantize(Decimal('0.01'))
        linea.haber_me = (linea.haber / asiento.tipo_cambio).quantize(Decimal('0.01'))
        linea.save(update_fields=['debe_me', 'haber_me'])


@login_required
def asiento_nuevo(request):
    """Asiento manual con sustento obligatorio. No se edita ni elimina: se corrige con un extorno."""
    from core.sustentos import adjuntar
    asiento = Asiento(libro='05')
    form = AsientoForm(request.POST or None, request.FILES or None, instance=asiento)
    formset = lineas_formset(extra=4)(request.POST or None, instance=asiento)
    if request.method == 'POST' and form.is_valid() and formset.is_valid() and _tipo_cambio_ok(form, formset) \
            and _bancos_ok(request, form, formset):
        with transaction.atomic():
            a = form.save(commit=False)
            a.origen, a.creado_por = 'MANUAL', request.user
            a.save()
            formset.instance = a
            formset.save()
            a.lineas.update(norma=a.norma)
            _importes_en_dolares(a)
            adjuntar(a, form.cleaned_data['sustento'], request.user, form.cleaned_data.get('descripcion_sustento', ''))
        messages.success(request, f'Asiento {a.numero} registrado con su sustento.')
        return redirect('contabilidad:asiento_detalle', a.pk)
    cuentas = list(CuentaContable.objects.filter(imputable=True, activo=True).values('id', 'codigo', 'nombre'))
    return render(request, 'contabilidad/asiento_form.html', {'form': form, 'formset': formset,
                                                              'titulo': 'Nuevo asiento manual', 'cuentas': cuentas})


@login_required
def asiento_editar(request, pk):
    messages.error(request, 'Los asientos no se editan: si hay un error, extórnelo (asiento inverso con motivo) y '
                            'registre el asiento correcto.')
    return redirect('contabilidad:asiento_detalle', pk)


@login_required
def asiento_eliminar(request, pk):
    messages.error(request, 'Los asientos no se eliminan: use "Extornar" para anular su efecto con un asiento inverso.')
    return redirect('contabilidad:asiento_detalle', pk)


@login_required
def asiento_extornar(request, pk):
    """Asiento inverso (debe <-> haber) con motivo: anula el efecto de un asiento manual sin borrarlo."""
    from core.auditoria import registrar
    from core.sustentos import adjuntar
    from .forms import ExtornoForm
    a = get_object_or_404(Asiento, pk=pk)
    if a.origen != 'MANUAL' or a.extorna_id or a.extornado:
        messages.error(request, 'Solo se extornan asientos manuales que no son extornos ni fueron extornados. Los '
                                'automáticos se corrigen en su documento de origen.')
        return redirect('contabilidad:asiento_detalle', pk)
    form = ExtornoForm(request.POST or None, request.FILES or None,
                       initial={'fecha': a.fecha if not PeriodoContable.esta_cerrado(a.periodo) else timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            ext = Asiento.objects.create(fecha=form.cleaned_data['fecha'], libro=a.libro, origen='MANUAL', extorna=a,
                                         moneda=a.moneda, tipo_cambio=a.tipo_cambio, creado_por=request.user,
                                         norma=a.norma,
                                         glosa=f'Extorno de {a.numero}: {form.cleaned_data["motivo"]}'[:250])
            AsientoLinea.objects.bulk_create([AsientoLinea(
                asiento=ext, cuenta=l.cuenta, tercero=l.tercero, centro_costo=l.centro_costo, documento=l.documento,
                glosa=l.glosa, debe=l.haber, haber=l.debe, debe_me=l.haber_me, haber_me=l.debe_me,
                es_destino=l.es_destino, norma=l.norma) for l in a.lineas.all()])
            if form.cleaned_data.get('sustento'):
                adjuntar(ext, form.cleaned_data['sustento'], request.user, 'Sustento del extorno')
            registrar('EXTORNAR', a, {'extorno': ext.numero}, form.cleaned_data['motivo'])
        messages.success(request, f'Asiento {a.numero} extornado con el asiento {ext.numero}.')
        return redirect('contabilidad:asiento_detalle', ext.pk)
    return render(request, 'core/form.html', {'form': form, 'titulo': f'Extornar asiento {a.numero}'})


# ---------------------------------------------------------------- libros
@login_required
@al_dia
@con_norma
def libro_diario(request):
    periodo = _periodo(request)
    asientos_qs = (Asiento.objects.filter(periodo=periodo).exclude(norma=norma_excluida())
                   .order_by('fecha', 'libro', 'numero')
                   .prefetch_related('lineas__cuenta', 'lineas__tercero', 'lineas__centro_costo'))
    formato = request.GET.get('formato')
    if formato == 'ple':
        return _diario_ple(periodo, asientos_qs)
    if formato == 'excel':
        filas = []
        for a in asientos_qs:
            for l in a.lineas.all():
                filas.append([a.numero, fmt_fecha(a.fecha), a.get_libro_display(), a.glosa, l.cuenta.codigo,
                              l.cuenta.nombre, l.tercero.numero_doc if l.tercero else '', l.documento,
                              l.centro_costo.codigo if l.centro_costo else '', l.debe, l.haber])
        empresa = Empresa.actual()
        return excel_response(f'Libro_diario_{periodo}', f'LIBRO DIARIO {periodo} - {empresa.razon_social} RUC {empresa.ruc}',
                              ['Asiento', 'Fecha', 'Libro', 'Glosa', 'Cuenta', 'Denominación', 'RUC/DNI', 'Documento',
                               'C. costo', 'Debe', 'Haber'], filas)
    tot = AsientoLinea.objects.filter(asiento__periodo=periodo).aggregate(d=Sum('debe'), h=Sum('haber'))
    pagina = Paginator(asientos_qs, 60).get_page(request.GET.get('page'))
    return render(request, 'contabilidad/diario.html', {
        'asientos': pagina, 'page_obj': pagina, 'periodo': periodo, 'mes': _mes_input(periodo),
        'debe': tot['d'] or D0, 'haber': tot['h'] or D0})


def _linea_ple(periodo, a, n, l):
    """Campos comunes del Libro Diario 5.1 y del Libro Mayor 6.1 (misma estructura en el PLE)."""
    doc = a.documento
    tercero = l.tercero
    tipo_doc = serie = numero = ''
    if doc is not None and hasattr(doc, 'tipo_comprobante'):
        tipo_doc, serie, numero = doc.tipo_comprobante, doc.serie, doc.numero
    campos = [f'{periodo}00', a.numero.replace('-', ''), f'M{n}', l.cuenta.codigo, '',
              l.centro_costo.codigo if l.centro_costo else '', a.moneda,
              tercero.tipo_doc if tercero else '', tercero.numero_doc if tercero else '',
              tipo_doc or '00', serie, numero, fmt_fecha(a.fecha), '', fmt_fecha(a.fecha),
              (l.glosa or a.glosa)[:200].replace('|', ' '), '', f'{l.debe:.2f}', f'{l.haber:.2f}', '', '1']
    return '|'.join(campos) + '|'


def _diario_ple(periodo, asientos_qs):
    """Libro Diario formato 5.1 (PLE)."""
    lineas = []
    for a in asientos_qs:
        for n, l in enumerate(a.lineas.all(), 1):
            lineas.append(_linea_ple(periodo, a, n, l))
    empresa = Empresa.actual()
    indicador = '1' if lineas else '0'
    return txt_response(f'LE{empresa.ruc}{periodo}00050100001{indicador}11.txt', lineas)


def _mayor_ple(periodo):
    """Libro Mayor formato 6.1 (PLE): los movimientos del mes agrupados por cuenta."""
    asientos = (Asiento.objects.filter(periodo=periodo).exclude(norma=norma_excluida())
                .prefetch_related('lineas__cuenta', 'lineas__tercero', 'lineas__centro_costo'))
    movs = []
    for a in asientos:
        for n, l in enumerate(a.lineas.all(), 1):
            movs.append((l.cuenta.codigo, a.fecha, a.numero, n, a, l))
    lineas = [_linea_ple(periodo, a, n, l) for _, _, _, n, a, l in sorted(movs, key=lambda m: m[:4])]
    empresa = Empresa.actual()
    indicador = '1' if lineas else '0'
    return txt_response(f'LE{empresa.ruc}{periodo}00060100001{indicador}11.txt', lineas)


@login_required
@al_dia
@con_norma
def libro_mayor(request):
    cuentas = CuentaContable.objects.filter(imputable=True)
    cuenta = cuentas.filter(pk=id_o_none(request.GET.get('cuenta'))).first() if request.GET.get('cuenta') else None
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    if request.GET.get('formato') == 'ple':  # todas las cuentas del mes "hasta"
        return _mayor_ple(hasta)
    ctx = {'cuentas': cuentas, 'cuenta': cuenta, 'desde': desde, 'hasta': hasta,
           'mes_desde': _mes_input(desde), 'mes_hasta': _mes_input(hasta)}
    if cuenta:
        base = AsientoLinea.objects.filter(cuenta=cuenta).select_related('asiento', 'tercero')
        ant = base.filter(asiento__periodo__lt=desde).aggregate(d=Sum('debe'), h=Sum('haber'))
        saldo = (ant['d'] or D0) - (ant['h'] or D0)
        inicial = saldo
        filas = []
        for l in base.filter(asiento__periodo__gte=desde, asiento__periodo__lte=hasta).order_by('asiento__fecha', 'asiento__numero', 'id'):
            saldo += l.debe - l.haber
            filas.append({'l': l, 'saldo': saldo})
        if request.GET.get('formato') == 'excel':
            datos = [[fmt_fecha(f['l'].asiento.fecha), f['l'].asiento.numero, f['l'].glosa or f['l'].asiento.glosa,
                      f['l'].tercero.nombre if f['l'].tercero else '', f['l'].documento, f['l'].debe, f['l'].haber,
                      f['saldo']] for f in filas]
            datos.insert(0, ['', '', 'Saldo anterior', '', '', '', '', inicial])
            return excel_response(f'Mayor_{cuenta.codigo}', f'LIBRO MAYOR {cuenta} {desde}-{hasta}',
                                  ['Fecha', 'Asiento', 'Glosa', 'Tercero', 'Documento', 'Debe', 'Haber', 'Saldo'], datos)
        ctx.update(filas=filas, inicial=inicial, final=saldo,
                   debe=sum((f['l'].debe for f in filas), D0), haber=sum((f['l'].haber for f in filas), D0))
    return render(request, 'contabilidad/mayor.html', ctx)


@login_required
@al_dia
@con_norma
def balance(request):
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    nivel = int(request.GET.get('nivel') or 0) or None
    filas, tot = reportes.balance_comprobacion(desde, hasta, nivel)
    if request.GET.get('formato') == 'excel':
        datos = [[f['codigo'], f['nombre'], f['d'], f['h'], f['sd'], f['sa'], f.get('activo', D0), f.get('pasivo', D0),
                  f.get('nat_p', D0), f.get('nat_g', D0), f.get('fun_p', D0), f.get('fun_g', D0)] for f in filas]
        if tot['sin_destino']:
            datos.append(['', 'Gastos 62-68 sin destino a la clase 9', '', '', '', '', '', '', '', '',
                          tot['sin_destino_p'], tot['sin_destino_g']])
        datos.append(['', 'TOTALES', tot['d'], tot['h'], tot['sd'], tot['sa'], tot['activo'], tot['pasivo'],
                      tot['nat_p'], tot['nat_g'], tot['fun_p'], tot['fun_g']])
        return excel_response(f'Balance_comprobacion_{desde}_{hasta}', f'BALANCE DE COMPROBACIÓN {desde} - {hasta}',
                              ['Cuenta', 'Denominación', 'Debe', 'Haber', 'Deudor', 'Acreedor', 'Activo',
                               'Pasivo y patrimonio', 'Pérdidas (naturaleza)', 'Ganancias (naturaleza)',
                               'Pérdidas (función)', 'Ganancias (función)'], datos)
    return render(request, 'contabilidad/balance.html', {
        'filas': filas, 'tot': tot, 'desde': desde, 'hasta': hasta, 'nivel': nivel or 0,
        'mes_desde': _mes_input(desde), 'mes_hasta': _mes_input(hasta)})


@login_required
@al_dia
@con_norma
def estado_situacion(request):
    hasta = _periodo(request, 'hasta')
    comparar = request.GET.get('comparar') if request.GET.get('comparar') in ('anio', 'previo') else ''
    datos = reportes.situacion_financiera(hasta, f'{hasta[:4]}01')
    ctx = {**datos, 'hasta': hasta, 'mes_hasta': _mes_input(hasta), 'comparar': comparar,
           'opciones': [('anio', 'Cierre del año anterior'), ('previo', 'Mes anterior')]}
    if comparar or request.GET.get('formato') == 'excel':
        secciones, comp = reportes.situacion_comparativa(hasta, comparar or 'anio')
        ctx.update(secciones=secciones, comp=comp)
        if request.GET.get('formato') == 'excel':
            datos_xls = []
            for s in secciones:
                datos_xls += [['', f['nombre'], f['valor'], f['comp'], f['var']] for f in s['filas']]
                datos_xls.append([s['titulo'].upper() if s['filas'] else s['titulo'], '', s['total']['valor'],
                                  s['total']['comp'], s['total']['var']])
            return excel_response(f'Situacion_financiera_{hasta}', f'ESTADO DE SITUACIÓN FINANCIERA AL {hasta}',
                                  ['Sección', 'Rubro', _periodo_txt(hasta), _periodo_txt(comp), 'Variación'],
                                  datos_xls)
    return render(request, 'contabilidad/situacion.html', ctx)


def _periodo_txt(p):
    return f'{p[4:]}/{p[:4]}'


@login_required
@al_dia
@con_norma
def estado_resultados(request):
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    comparar = request.GET.get('comparar') if request.GET.get('comparar') in ('anio', 'previo', 'presupuesto',
                                                                               'meses') else ''
    vista = 'naturaleza' if request.GET.get('vista') == 'naturaleza' else 'funcion'
    calcular = reportes.estado_resultados_naturaleza if vista == 'naturaleza' else reportes.estado_resultados
    lineas, neta = calcular(desde, hasta)
    detalle = reportes.detalle_resultados(desde, hasta, vista)  # un nivel más: las cuentas de cada línea
    ctx = {'lineas': lineas, 'neta': neta, 'desde': desde, 'hasta': hasta, 'comparar': comparar, 'vista': vista,
           'lineas_det': [(n, v, e, detalle.get(n, [])) for n, v, e in lineas],
           'mes_desde': _mes_input(desde), 'mes_hasta': _mes_input(hasta),
           'opciones': [('anio', 'Mismo periodo del año anterior'), ('previo', 'Periodo anterior'),
                        ('presupuesto', 'Presupuesto')]}
    if comparar == 'meses':  # desglose: una columna por mes y el total
        meses, mensual = reportes.resultados_mensual(desde, hasta, vista)
        ctx.update(meses=[_periodo_txt(p) for p in meses], mensual=mensual)
        if request.GET.get('formato') == 'excel':
            return excel_response(
                f'Estado_resultados_mensual_{desde}_{hasta}',
                f'ESTADO DE RESULTADOS POR {vista.upper()} · MENSUAL {_periodo_txt(desde)} - {_periodo_txt(hasta)}',
                ['Concepto'] + ctx['meses'] + ['Total'],
                [fila for f in mensual for fila in ([[f['nombre']] + f['valores'] + [f['total']]] +
                                                    [[f'    {c["codigo"]} {c["nombre"]}'] + c['valores'] + [c['total']]
                                                     for c in f.get('cuentas', [])])])
        return render(request, 'contabilidad/resultados.html', ctx)
    if comparar and vista == 'funcion':
        filas, rango = reportes.resultados_comparativo(desde, hasta, comparar)
        etiqueta = 'Presupuesto' if comparar == 'presupuesto' else \
            f'{_periodo_txt(rango[0])} - {_periodo_txt(rango[1])}'
        ctx.update(filas=filas, etiqueta_comp=etiqueta)
        if comparar == 'presupuesto':
            from .models import Presupuesto
            ctx['sin_presupuesto'] = not any(Presupuesto.del_anio(a) for a in range(int(desde[:4]), int(hasta[:4]) + 1))
    if request.GET.get('formato') == 'excel':
        actual = f'{_periodo_txt(desde)} - {_periodo_txt(hasta)}'
        if comparar and 'filas' in ctx:
            datos_xls = [[f['nombre'], f['valor'], f['comp'], f['var'], f['pct']] for f in ctx['filas']]
            encabezados = ['Concepto', actual, ctx['etiqueta_comp'], 'Variación', 'Variación %']
        else:
            datos_xls = [fila for n, v, _, cuentas in ctx['lineas_det']
                         for fila in [[n, v]] + [[f'    {c["codigo"]} {c["nombre"]}', c['importe']] for c in cuentas]]
            encabezados = ['Concepto', actual]
        return excel_response(f'Estado_resultados_{desde}_{hasta}', f'ESTADO DE RESULTADOS {actual}',
                              encabezados, datos_xls)
    return render(request, 'contabilidad/resultados.html', ctx)


@login_required
@al_dia
def centros_costo_reporte(request):
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    # gastos por naturaleza (61 a 68); los destinos (clase 9) en su propia vista. Las compras (60), el costo de
    # ventas (69) y las cuentas de balance no son gasto del área: el activo se ve en su depreciación y los
    # suministros cuando salen del almacén
    # vista: 'gasto' (61 a 68), 'destino' (clase 9) o 'todos' (las dos, en columnas separadas: un gasto y su destino
    # son el mismo importe visto dos veces, por eso no se suman)
    vista = request.GET.get('vista') if request.GET.get('vista') in ('destino', 'todos') else 'gasto'
    base = reportes.lineas_rango(desde, hasta)
    gasto_q = Q(es_destino=False, cuenta__codigo__regex=r'^6[1-8]')
    destino_q = Q(cuenta__codigo__regex=r'^9')
    base = base.filter({'gasto': gasto_q, 'destino': destino_q}.get(vista, gasto_q | destino_q))
    filas = (base
             .values('centro_costo__codigo', 'centro_costo__nombre', 'cuenta__codigo', 'cuenta__nombre')
             .annotate(d=Sum('debe'), h=Sum('haber')).order_by('centro_costo__codigo', 'cuenta__codigo'))
    propios = {}
    for f in filas:
        clave = f['centro_costo__codigo'] or '—'
        g = propios.setdefault(clave, {'filas': [], 'gasto': D0, 'destino': D0})
        neto = (f['d'] or D0) - (f['h'] or D0)
        clase = 'destino' if f['cuenta__codigo'].startswith('9') else 'gasto'
        g['filas'].append({'codigo': f['cuenta__codigo'], 'nombre': f['cuenta__nombre'], 'importe': neto,
                           'clase': clase})
        g[clase] += neto
    # árbol: cada centro con lo propio y el acumulado de los que dependen de él
    centros = list(CentroCosto.objects.select_related('padre'))
    hijos = {}
    for c in centros:
        hijos.setdefault(c.padre_id, []).append(c)
    vacio = {'gasto': D0, 'destino': D0}

    def acumulado(c, clase, n=0):
        return propios.get(c.codigo, vacio)[clase] + sum(
            (acumulado(h, clase, n + 1) for h in hijos.get(c.pk, []) if n < 20), D0)

    grupos = OrderedDict()

    def recorrer(padre_id, nivel):
        for c in sorted(hijos.get(padre_id, []), key=lambda x: x.codigo):
            acum_g, acum_d = acumulado(c, 'gasto'), acumulado(c, 'destino')
            if acum_g or acum_d or c.codigo in propios:
                propio = propios.get(c.codigo, vacio)
                grupos[c.codigo] = {'nombre': c.nombre, 'tipo': c.get_tipo_display(), 'nivel': nivel,
                                    'filas': propios.get(c.codigo, {}).get('filas', []),
                                    'gasto': propio['gasto'], 'destino': propio['destino'],
                                    'acum_gasto': acum_g, 'acum_destino': acum_d,
                                    'total': propio['gasto'] + propio['destino'],
                                    'acumulado': acum_g + acum_d, 'tiene_hijos': bool(hijos.get(c.pk))}
            if nivel < 20:
                recorrer(c.pk, nivel + 1)
    recorrer(None, 0)
    if '—' in propios:
        s = propios['—']
        grupos['—'] = {'nombre': 'Sin centro de costo', 'tipo': '', 'nivel': 0, 'filas': s['filas'],
                       'gasto': s['gasto'], 'destino': s['destino'], 'acum_gasto': s['gasto'],
                       'acum_destino': s['destino'], 'total': s['gasto'] + s['destino'],
                       'acumulado': s['gasto'] + s['destino'], 'tiene_hijos': False}
    total_gasto = sum((g['gasto'] for g in propios.values()), D0)
    total_destino = sum((g['destino'] for g in propios.values()), D0)
    return render(request, 'contabilidad/centros_reporte.html', {
        'grupos': grupos, 'desde': desde, 'hasta': hasta, 'mes_desde': _mes_input(desde), 'vista': vista,
        'mes_hasta': _mes_input(hasta), 'total_gasto': total_gasto, 'total_destino': total_destino,
        'total': total_gasto + total_destino})



@login_required
@al_dia
def resultados_por_linea(request):
    """Estado de resultados por centro de beneficio (línea de negocio), con la cuenta por cobrar y el inventario
    de cada línea al cierre del rango."""
    from core.inventario import valor_inventario
    from ventas.models import Venta
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    lineas = list(CentroBeneficio.objects.filter(activo=True))
    columnas = [(cb.pk, cb.nombre) for cb in lineas] + [(None, 'Sin línea / producción en curso')]
    # cada línea se calcula igual que el estado de resultados por función, con sus propias cuentas, así el total es
    # exactamente el del estado oficial: ventas 70/74, costo 69 + 60/61 + producción (9x) − 71/72, gastos por destino
    por_linea = {pk: {} for pk, _ in columnas}
    for f in (reportes.lineas_rango(desde, hasta).values('centro_beneficio', 'cuenta__codigo')
              .annotate(d=Sum('debe'), h=Sum('haber'))):
        pk = f['centro_beneficio'] if f['centro_beneficio'] in por_linea else None
        d, h = por_linea[pk].get(f['cuenta__codigo'], (D0, D0))
        por_linea[pk][f['cuenta__codigo']] = (d + (f['d'] or D0), h + (f['h'] or D0))
    calculo = {pk: {n: v for n, v, _ in reportes.estado_resultados(desde, hasta, sumas=s)[0]}
               for pk, s in por_linea.items()}
    oficial, _ = reportes.estado_resultados(desde, hasta)
    # en cada línea el costo de ventas es el costo de lo vendido (69). Lo comprado, consumido y producido en el
    # periodo (60/61, destinos de producción 9x y producción almacenada 71/72) no se identifica con una línea hasta
    # que se vende: esa diferencia pasa a «Sin línea», así el margen por línea es confiable y el total no cambia
    no_absorbido = 'Producción en proceso y costos de producción no absorbidos'
    acumulados = [n for n, _, e in oficial[oficial.index(next(x for x in oficial if x[0] == 'Costo de ventas')) + 1:]
                  if e in ('subtotal', 'total')]
    for pk in por_linea:
        if pk is None:
            continue
        delta = calculo[pk].pop(no_absorbido, D0)  # en la línea queda solo su costo de lo vendido (69)
        if delta:
            calculo[None][no_absorbido] = calculo[None].get(no_absorbido, D0) + delta
            for n in acumulados:
                calculo[pk][n] = calculo[pk].get(n, D0) - delta
                calculo[None][n] = calculo[None].get(n, D0) + delta
    filas = [(n, {pk: calculo[pk].get(n, D0) for pk, _ in columnas}, e in ('subtotal', 'total'), v)
             for n, v, e in oficial]
    # cuentas por cobrar e inventario por línea al cierre
    from contabilidad.centralizar import _fin_mes
    corte = _fin_mes(hasta)
    cxc = {pk: D0 for pk, _ in columnas}
    for v in (Venta.objects.con_saldos().cobrables().filter(estado='REGISTRADO', fecha_emision__lte=corte)
              .exclude(tipo_comprobante__in=['07', '08']).prefetch_related('items__producto')):
        saldo = v.saldo_pen
        if saldo <= 0:
            continue
        pesos = {}
        for i in v.items.all():
            pk = i.producto.centro_beneficio_id if i.producto and i.producto.centro_beneficio_id in cxc else None
            pesos[pk] = pesos.get(pk, D0) + i.subtotal
        total = sum(pesos.values(), D0) or Decimal('1')
        for pk, peso in pesos.items():
            cxc[pk] += saldo * peso / total
    inventario = {pk: D0 for pk, _ in columnas}
    for fila in valor_inventario(corte)[0]:
        pk = fila['p'].centro_beneficio_id if fila['p'].centro_beneficio_id in inventario else None
        inventario[pk] += fila['valor']
    filas += [('Cuentas por cobrar al cierre', cxc, False, sum(cxc.values(), D0)),
              ('Inventario al cierre', inventario, False, sum(inventario.values(), D0))]
    tabla = [{'rubro': r, 'valores': [v[pk] for pk, _ in columnas], 'total': total, 'fuerte': fuerte}
             for r, v, fuerte, total in filas]
    if request.GET.get('formato') == 'excel':
        from core.utils import excel_response
        return excel_response(f'Resultados_por_linea_{desde}_{hasta}', f'Estado de resultados por línea {desde}-{hasta}',
                              ['Rubro'] + [n for _, n in columnas] + ['Total'],
                              [[t['rubro']] + t['valores'] + [t['total']] for t in tabla])
    return render(request, 'contabilidad/resultados_linea.html', {
        'columnas': columnas, 'tabla': tabla, 'desde': desde, 'hasta': hasta, 'mes_desde': _mes_input(desde),
        'mes_hasta': _mes_input(hasta), 'sin_lineas': not lineas})


# ---------------------------------------------------------------- plan de cuentas y configuración
@login_required
def plan_cuentas(request):
    if request.method == 'POST' and request.POST.get('accion') == 'cargar_pcge':
        antes = CuentaContable.objects.count()
        pcge.cargar(CuentaContable, CuentaDefecto)
        messages.success(request, f'Plan contable actualizado: {CuentaContable.objects.count() - antes} cuentas nuevas.')
        return redirect('contabilidad:plan')
    qs = CuentaContable.objects.select_related('destino_debe', 'destino_haber')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(codigo__startswith=q) | Q(nombre__icontains=q))
    if request.GET.get('clase'):
        qs = qs.filter(codigo__startswith=request.GET['clase'])
    if request.GET.get('formato') == 'ple':
        empresa = Empresa.actual()
        periodo = timezone.localdate().strftime('%Y%m')
        lineas = [f'{periodo}00|{c.codigo}|{c.nombre}|01|PLAN CONTABLE GENERAL EMPRESARIAL|||1|' for c in qs]
        return txt_response(f'LE{empresa.ruc}{periodo}00050300001111.txt', lineas)
    return render(request, 'contabilidad/plan.html', {'cuentas': qs, 'q': q, 'clase': request.GET.get('clase', ''),
                                                      'plan_contable': Empresa.actual().get_plan_contable_display()})


class CuentaNueva(FormGenerico, CreateView):
    model, form_class, titulo = CuentaContable, CuentaContableForm, 'Nueva cuenta contable'
    success_url = reverse_lazy('contabilidad:plan')


class CuentaEditar(FormGenerico, UpdateView):
    model, form_class, titulo = CuentaContable, CuentaContableForm, 'Editar cuenta contable'
    success_url = reverse_lazy('contabilidad:plan')


@login_required
def configuracion(request):
    if not CuentaDefecto.objects.exists():
        pcge.cargar(CuentaContable, CuentaDefecto)
    formset = CuentaDefectoFormSet(request.POST or None, queryset=CuentaDefecto.objects.select_related('cuenta'))
    if request.method == 'POST' and formset.is_valid():
        formset.save()
        messages.success(request, 'Configuración contable guardada. Vuelva a centralizar los periodos abiertos '
                                  'para aplicar los cambios.')
        return redirect('contabilidad:configuracion')
    return render(request, 'contabilidad/configuracion.html', {'formset': formset})


class CentroCostoLista(ListaGenerica):
    model = CentroCosto
    titulo = 'Centros de costo'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Tipo', 'get_tipo_display'), ('Depende de', 'padre'),
                ('Centro de beneficio', 'beneficio'), ('Responsable', 'responsable'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'contabilidad:cc_nuevo', 'contabilidad:cc_editar'
    buscar_en = ['codigo', 'nombre', 'responsable']

    def get_queryset(self):
        return super().get_queryset().select_related('padre', 'centro_beneficio')


class CentroBeneficioLista(ListaGenerica):
    model = CentroBeneficio
    titulo = 'Centros de beneficio (líneas de negocio)'
    columnas = [('Código', 'codigo'), ('Nombre', 'nombre'), ('Responsable', 'responsable'), ('Activo', 'activo')]
    url_nuevo, url_editar = 'contabilidad:cb_nuevo', 'contabilidad:cb_editar'
    buscar_en = ['codigo', 'nombre']


class CentroBeneficioNuevo(FormGenerico, CreateView):
    model, form_class, titulo = CentroBeneficio, CentroBeneficioForm, 'Nuevo centro de beneficio'
    success_url = reverse_lazy('contabilidad:beneficios')


class CentroBeneficioEditar(FormGenerico, UpdateView):
    model, form_class, titulo = CentroBeneficio, CentroBeneficioForm, 'Editar centro de beneficio'
    success_url = reverse_lazy('contabilidad:beneficios')


class CentroCostoNuevo(FormGenerico, CreateView):
    model, form_class, titulo = CentroCosto, CentroCostoForm, 'Nuevo centro de costo'
    success_url = reverse_lazy('contabilidad:centros')


class CentroCostoEditar(FormGenerico, UpdateView):
    model, form_class, titulo = CentroCosto, CentroCostoForm, 'Editar centro de costo'
    success_url = reverse_lazy('contabilidad:centros')
