"""Obras › Ejecución: adelantos y valorizaciones con su factura, fórmula polinómica e índices, cronograma y avance
físico (curva S), tareo y planilla de construcción civil, partes diarios de equipo, subcontratos, reporte por partida
y versiones del presupuesto que vienen del S10."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.permisos import puede
from core.utils import excel_response

from . import construccion_civil as civil
from . import ejecucion as ej
from . import equipos as eq
from . import subcontratos as sub
from . import valorizaciones as val
from . import versiones as ver
from .models import (AdelantoObra, IndiceUnificado, Obra, ParteEquipo, Partida, PlanillaCivil,
                     Presupuesto, Subcontrato, Tareo, Valorizacion, ValorizacionSubcontrato)


def _dec(valor, defecto=None):
    texto = str(valor or '').strip().replace(',', '')
    if not texto:
        return defecto
    try:
        return Decimal(texto)
    except InvalidOperation:
        return defecto


def _fecha(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def _periodo(valor):
    """'2026-10' (input month) o '202610' → '202610'."""
    texto = (valor or '').replace('-', '').strip()
    return texto if len(texto) == 6 and texto.isdigit() else ''


def _partidas(presupuesto):
    return val.partidas_de(presupuesto)


# ---------------------------------------------------------------- tablero de la ejecución
@login_required
def ejecucion(request, pk):
    o = get_object_or_404(Obra.objects.select_related('cliente', 'centro_costo', 'almacen'), pk=pk)
    from .ejecucion import atrasadas
    from .gestion import control_partidas
    return render(request, 'obras/ejecucion.html', {
        'control': control_partidas(o) if o.tipo != 'SERVICIO' and o.estado in ('EJECUCION', 'PARALIZADA') else None,
        'o': o, 'r': val.resumen(o), 'valorizaciones': o.valorizaciones.select_related('factura').order_by('-numero'),
        'adelantos': o.adelantos.select_related('factura'), 'tipos_adelanto': AdelantoObra.TIPOS,
        'atrasadas': atrasadas(o)[:5], 'hoy': timezone.localdate(),
        'formulas': o.formulas.prefetch_related('monomios'),
        'subcontratos': o.subcontratos.select_related('proveedor')[:10],
        'cuentas': _cuentas(), 'puede_facturar': puede(request.user, 'obras.facturar')})


def _cuentas():
    from finanzas.models import Cuenta
    return Cuenta.objects.filter(activo=True, es_detracciones=False).order_by('nombre')


@login_required
def adelanto(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    if request.method == 'POST':
        try:
            a = val.registrar_adelanto(o, request.POST.get('tipo'), _dec(request.POST.get('monto')),
                                       _fecha(request.POST.get('fecha')), request.user, request.POST.get('glosa', ''))
            messages.success(request, f'Factura de adelanto {a.factura.numero_completo} por S/ {a.factura.total:,.2f} '
                                      f'(anticipo de clientes, con detracción {a.factura.detraccion_pct:g} %).')
        except val.ErrorValorizacion as exc:
            messages.error(request, str(exc))
    return redirect('obras:ejecucion', pk)


@login_required
def reclasificar(request, pk):
    """Facturas del cliente o con el centro de la obra que se pueden pasar a anticipo (OBR-13)."""
    from django.db.models import Q

    from ventas.models import Venta
    o = get_object_or_404(Obra, pk=pk)
    if request.method == 'POST':
        venta = get_object_or_404(Venta, pk=request.POST.get('venta'))
        try:
            a = val.reclasificar(venta, o, request.POST.get('tipo', 'DIRECTO'), request.user,
                                 request.POST.get('motivo', ''))
            messages.success(request, f'{venta} quedó como {a.get_tipo_display().lower()} de la obra (122): se '
                                      f'amortiza en las valorizaciones y no cuenta como venta en el resultado.')
            return redirect('obras:ejecucion', pk)
        except val.ErrorValorizacion as exc:
            messages.error(request, str(exc))
    filtro = Q(centro_costo=o.centro_costo)
    if o.cliente_id:
        filtro |= Q(tercero=o.cliente)
    ventas = (Venta.objects.filter(filtro, tipo_comprobante='01', estado='REGISTRADO')
              .exclude(adelantos_obra__isnull=False).select_related('tercero').order_by('-fecha_emision')[:100])
    return render(request, 'obras/reclasificar.html', {'o': o, 'ventas': ventas, 'tipos': AdelantoObra.TIPOS})


@login_required
def garantia_cobrar(request, pk):
    from finanzas.models import Cuenta
    o = get_object_or_404(Obra, pk=pk)
    if request.method == 'POST':
        try:
            d = val.cobrar_garantia(o, _dec(request.POST.get('monto')), _fecha(request.POST.get('fecha')),
                                    Cuenta.objects.filter(pk=request.POST.get('cuenta') or 0).first(), request.user,
                                    request.POST.get('numero_operacion', ''))
            messages.success(request, f'Cobro del fondo de garantía registrado ({d.movimiento.voucher}).')
        except val.ErrorValorizacion as exc:
            messages.error(request, str(exc))
    return redirect('obras:ejecucion', pk)


# ---------------------------------------------------------------- valorizaciones
@login_required
def valorizacion_nueva(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    if request.method == 'POST':
        try:
            v = val.nueva(o, _periodo(request.POST.get('periodo')), request.user)
            messages.success(request, f'{v} creada con el metrado propuesto desde el avance físico: revíselo.')
            return redirect('obras:valorizacion', v.pk)
        except val.ErrorValorizacion as exc:
            messages.error(request, str(exc))
    return redirect('obras:ejecucion', pk)


@login_required
def valorizacion(request, pk):
    v = get_object_or_404(Valorizacion.objects.select_related('obra__cliente', 'presupuesto', 'factura'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'guardar':
                metrados = {int(k[8:]): _dec(x, Decimal('0')) for k, x in request.POST.items()
                            if k.startswith('metrado_') and k[8:].isdigit()}
                amort = {t: _dec(request.POST.get(f'amort_{t}')) for t in ('DIRECTO', 'MATERIALES')
                         if request.POST.get(f'amort_{t}', '').strip()}
                ret = _dec(request.POST.get('retencion')) if request.POST.get('retencion', '').strip() else None
                for aviso in val.calcular(v, metrados, amort or None, ret, recalcular_amortizacion=not amort):
                    messages.warning(request, aviso)
                messages.success(request, 'Valorización recalculada.')
            elif accion == 'enviar':
                val.enviar(v, request.user)
                messages.success(request, 'Enviada al supervisor para su aprobación.')
            elif accion == 'observar':
                val.observar(v, request.user, request.POST.get('observacion', ''))
                messages.info(request, 'Observada: vuelve a borrador para corregirla.')
            elif accion == 'aprobar':
                if not puede(request.user, 'obras.aprobar'):
                    raise val.ErrorValorizacion('Registrar la aprobación exige el permiso «Aprobar» de Obras.')
                val.aprobar(v, request.user, request.POST.get('supervisor', ''), _fecha(request.POST.get('fecha')))
                messages.success(request, 'Aprobación del supervisor registrada: ya se puede facturar.')
            elif accion == 'facturar':
                if not puede(request.user, 'obras.facturar'):
                    raise val.ErrorValorizacion('Facturar exige el permiso «Facturar adelantos y valorizaciones».')
                comp = val.facturar(v, request.user, _fecha(request.POST.get('fecha')))
                messages.success(request, f'Factura {comp.numero_completo} por S/ {comp.total:,.2f}' + (
                    f'; fondo de garantía retenido S/ {v.retencion_garantia:,.2f}' if v.retencion_garantia else ''))
            elif accion == 'anular':
                val.anular(v, request.user, request.POST.get('motivo_anulacion', ''))
                messages.info(request, 'Valorización anulada.')
        except val.ErrorValorizacion as exc:
            messages.error(request, str(exc))
        return redirect('obras:valorizacion', pk)
    filas = val.acumulados(v)
    if request.GET.get('formato') == 'excel':
        datos = [[f['partida'].item, f['partida'].descripcion, f['partida'].unidad, f['partida'].metrado,
                  f['partida'].precio, f['partida'].parcial, f['anterior'], f['linea'].metrado, f['acumulado'],
                  f['saldo'], f['anterior_monto'], f['linea'].monto, f['acumulado_monto'], f['saldo_monto'],
                  f['avance']] for f in filas]
        datos += [[], ['', 'COSTO DIRECTO DEL PERIODO'] + [''] * 9 + [v.costo_directo],
                  ['', 'GASTOS GENERALES'] + [''] * 9 + [v.gastos_generales],
                  ['', 'UTILIDAD'] + [''] * 9 + [v.utilidad], ['', 'VALORIZADO SIN IGV'] + [''] * 9 + [v.subtotal],
                  ['', f'REINTEGRO (K = {v.k or "—"})'] + [''] * 9 + [v.reintegro],
                  ['', 'DEDUCCIÓN DEL REINTEGRO POR ADELANTOS'] + [''] * 9 + [-v.deduccion_reintegro],
                  ['', 'AMORTIZACIÓN ADELANTO DIRECTO'] + [''] * 9 + [-v.amort_directo],
                  ['', 'AMORTIZACIÓN ADELANTO PARA MATERIALES'] + [''] * 9 + [-v.amort_materiales],
                  ['', 'BASE DE LA FACTURA'] + [''] * 9 + [v.base_factura], ['', 'IGV'] + [''] * 9 + [v.igv],
                  ['', 'TOTAL DE LA FACTURA'] + [''] * 9 + [v.total_factura],
                  ['', 'FONDO DE GARANTÍA RETENIDO'] + [''] * 9 + [-v.retencion_garantia]]
        return excel_response(f'Valorizacion_{v.obra.codigo}_{v.numero}', f'{v} · {v.obra.nombre}', [
            'Ítem', 'Descripción', 'Und.', 'Metrado contrato', 'Precio', 'Parcial', 'Metrado anterior',
            'Metrado del periodo', 'Metrado acumulado', 'Saldo metrado', 'S/ anterior', 'S/ del periodo',
            'S/ acumulado', 'Saldo S/', 'Avance %'], datos)
    return render(request, 'obras/valorizacion.html', {
        'v': v, 'filas': filas, 'adelantos': {t: val.saldo_adelanto(v.obra, t, excluir=v)
                                              for t, _ in AdelantoObra.TIPOS},
        'hoy': timezone.localdate(), 'puede_facturar': puede(request.user, 'obras.facturar'),
        'puede_aprobar': puede(request.user, 'obras.aprobar')})


# ---------------------------------------------------------------- fórmula polinómica e índices
@login_required
def formula(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    if request.method == 'POST':
        try:
            if request.FILES.get('archivo'):
                hojas = ej.hojas(request.FILES['archivo'])
                filas = []
                for _, h in hojas:
                    filas = val.leer_formula(h)
                    if filas:
                        break
            else:
                filas = []
                for linea in (request.POST.get('texto') or '').splitlines():
                    partes = linea.replace(';', ' ').replace('\t', ' ').split()
                    if len(partes) >= 3:
                        coef, codigo = _dec(partes[1]), val._codigo_indice(partes[2])
                        if coef is not None and codigo:
                            filas.append((partes[0][:10], val.r3(coef), codigo, ' '.join(partes[3:])[:120]))
            f = val.guardar_formula(o, filas, numero=int(request.POST.get('numero') or 1),
                                    nombre=request.POST.get('nombre', ''),
                                    subpresupuesto=request.POST.get('subpresupuesto', '').strip(),
                                    area=request.POST.get('area') or '1',
                                    periodo_base=_periodo(request.POST.get('periodo_base')))
            messages.success(request, f'{f} guardada con {f.monomios.count()} monomios.')
        except (val.ErrorValorizacion, ej.ErrorEjecucion) as exc:
            messages.error(request, str(exc))
        return redirect('obras:formula', pk)
    formulas = list(o.formulas.prefetch_related('monomios'))
    meses = sorted({v.periodo for v in o.valorizaciones.exclude(estado='ANULADA')} |
                   {timezone.localdate().strftime('%Y%m')})
    tabla = [{'f': f, 'ks': [(m, val.coeficiente_k(f, m)) for m in meses[-8:]]} for f in formulas]
    return render(request, 'obras/formula.html', {'o': o, 'tabla': tabla, 'meses': meses[-8:],
                                                  'areas': IndiceUnificado.AREAS})


@login_required
def indices(request):
    if request.method == 'POST':
        periodo = _periodo(request.POST.get('periodo'))
        try:
            if request.FILES.get('archivo'):
                valores = {}
                for _, h in ej.hojas(request.FILES['archivo']):
                    valores = val.leer_indices(h)
                    if valores:
                        break
            else:
                valores = {}
                for linea in (request.POST.get('texto') or '').splitlines():
                    partes = linea.replace(';', ' ').replace('\t', ' ').split()
                    codigo = val._codigo_indice(partes[0]) if partes else None
                    if codigo:
                        for area, x in enumerate(partes[1:7], 1):
                            d = _dec(x)
                            if d:
                                valores[(codigo, str(area))] = d
            n = val.guardar_indices(periodo, valores)
            messages.success(request, f'{n} índices de {periodo[4:]}/{periodo[:4]} guardados.')
        except (val.ErrorValorizacion, ej.ErrorEjecucion) as exc:
            messages.error(request, str(exc))
        return redirect('obras:indices')
    periodo = _periodo(request.GET.get('periodo')) or IndiceUnificado.objects.order_by('-periodo').values_list(
        'periodo', flat=True).first() or ''
    filas = {}
    for i in IndiceUnificado.objects.filter(periodo=periodo):
        filas.setdefault(i.codigo, {})[i.area] = i.valor
    return render(request, 'obras/indices.html', {
        'periodo': periodo, 'filas': sorted(filas.items()), 'areas': [a for a, _ in IndiceUnificado.AREAS],
        'periodos': IndiceUnificado.objects.values_list('periodo', flat=True).distinct().order_by('-periodo')[:36]})


# ---------------------------------------------------------------- cronograma, avance y curva S
@login_required
def cronograma(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    p = val.presupuesto_ejecucion(o)
    if request.method == 'POST' and request.FILES.get('archivo'):
        if p is None:
            messages.error(request, 'La obra no tiene presupuesto contractual ni meta.')
        else:
            try:
                n, faltan = ej.importar_cronograma(p, request.FILES['archivo'],
                                                   en_porcentaje=request.POST.get('unidad') == 'PCT',
                                                   forzar=bool(request.POST.get('forzar')))
                messages.success(request, f'Cronograma cargado: {n} partidas programadas.')
                if faltan:
                    messages.warning(request, f'{len(faltan)} fila(s) sin partida equivalente: ' + '; '.join(faltan[:6]))
            except ej.ErrorEjecucion as exc:
                messages.error(request, str(exc))
        return redirect('obras:cronograma', pk)
    curva = ej.curva_s(o)
    return render(request, 'obras/cronograma.html', {'o': o, 'p': p, 'curva': curva, 'atrasadas': ej.atrasadas(o)})


@login_required
def avance(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    p = val.presupuesto_ejecucion(o)
    if request.method == 'POST':
        partida = Partida.objects.filter(pk=request.POST.get('partida') or 0, presupuesto__obra=o).first()
        try:
            if partida is None:
                raise ej.ErrorEjecucion('Elija la partida.')
            a, aviso = ej.registrar_avance(o, partida, _fecha(request.POST.get('fecha')),
                                           _dec(request.POST.get('metrado'), Decimal('0')), request.user,
                                           request.FILES.get('foto'), request.POST.get('nota', ''))
            messages.success(request, f'Avance de {partida.item} registrado: {a.metrado:,.2f} {partida.unidad}.')
            if aviso:
                messages.warning(request, aviso)
        except ej.ErrorEjecucion as exc:
            messages.error(request, str(exc))
        return redirect('obras:avance', pk)
    acumulado = val.avance_acumulado(o)
    partidas = _partidas(p)
    for x in partidas:
        x.acumulado = acumulado.get(x.clave, Decimal('0'))
        x.pct = (x.acumulado / x.metrado * 100).quantize(Decimal('0.1')) if x.metrado else Decimal('0')
    from core import sustentos
    from .models import AvancePartida
    registros = AvancePartida.todos.filter(obra=o).select_related('partida', 'responsable', 'anulado_por')
    filtro = request.GET.get('partida')
    if filtro:  # OBR-23: lista de los avances de una partida (también los anulados)
        registros = registros.filter(partida_id=filtro)
    ultimos = list(registros[:200 if filtro else 40])
    for a in ultimos:
        a.fotos = sustentos.de(a)
    return render(request, 'obras/avance.html', {'o': o, 'p': p, 'partidas': partidas, 'ultimos': ultimos,
                                                 'hoy': timezone.localdate(), 'filtro': filtro})


@login_required
def avance_anular(request, pk):
    """OBR-23: anular un avance mal registrado, con su motivo."""
    from .models import AvancePartida
    a = get_object_or_404(AvancePartida.todos, pk=pk)
    if request.method == 'POST':
        try:
            ej.anular_avance(a, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'Avance de {a.partida.item} del {a.fecha:%d/%m/%Y} anulado.')
        except ej.ErrorEjecucion as exc:
            messages.error(request, str(exc))
    return redirect(f"{reverse('obras:avance', args=[a.obra_id])}?partida={a.partida_id}")


# ---------------------------------------------------------------- reporte por partida y versiones
@login_required
def reporte_partidas(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    r = ej.reporte_partidas(o)
    if r is None:
        messages.error(request, 'La obra no tiene presupuesto.')
        return redirect('obras:obra', pk)
    if request.GET.get('formato') == 'excel':
        filas = []
        for f in r['filas']:
            x = f['partida']
            if f['titulo']:
                filas.append([x.item, x.descripcion])
                continue
            filas.append([x.item, x.descripcion, x.unidad, x.metrado, x.precio, f['contractual'], f['meta'],
                          f['avance'], f['avance_pct'], f['valorizado'], f['mat'], f['mo'], f['eq'], f['sub'], f['gg'],
                          f['costo'], f['margen']])
        t = r['totales']
        sp = r['sin_partida']
        filas += [[], ['', 'Costos sin partida (no asignables)', '', '', '', '', '', '', '', '', sp['mat'], sp['mo'],
                       sp['eq'], sp['sub']],
                  ['', 'TOTAL', '', '', '', t['contractual'], t['meta'], '', '', t['valorizado'], '', '', '', '', '',
                   t['costo'], t['margen']]]
        return excel_response(f'Partidas_{o.codigo}', f'CONTROL POR PARTIDA · {o.codigo} {o.nombre}', [
            'Ítem', 'Descripción', 'Und.', 'Metrado', 'Precio', 'Contractual S/', 'Meta S/', 'Avance (metrado)',
            'Avance %', 'Valorizado S/', 'Materiales S/', 'Mano de obra S/', 'Equipos S/', 'Subcontratos S/',
            'Gastos generales S/', 'Costo real S/', 'Margen S/'], filas)
    return render(request, 'obras/reporte_partidas.html', {'o': o, 'r': r})


@login_required
def versiones(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    if request.method == 'POST':
        p = get_object_or_404(Presupuesto, pk=request.POST.get('presupuesto'), obra=o)
        p.motivo = request.POST.get('motivo', '') if request.POST.get('motivo', '') in dict(Presupuesto.MOTIVOS) else ''
        p.resolucion = request.POST.get('resolucion', '')[:120]
        p.fecha_resolucion = _fecha(request.POST.get('fecha_resolucion'))
        p.save(update_fields=['motivo', 'resolucion', 'fecha_resolucion'])
        messages.success(request, f'{p}: motivo y resolución guardados.')
        return redirect(f'{request.path}?anterior={request.POST.get("anterior", "")}&nuevo={p.pk}')
    historial = ver.historial(o)
    comparacion = None
    nuevo = Presupuesto.objects.filter(pk=request.GET.get('nuevo') or 0, obra=o).first()
    anterior = Presupuesto.objects.filter(pk=request.GET.get('anterior') or 0, obra=o).first()
    if nuevo is not None and anterior is None:
        anterior = ver.anterior_de(nuevo)
    if nuevo is not None and anterior is not None and nuevo.pk != anterior.pk:
        comparacion = ver.comparar(anterior, nuevo)
    return render(request, 'obras/versiones.html', {
        'o': o, 'historial': historial, 'c': comparacion, 'presupuestos': o.presupuestos.order_by('tipo', 'version'),
        'motivos': Presupuesto.MOTIVOS, 'nuevo': nuevo, 'anterior': anterior})


# ---------------------------------------------------------------- mano de obra
@login_required
def tareo(request, pk):
    from planillas.models import Trabajador
    o = get_object_or_404(Obra, pk=pk)
    meta = val.presupuesto_meta(o)
    partidas = _partidas(meta)
    trabajadores = list(civil.trabajadores_obra())
    if request.method == 'POST':
        por_id = {t.pk: t for t in trabajadores}
        por_partida = {x.pk: x for x in partidas}
        filas = []
        for n in range(int(request.POST.get('filas') or 0)):
            t = por_id.get(int(request.POST.get(f'trabajador_{n}') or 0))
            if t is None:
                continue
            filas.append((t, por_partida.get(int(request.POST.get(f'partida_{n}') or 0)),
                          _dec(request.POST.get(f'horas_{n}'), Decimal('0')),
                          _dec(request.POST.get(f'h60_{n}'), Decimal('0')),
                          _dec(request.POST.get(f'h100_{n}'), Decimal('0'))))
        try:
            capataz = Trabajador.objects.filter(pk=request.POST.get('capataz') or 0).first()
            t = civil.registrar_tareo(o, _fecha(request.POST.get('fecha')), request.POST.get('cuadrilla', ''), filas,
                                      request.user, capataz=capataz,
                                      observaciones=request.POST.get('observaciones', ''))
            messages.success(request, f'{t} registrado con {t.lineas.count()} trabajador(es).')
        except civil.ErrorCivil as exc:
            messages.error(request, str(exc))
        return redirect('obras:tareo', pk)
    tareos = list(Tareo.objects.filter(obra=o).prefetch_related('lineas__trabajador', 'lineas__partida')[:30])
    return render(request, 'obras/tareo.html', {
        'o': o, 'partidas': partidas, 'trabajadores': trabajadores, 'tareos': tareos, 'hoy': timezone.localdate(),
        'n_filas': range(max(len(trabajadores), 6) if len(trabajadores) <= 40 else 40)})


@login_required
def planillas_civil(request, pk):
    o = get_object_or_404(Obra, pk=pk)
    if request.method == 'POST':
        try:
            p = civil.nueva_planilla(o, _fecha(request.POST.get('fecha')))
            messages.success(request, f'{p} calculada desde los tareos.')
            return redirect('obras:planilla_civil', p.pk)
        except civil.ErrorCivil as exc:
            messages.error(request, str(exc))
        return redirect('obras:planillas_civil', pk)
    planillas = list(o.planillas_civil.all())
    for p in planillas:
        p.t = civil.totales(p)
    return render(request, 'obras/planillas_civil.html', {
        'o': o, 'planillas': planillas, 'hoy': timezone.localdate(), 'horas': civil.horas_meta_real(o),
        'tasas': civil.tasas(timezone.localdate())})


@login_required
def planilla_civil(request, pk):
    from finanzas.models import Cuenta
    p = get_object_or_404(PlanillaCivil.objects.select_related('obra'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'calcular':
                civil.calcular(p)
                messages.success(request, 'Planilla recalculada desde los tareos.')
            elif accion == 'cerrar':
                civil.cerrar(p)
                messages.success(request, 'Planilla cerrada: su costo va al centro de costo de la obra al centralizar.')
            elif accion == 'reabrir':
                civil.reabrir(p)
                messages.info(request, 'Planilla reabierta.')
            elif accion == 'pagar':
                mov = civil.pagar(p, Cuenta.objects.filter(pk=request.POST.get('cuenta') or 0).first(),
                                  _fecha(request.POST.get('fecha')) or timezone.localdate(), request.user)
                messages.success(request, f'Pago registrado en Tesorería ({mov.voucher}).')
            elif accion == 'anular' and p.estado == 'BORRADOR':
                p.estado = 'ANULADA'
                p.save(update_fields=['estado'])
                messages.info(request, 'Planilla anulada.')
        except civil.ErrorCivil as exc:
            messages.error(request, str(exc))
        return redirect('obras:planilla_civil', pk)
    lineas = list(p.lineas.select_related('trabajador'))
    tot = civil.totales(p)
    if request.GET.get('formato') == 'excel':
        filas = [[l.trabajador.numero_doc, l.trabajador.nombre_completo, l.categoria.title(), l.dias, l.horas, l.horas_60, l.horas_100,
                  l.jornal, l.basico, l.dominical, l.buc, l.extras, l.movilidad, l.pension, l.conafovicer, l.neto,
                  l.essalud, l.sctr, l.cts, l.vacaciones, l.gratificacion, l.costo] for l in lineas]
        return excel_response(f'Planilla_civil_{p.obra.codigo}_{p.numero}', str(p), [
            'DNI', 'Trabajador', 'Categoría', 'Días', 'Horas', 'HE 60%', 'HE 100%', 'Jornal', 'Básico', 'Dominical',
            'BUC', 'Horas extra', 'Movilidad', 'ONP/AFP', 'CONAFOVICER', 'Neto', 'EsSalud', 'SCTR', 'CTS',
            'Vacaciones', 'Gratificación', 'Costo total'], filas)
    return render(request, 'obras/planilla_civil.html', {'p': p, 'lineas': lineas, 't': tot, 'cuentas': _cuentas(),
                                                         'hoy': timezone.localdate()})


# ---------------------------------------------------------------- equipos
@login_required
def partes(request, pk):
    from core.models import Tercero
    from produccion.models import Equipo
    o = get_object_or_404(Obra, pk=pk)
    meta = val.presupuesto_meta(o)
    partidas = _partidas(meta)
    if request.method == 'POST':
        d = request.POST
        parte = ParteEquipo(
            obra=o, fecha=_fecha(d.get('fecha')) or timezone.localdate(),
            equipo=Equipo.objects.filter(pk=d.get('equipo') or 0).first(), descripcion=d.get('descripcion', '')[:120],
            propiedad=d.get('propiedad') if d.get('propiedad') in ('PROPIO', 'ALQUILADO') else 'PROPIO',
            proveedor=Tercero.objects.filter(pk=d.get('proveedor') or 0).first(),
            partida=Partida.objects.filter(pk=d.get('partida') or 0, presupuesto__obra=o).first(),
            operador=d.get('operador', '')[:120], horometro_inicio=_dec(d.get('horometro_inicio')),
            horometro_fin=_dec(d.get('horometro_fin')), horas=_dec(d.get('horas'), Decimal('0')),
            horas_espera=_dec(d.get('horas_espera'), Decimal('0')), combustible=_dec(d.get('combustible'), Decimal('0')),
            tarifa=_dec(d.get('tarifa'), Decimal('0')), tarifa_espera=_dec(d.get('tarifa_espera'), Decimal('0')),
            observacion=d.get('observacion', '')[:200])
        try:
            parte = eq.registrar(parte, request.user)
            messages.success(request, f'{parte} registrado: {parte.horas:g} h × S/ {parte.tarifa:,.2f} = '
                                      f'S/ {parte.costo:,.2f}.')
        except eq.ErrorParte as exc:
            messages.error(request, str(exc))
        return redirect('obras:partes', pk)
    return render(request, 'obras/partes.html', {
        'o': o, 'partidas': partidas, 'partes': o.partes_equipo.select_related('equipo', 'partida', 'proveedor')[:60],
        'equipos': Equipo.objects.filter(activo=True).order_by('codigo'), 'r': eq.resumen(o),
        'proveedores': Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS']).order_by('nombre'),
        'hoy': timezone.localdate()})


# ---------------------------------------------------------------- subcontratos
@login_required
def subcontratos(request, pk):
    from core.models import Tercero
    o = get_object_or_404(Obra, pk=pk)
    meta = val.presupuesto_meta(o)
    partidas = _partidas(meta)
    if request.method == 'POST':
        d = request.POST
        lineas = []
        for x in partidas:
            metrado = _dec(d.get(f'metrado_{x.pk}'))
            if metrado:
                lineas.append((x, '', '', metrado, _dec(d.get(f'precio_{x.pk}'), Decimal('0'))))
        try:
            sc = sub.crear(o, Tercero.objects.filter(pk=d.get('proveedor') or 0).first(), d.get('descripcion', ''),
                           lineas, request.user, modalidad=d.get('modalidad') or 'PRECIOS_UNITARIOS',
                           adelanto_pct=_dec(d.get('adelanto_pct'), Decimal('0')),
                           garantia_pct=_dec(d.get('garantia_pct'), Decimal('0')), detraccion=bool(d.get('detraccion')))
            messages.success(request, f'{sc} registrado por S/ {sc.monto:,.2f}. Emita su orden de servicio.')
            return redirect('obras:subcontrato', sc.pk)
        except sub.ErrorSubcontrato as exc:
            messages.error(request, str(exc))
        return redirect('obras:subcontratos', pk)
    return render(request, 'obras/subcontratos.html', {
        'o': o, 'partidas': partidas, 'lista': o.subcontratos.select_related('proveedor', 'orden_compra'),
        'proveedores': Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS']).order_by('nombre'),
        'modalidades': Subcontrato.MODALIDADES})


@login_required
def subcontrato(request, pk):
    from compras.models import Compra
    from finanzas.models import Cuenta
    sc = get_object_or_404(Subcontrato.objects.select_related('obra', 'proveedor', 'orden_compra'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'orden':
                oc = sub.emitir_orden(sc, request.user)
                messages.success(request, f'Orden de servicio {oc.numero} creada (pendiente de aprobar en Compras).')
            elif accion == 'valorizar':
                metrados = {int(k[8:]): _dec(x, Decimal('0')) for k, x in request.POST.items()
                            if k.startswith('metrado_') and k[8:].isdigit()}
                v = sub.valorizar(sc, _periodo(request.POST.get('periodo')), metrados, request.user)
                messages.success(request, f'{v} por S/ {v.bruto:,.2f} (neto S/ {v.neto:,.2f}).')
            elif accion in ('aprobar', 'anular'):
                v = get_object_or_404(ValorizacionSubcontrato, pk=request.POST.get('valorizacion'), subcontrato=sc)
                if accion == 'aprobar':
                    sub.aprobar(v, request.user)
                    messages.success(request, f'{v} aprobada: se generaron sus conformidades de servicio (HES) en '
                                              f'borrador; adjunte el informe y envíelas a liberar.')
                else:
                    sub.anular(v)
                    messages.info(request, f'{v} anulada.')
            elif accion == 'retener':
                v = get_object_or_404(ValorizacionSubcontrato, pk=request.POST.get('valorizacion'), subcontrato=sc)
                compra = get_object_or_404(Compra, pk=request.POST.get('compra'))
                sub.retener_garantia(v, compra, request.user)
                messages.success(request, f'Fondo de garantía retenido de {compra}.')
            elif accion == 'devolver':
                mov = sub.devolver_garantia(sc, _dec(request.POST.get('monto')), _fecha(request.POST.get('fecha')),
                                            Cuenta.objects.filter(pk=request.POST.get('cuenta') or 0).first(),
                                            request.user, request.POST.get('numero_operacion', ''))
                messages.success(request, f'Devolución del fondo de garantía registrada ({mov.voucher}).')
        except sub.ErrorSubcontrato as exc:
            messages.error(request, str(exc))
        return redirect('obras:subcontrato', pk)
    compras = Compra.objects.filter(tercero=sc.proveedor, estado='REGISTRADO').exclude(
        aplicaciones__origen='GARANTIA').order_by('-fecha_emision')[:30]
    retenido, devuelto, saldo = sub.garantia(sc)
    from .models import EvaluacionSubcontrato
    evaluacion = EvaluacionSubcontrato.objects.filter(subcontrato=sc).select_related('evaluado_por').first()
    return render(request, 'obras/subcontrato.html', {
        'sc': sc, 'control': sub.control(sc), 'valorizaciones': sc.valorizaciones.prefetch_related('lineas__linea',
                                                                                               'lineas__hes'),
        'adelanto': sub.adelanto_entregado(sc), 'amortizado': sub.amortizado(sc), 'compras': compras,
        'garantia': {'retenido': retenido, 'devuelto': devuelto, 'saldo': saldo}, 'cuentas': _cuentas(),
        'hoy': timezone.localdate(), 'evaluacion': evaluacion, 'notas': EvaluacionSubcontrato.NOTAS,
        'criterios': [(c, e, getattr(evaluacion, c) if evaluacion else 3) for c, e in (
            ('plazo', 'Cumplimiento del plazo'), ('calidad', 'Calidad'), ('seguridad', 'Seguridad (SST)'))]})
