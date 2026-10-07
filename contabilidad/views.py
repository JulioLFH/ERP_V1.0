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
from core.utils import excel_response, fmt_fecha, txt_response
from core.views import FormGenerico, ListaGenerica
from finanzas.models import Movimiento
from ventas.models import Venta

from . import automatico, pcge, reportes
from .centralizar import ErrorContable, centralizar_periodo
from .forms import (AsientoForm, CentroBeneficioForm, CentroCostoForm, CuentaContableForm, CuentaDefectoFormSet,
                    lineas_formset)
from .models import (LIBROS, ORIGENES, Asiento, AsientoLinea, CentroBeneficio, CentroCosto, CuentaContable,
                     CuentaDefecto, PeriodoContable, norma_excluida, usar_norma)


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
            or date.today().strftime('%Y%m'))


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
    meses.add(date.today().strftime('%Y%m'))
    estado = {p.periodo: p for p in PeriodoContable.objects.all()}
    asientos = dict(Asiento.objects.values('periodo').annotate(n=Count('id')).values_list('periodo', 'n'))
    filas = []
    for p in sorted((m for m in meses if m), reverse=True):
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
    qs = Asiento.objects.annotate(total=Sum('lineas__debe'))
    periodo = _periodo(request)
    qs = qs.filter(periodo=periodo)
    for campo in ('libro', 'origen'):
        if request.GET.get(campo):
            qs = qs.filter(**{campo: request.GET[campo]})
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(glosa__icontains=q) | Q(numero__icontains=q) | Q(lineas__cuenta__codigo__startswith=q)).distinct()
    return render(request, 'contabilidad/asientos.html', {
        'page_obj': Paginator(qs.order_by('fecha', 'libro', 'numero'), 50).get_page(request.GET.get('page')),
        'periodo': periodo, 'mes': _mes_input(periodo), 'libros': LIBROS, 'origenes': ORIGENES, 'q': q,
        'cerrado': PeriodoContable.esta_cerrado(periodo)})


@login_required
@al_dia
def asiento_detalle(request, pk):
    a = get_object_or_404(Asiento, pk=pk)
    lineas = a.lineas.select_related('cuenta', 'tercero', 'centro_costo')
    d, h = a.totales
    return render(request, 'contabilidad/asiento_detalle.html', {
        'a': a, 'lineas': lineas, 'debe': d, 'haber': h, 'cerrado': PeriodoContable.esta_cerrado(a.periodo)})


@login_required
def asiento_nuevo(request):
    """Asiento manual con sustento obligatorio. No se edita ni elimina: se corrige con un extorno."""
    from core.sustentos import adjuntar
    asiento = Asiento(libro='05')
    form = AsientoForm(request.POST or None, request.FILES or None, instance=asiento)
    formset = lineas_formset(extra=4)(request.POST or None, instance=asiento)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            a = form.save(commit=False)
            a.origen, a.creado_por = 'MANUAL', request.user
            a.save()
            formset.instance = a
            formset.save()
            a.lineas.update(norma=a.norma)
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
                       initial={'fecha': a.fecha if not PeriodoContable.esta_cerrado(a.periodo) else date.today()})
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
    cuenta = cuentas.filter(pk=request.GET.get('cuenta')).first() if request.GET.get('cuenta') else None
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
    ctx = {'lineas': lineas, 'neta': neta, 'desde': desde, 'hasta': hasta, 'comparar': comparar, 'vista': vista,
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
                [[f['nombre']] + f['valores'] + [f['total']] for f in mensual])
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
            datos_xls = [[n, v] for n, v, _ in lineas]
            encabezados = ['Concepto', actual]
        return excel_response(f'Estado_resultados_{desde}_{hasta}', f'ESTADO DE RESULTADOS {actual}',
                              encabezados, datos_xls)
    return render(request, 'contabilidad/resultados.html', ctx)


@login_required
@al_dia
def centros_costo_reporte(request):
    hasta = _periodo(request, 'hasta')
    desde = _periodo(request, 'desde', defecto=f'{hasta[:4]}01')
    filas = (reportes.lineas_rango(desde, hasta).filter(es_destino=False, cuenta__codigo__regex=r'^(6|3)')
             .values('centro_costo__codigo', 'centro_costo__nombre', 'cuenta__codigo', 'cuenta__nombre')
             .annotate(d=Sum('debe'), h=Sum('haber')).order_by('centro_costo__codigo', 'cuenta__codigo'))
    propios = {}
    for f in filas:
        clave = f['centro_costo__codigo'] or '—'
        g = propios.setdefault(clave, {'filas': [], 'total': D0})
        neto = (f['d'] or D0) - (f['h'] or D0)
        g['filas'].append({'codigo': f['cuenta__codigo'], 'nombre': f['cuenta__nombre'], 'importe': neto})
        g['total'] += neto
    # árbol: cada centro con su gasto propio y el acumulado de los que dependen de él
    centros = list(CentroCosto.objects.select_related('padre'))
    hijos = {}
    for c in centros:
        hijos.setdefault(c.padre_id, []).append(c)

    def acumulado(c, n=0):
        return propios.get(c.codigo, {'total': D0})['total'] + sum(
            (acumulado(h, n + 1) for h in hijos.get(c.pk, []) if n < 20), D0)

    grupos = OrderedDict()

    def recorrer(padre_id, nivel):
        for c in sorted(hijos.get(padre_id, []), key=lambda x: x.codigo):
            total = acumulado(c)
            if total or c.codigo in propios:
                grupos[c.codigo] = {'nombre': c.nombre, 'tipo': c.get_tipo_display(), 'nivel': nivel,
                                    'filas': propios.get(c.codigo, {}).get('filas', []),
                                    'total': propios.get(c.codigo, {'total': D0})['total'], 'acumulado': total,
                                    'tiene_hijos': bool(hijos.get(c.pk))}
            if nivel < 20:
                recorrer(c.pk, nivel + 1)
    recorrer(None, 0)
    if '—' in propios:
        grupos['—'] = {'nombre': 'Sin centro de costo', 'tipo': '', 'nivel': 0, 'filas': propios['—']['filas'],
                       'total': propios['—']['total'], 'acumulado': propios['—']['total'], 'tiene_hijos': False}
    return render(request, 'contabilidad/centros_reporte.html', {
        'grupos': grupos, 'desde': desde, 'hasta': hasta, 'mes_desde': _mes_input(desde),
        'mes_hasta': _mes_input(hasta), 'total': sum((g['total'] for g in propios.values()), D0)})


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
    columnas = [(cb.pk, cb.nombre) for cb in lineas] + [(None, 'Sin línea')]
    planta = Q(centro_costo__tipo__in=['PRODUCCION', 'SERVICIO'])
    RUBROS = [('ventas', r'^70', Q()), ('costo', r'^69', Q()),
              ('gastos', r'^6[2-8]', ~planta),  # administración y ventas (la planta va al costo del producto)
              ('planta', r'^6[2-8]', planta), ('otros', r'^7[3-8]', Q())]
    datos = {clave: {pk: D0 for pk, _ in columnas} for clave, _, _ in RUBROS}
    base = reportes.lineas_rango(desde, hasta).filter(es_destino=False)
    for clave, regex, filtro in RUBROS:
        for f in (base.filter(filtro, cuenta__codigo__regex=regex).values('centro_beneficio')
                  .annotate(d=Sum('debe'), h=Sum('haber'))):
            pk = f['centro_beneficio'] if f['centro_beneficio'] in datos[clave] else None
            datos[clave][pk] += (f['h'] or D0) - (f['d'] or D0)  # ingresos positivos, costos negativos
    # gasto de planta: solo lo que no absorbieron las órdenes de producción (lo absorbido ya está en el costo)
    from produccion.models import HoraOrden
    from contabilidad.centralizar import _rango
    rango = [_rango(desde)[0], _rango(hasta)[1]]
    for h in HoraOrden.objects.filter(orden__estado='TERMINADA', orden__fecha_fin__range=rango).select_related(
            'orden__producto'):
        pk = h.orden.producto.centro_beneficio_id
        pk = pk if pk in datos['planta'] else None
        datos['planta'][pk] += h.costo_mo + h.costo_maquina + h.costo_cif
    # lo que la liquidación del costo real llevó al producto también salió del gasto de planta
    from produccion.models import LiquidacionOrden
    for lo in LiquidacionOrden.objects.filter(liquidacion__periodo__range=[desde, hasta]).select_related(
            'orden__producto'):
        pk = lo.orden.producto.centro_beneficio_id
        pk = pk if pk in datos['planta'] else None
        datos['planta'][pk] += lo.mano_obra + lo.cif
    margen ={pk: datos['ventas'][pk] + datos['costo'][pk] for pk, _ in columnas}
    resultado = {pk: margen[pk] + datos['gastos'][pk] + datos['planta'][pk] + datos['otros'][pk]
                 for pk, _ in columnas}
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
    filas = [('Ventas netas', datos['ventas'], False), ('Costo de ventas', datos['costo'], False),
             ('Margen bruto', margen, True), ('Gastos de administración y ventas', datos['gastos'], False),
             ('Gasto de planta no absorbido (subaplicación)', datos['planta'], False),
             ('Otros ingresos y gastos', datos['otros'], False), ('Resultado de la línea', resultado, True),
             ('Cuentas por cobrar al cierre', cxc, False), ('Inventario al cierre', inventario, False)]
    tabla = [{'rubro': r, 'valores': [v[pk] for pk, _ in columnas], 'total': sum(v.values(), D0), 'fuerte': fuerte}
             for r, v, fuerte in filas]
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
        periodo = date.today().strftime('%Y%m')
        lineas = [f'{periodo}00|{c.codigo}|{c.nombre}|01|PLAN CONTABLE GENERAL EMPRESARIAL|||1|' for c in qs]
        return txt_response(f'LE{empresa.ruc}{periodo}00050300001111.txt', lineas)
    return render(request, 'contabilidad/plan.html', {'cuentas': qs, 'q': q, 'clase': request.GET.get('clase', '')})


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
