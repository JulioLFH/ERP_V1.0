"""Pantallas de la rendición de gastos (módulo «Rendición de gastos»)."""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core import sustentos
from core.permisos import puede
from core.utils import a_fecha, excel_response, fmt_fecha

from . import operaciones as op
from . import rendiciones as rd
from .forms_rendicion import (CategoriaGastoForm, IndicadorImpuestoForm, LineaGastoForm, MapeoCuentaERPForm,
                              MotivoObservacionForm, PlantillaDistribucionForm, PlantillaLineasFormSet,
                              PoliticaGastoForm, ReglaAprobacionForm, RendicionForm)
from contabilidad.models import CentroCosto

from .models import (CategoriaGasto, Cuenta, EntregaRendir, IndicadorImpuesto, LineaGasto, MapeoCuentaERP,
                     MotivoObservacion, PlantillaDistribucion, PoliticaGasto, ReglaAprobacion, Rendicion)

ERRORES = (rd.ErrorRendicion, op.ErrorTesoreria)


def _fecha(valor):
    try:
        return a_fecha(valor) if valor else timezone.localdate()
    except ValueError:
        raise rd.ErrorRendicion(f'Fecha inválida: {valor}')


def _exigir(request, accion):
    if not puede(request.user, f'rendiciones.{accion}'):
        raise rd.ErrorRendicion('Su usuario no tiene permiso para esta acción.')


# ---------------------------------------------------------------- lista
@login_required
def lista(request):
    user = request.user
    vista = request.GET.get('vista') or 'mias'
    qs = rd.visibles(user)
    if vista == 'mias':
        t = rd.trabajador_de(user)
        qs = qs.filter(Q(rendidor=user) | (Q(entrega__responsable=t) if t else Q(pk__in=[])))
    elif vista == 'aprobar':
        qs = qs.filter(pk__in=[r.pk for r in qs.filter(estado__in=Rendicion.POR_APROBAR) if rd.puede_aprobar(user, r)])
    elif vista == 'contabilizar':
        qs = qs.filter(estado='APROBADA')
    elif vista == 'pagar':
        qs = qs.filter(estado='CONTABILIZADA')
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(numero__icontains=q) | Q(descripcion__icontains=q) | Q(entrega__responsable__nombre__icontains=q)
                       | Q(entrega__numero__icontains=q))
    return render(request, 'finanzas/rendiciones.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')), 'vista': vista,
        'estados': Rendicion.ESTADOS, 've_todo': rd.ve_todo(user),
        'puede_rendir': rd.entregas_para_rendir(user).exists()})


@login_required
def nueva(request):
    entregas = rd.entregas_para_rendir(request.user)
    form = RendicionForm(request.POST or None, entregas=entregas,
                         initial={'fecha': timezone.localdate(), 'entrega': request.GET.get('entrega')})
    if not entregas.exists():
        messages.warning(request, 'No tiene entregas a rendir ni cajas chicas abiertas a su nombre. Pida a tesorería '
                                  'la entrega y al administrador que lo vincule como trabajador (Ajustes › Usuarios).')
    if request.method == 'POST' and form.is_valid():
        try:
            r = rd.crear(form.cleaned_data['entrega'], request.user, form.cleaned_data['descripcion'],
                         form.cleaned_data['fecha'])
            return redirect('rendiciones:detalle', r.pk)
        except rd.ErrorRendicion as exc:
            form.add_error(None, str(exc))
    return render(request, 'core/form.html', {'form': form, 'titulo': 'Nueva rendición de gastos'})


# ---------------------------------------------------------------- detalle y flujo
def _decisiones(request, r):
    salida = {}
    for linea in r.lineas.all():
        estado = request.POST.get(f'decision_{linea.pk}')
        if estado in ('APROBADA', 'OBSERVADA', 'RECHAZADA'):
            salida[linea.pk] = (estado, request.POST.get(f'nota_{linea.pk}', ''),
                                request.POST.get(f'motivo_{linea.pk}') or None)
    return salida


def _volver(request, pk):
    destino = request.POST.get('next', '')
    if destino.startswith('/rendiciones/'):
        return redirect(destino)
    return redirect('rendiciones:detalle', pk)


def _guardar_linea(request, r, form):
    if not form.is_valid():
        raise rd.ErrorRendicion('Revise el gasto: ' + '; '.join(
            f'{form.fields[k].label if k in form.fields else ""} {" ".join(v)}'.strip() for k, v in form.errors.items()))
    with transaction.atomic():
        linea = rd.guardar_linea(r, form.save(commit=False), request.user)
        for archivo in form.cleaned_data.get('fotos') or []:
            sustentos.adjuntar(linea, archivo, request.user, 'Comprobante del gasto')
    rd.evaluar(r)
    return linea


@login_required
def detalle(request, pk):
    r = get_object_or_404(rd.visibles(request.user).select_related('entrega__responsable', 'rendidor'), pk=pk)
    editar = None
    if request.GET.get('editar') and r.editable:
        editar = get_object_or_404(r.lineas, pk=request.GET['editar'])
    form = LineaGastoForm(instance=editar, prefix='g', initial=None if editar else {
        'fecha': timezone.localdate(), 'moneda': r.entrega.moneda, 'centro_costo': r.entrega.centro_costo_id})
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'linea':
                instancia = get_object_or_404(r.lineas, pk=request.POST['linea']) if request.POST.get('linea') \
                    else None
                form = LineaGastoForm(request.POST, request.FILES, instance=instancia, prefix='g')
                _guardar_linea(request, r, form)
                messages.success(request, 'Gasto guardado.')
            elif accion == 'quitar_linea':
                rd.quitar_linea(r, get_object_or_404(r.lineas, pk=request.POST.get('linea')), request.user)
                rd.evaluar(r)
                messages.success(request, 'Gasto quitado.')
            elif accion == 'foto':
                linea = get_object_or_404(r.lineas, pk=request.POST.get('linea'))
                if not r.editable:
                    raise rd.ErrorRendicion('La rendición ya no se modifica.')
                for archivo in request.FILES.getlist('fotos'):
                    sustentos.adjuntar(linea, archivo, request.user, 'Comprobante del gasto')
                messages.success(request, 'Foto agregada.')
            elif accion == 'validar':
                from .comprobantes import ErrorLectura, validar_en_sunat
                linea = get_object_or_404(r.lineas, pk=request.POST.get('linea'))
                try:
                    res = validar_en_sunat(linea)
                except ErrorLectura as exc:
                    raise rd.ErrorRendicion(str(exc))
                if res is None:
                    raise rd.ErrorRendicion('No aplica (solo comprobantes electrónicos) o faltan las credenciales de '
                                            'la API SUNAT en Ajustes › Empresa.')
                rd.evaluar(r)
                (messages.success if res['valido'] else messages.error)(request, f'SUNAT: {res["detalle"]}')
            elif accion == 'enviar':
                rd.enviar(r, request.user, request.POST.get('comentario', ''))
                messages.success(request, f'{r.numero} enviada para aprobación.')
            elif accion == 'aprobar':
                rd.aprobar(r, request.user, request.POST.get('comentario', ''), _decisiones(request, r))
                messages.success(request, f'{r.numero}: {r.get_estado_display().lower()}.')
            elif accion == 'observar':
                rd.observar(r, request.user, request.POST.get('comentario', ''), request.POST.get('motivo') or None)
                messages.success(request, f'{r.numero} devuelta al rendidor con observaciones.')
            elif accion == 'rechazar':
                rd.rechazar(r, request.user, request.POST.get('comentario', ''), request.POST.get('motivo') or None)
                messages.success(request, f'{r.numero} rechazada.')
            elif accion == 'distribuir':
                linea = get_object_or_404(r.lineas, pk=request.POST.get('linea'))
                centros = {c.pk: c for c in CentroCosto.objects.filter(pk__in=request.POST.getlist('centro'))}
                filas = []
                for c, p in zip(request.POST.getlist('centro'), request.POST.getlist('porcentaje')):
                    if c and p:
                        try:
                            filas.append((centros.get(int(c)), Decimal(p.replace(',', '.'))))
                        except (ValueError, ArithmeticError):
                            raise rd.ErrorRendicion(f'Porcentaje inválido: {p}')
                rd.distribuir(linea, filas, request.user)
                messages.success(request, 'Distribución guardada.' if filas else 'Distribución quitada.')
            elif accion == 'plantilla':
                plantilla = get_object_or_404(PlantillaDistribucion, pk=request.POST.get('plantilla'), activo=True)
                n = rd.aplicar_plantilla(r, plantilla, request.user)
                messages.success(request, f'{n} gasto(s) distribuidos con «{plantilla}».')
            elif accion == 'contabilizar':
                _exigir(request, 'contabilizar')
                rd.contabilizar(r, request.user, _fecha(request.POST.get('fecha')) if request.POST.get('fecha')
                                else None)
                messages.success(request, f'{r.numero} contabilizada: facturas al registro de compras y gastos '
                                          f'rendidos contra {r.entrega.numero}.')
            elif accion in ('reembolsar', 'devolver'):
                _exigir(request, 'tesoreria')
                cuenta = get_object_or_404(Cuenta, pk=request.POST.get('cuenta'))
                m = getattr(rd, accion)(r, request.user, cuenta, _fecha(request.POST.get('fecha')),
                                        request.POST.get('numero_operacion', ''))
                messages.success(request, f'Movimiento {m.voucher} registrado.')
            elif accion == 'planilla':
                _exigir(request, 'tesoreria')
                rd.descontar_planilla(r, request.user, _fecha(request.POST.get('fecha')))
                messages.success(request, 'Saldo pasado a cuenta por cobrar del trabajador: regístrelo como '
                                          'adelanto en su próxima planilla.')
            return _volver(request, pk)
        except ERRORES as exc:
            messages.error(request, str(exc))
            if accion != 'linea':
                return _volver(request, pk)
    if request.GET.get('formato') == 'excel':
        return _excel(r)
    lineas = list(r.lineas.select_related('categoria', 'centro_costo', 'compra', 'proyecto', 'sitio', 'indicador',
                                          'motivo').prefetch_related('distribucion__centro_costo'))
    for l in lineas:
        l.fotos = list(sustentos.de(l))
        l.reparto = list(l.distribucion.all())
    entrega = r.entrega
    en_tramite = sum((x.total for x in entrega.rendiciones.filter(estado__in=['ENVIADA', 'EN_REVISION', 'APROBADA',
                                                                               'OBSERVADA', 'BORRADOR'])), Decimal('0'))
    es_rendidor = request.user.pk == r.rendidor_id or request.user.is_superuser
    return render(request, 'finanzas/rendicion.html', {
        'r': r, 'e': entrega, 'lineas': lineas, 'form': form, 'editar': editar,
        'generales': rd.evaluar(r, guardar=False) if r.editable else [],
        'puede_editar': r.editable and es_rendidor, 'puede_aprobar': rd.puede_aprobar(request.user, r),
        'puede_contabilizar': r.estado == 'APROBADA' and puede(request.user, 'rendiciones.contabilizar'),
        'puede_pagar': r.estado == 'CONTABILIZADA' and puede(request.user, 'rendiciones.tesoreria'),
        'en_tramite': en_tramite, 'por_rendir': entrega.saldo - en_tramite, 'por_reembolsar': -entrega.saldo,
        'historial': r.historial.select_related('usuario'),
        'cuentas': Cuenta.objects.filter(activo=True, moneda=entrega.moneda), 'hoy': timezone.localdate(),
        'ct_linea': _ct(LineaGasto), 'hay_movilidad': any(l.tipo_comprobante == 'PM' for l in lineas),
        'motivos': MotivoObservacion.objects.filter(activo=True),
        'plantillas': PlantillaDistribucion.objects.filter(activo=True),
        'centros': CentroCosto.objects.filter(activo=True).order_by('codigo'),
        'fecha_sugerida': rd.fecha_contable_sugerida(r) if r.estado == 'APROBADA' else None,
        'corte': rd.proximo_corte(), 'sugerencias': _sugerencias()})


def _sugerencias():
    """Indicador sugerido por tipo de comprobante (para preseleccionarlo en el formulario)."""
    ids = dict(IndicadorImpuesto.objects.filter(activo=True).values_list('codigo', 'pk'))
    return {'01': ids.get('C1'), '01N': ids.get('S1'), '03': ids.get('S1'), '12': ids.get('S1'),
            'otros': ids.get('NG')}


def _ct(modelo):
    from django.contrib.contenttypes.models import ContentType
    return ContentType.objects.get_for_model(modelo).pk


def _excel(r):
    filas = [[fmt_fecha(l.fecha), l.get_tipo_comprobante_display(), l.ruc, l.razon_social, l.documento,
              l.categoria.nombre, l.centro_costo.codigo if l.centro_costo else '',
              f'{l.proyecto or ""} {l.sitio or ""}'.strip(), l.glosa, l.moneda,
              l.base, l.igv, l.total, l.importe, l.get_estado_display(), l.alertas, l.justificacion, l.comentario]
             for l in r.lineas.select_related('categoria', 'centro_costo')]
    return excel_response(f'rendicion_{r.numero}', f'Rendición {r.numero} - {r.entrega.responsable.nombre}',
                          ['Fecha', 'Comprobante', 'RUC', 'Razón social', 'N°', 'Categoría', 'C. costo', 'Proyecto',
                           'Glosa', 'Moneda', 'Base', 'IGV', 'Total', f'Importe {r.entrega.moneda}', 'Estado',
                           'Fuera de política', 'Justificación', 'Comentario del aprobador'], filas)


@login_required
def imprimir(request, pk):
    r = get_object_or_404(rd.visibles(request.user).select_related('entrega__responsable', 'rendidor'), pk=pk)
    lineas = list(r.lineas.select_related('categoria', 'centro_costo', 'proyecto', 'sitio'))
    for l in lineas:
        l.fotos = [s for s in sustentos.de(l) if (s.archivo.tipo or '').startswith('image/')]
        l.pdfs = [s for s in sustentos.de(l) if not (s.archivo.tipo or '').startswith('image/')]
    return render(request, 'finanzas/rendicion_imprimir.html', {
        'r': r, 'e': r.entrega, 'lineas': lineas, 'historial': r.historial.select_related('usuario')})


# ---------------------------------------------------------------- tesorería
@login_required
def pagos(request):
    """Reembolsos y devoluciones pendientes de las rendiciones contabilizadas (individual o en lote)."""
    pendientes = []
    for r in Rendicion.objects.filter(estado='CONTABILIZADA').select_related('entrega__responsable'):
        saldo = r.entrega.saldo
        if saldo != 0:
            pendientes.append({'r': r, 'saldo': saldo, 'reembolso': -saldo})
    if request.method == 'POST':
        try:
            _exigir(request, 'tesoreria')
            cuenta = get_object_or_404(Cuenta, pk=request.POST.get('cuenta'))
            fecha = _fecha(request.POST.get('fecha'))
            ids = {int(x) for x in request.POST.getlist('rendicion') if x.isdigit()}
            n = 0
            with transaction.atomic():
                for p in pendientes:
                    if p['r'].pk in ids and p['saldo'] < 0:
                        rd.reembolsar(p['r'], request.user, cuenta, fecha, request.POST.get('numero_operacion', ''))
                        n += 1
            messages.success(request, f'{n} reembolso(s) registrados desde {cuenta}.')
        except ERRORES as exc:
            messages.error(request, str(exc))
        return redirect('rendiciones:pagos')
    if request.GET.get('formato') == 'excel':
        return excel_response('reembolsos_rendiciones', 'Reembolsos y devoluciones de rendiciones', [
            'Rendición', 'Entrega', 'Responsable', 'Documento', 'Banco', 'Cuenta', 'CCI', 'Moneda', 'Reembolsar',
            'Por devolver'], [[p['r'].numero, p['r'].entrega.numero, p['r'].entrega.responsable.nombre,
                               p['r'].entrega.responsable.numero_doc, p['r'].entrega.responsable.banco,
                               p['r'].entrega.responsable.cuenta_bancaria, p['r'].entrega.responsable.cci,
                               p['r'].entrega.moneda, max(-p['saldo'], 0), max(p['saldo'], 0)] for p in pendientes])
    return render(request, 'finanzas/rendicion_pagos.html', {
        'pendientes': pendientes, 'cuentas': Cuenta.objects.filter(activo=True), 'hoy': timezone.localdate()})


@login_required
def cajas(request):
    """Cajas chicas: rendiciones aprobadas por contabilizar y reposición del fondo fijo en un paso."""
    if request.method == 'POST':
        try:
            _exigir(request, 'tesoreria')
            _exigir(request, 'contabilizar')
            caja = get_object_or_404(EntregaRendir, pk=request.POST.get('caja'), tipo='CAJA_CHICA', estado='ABIERTA')
            cuenta = get_object_or_404(Cuenta, pk=request.POST.get('cuenta'))
            m, monto, n = rd.reponer_caja(caja, request.user, cuenta, _fecha(request.POST.get('fecha')),
                                          request.POST.get('numero_operacion', ''))
            messages.success(request, f'{caja.numero}: {n} rendición(es) contabilizada(s) y fondo repuesto con '
                                      f'{m.voucher} por {caja.simbolo} {monto:,.2f}.')
        except ERRORES as exc:
            messages.error(request, str(exc))
        return redirect('rendiciones:cajas')
    filas = []
    for c in EntregaRendir.objects.filter(tipo='CAJA_CHICA', estado='ABIERTA').select_related('responsable'):
        aprobadas = list(c.rendiciones.filter(estado='APROBADA'))
        filas.append({'c': c, 'aprobadas': len(aprobadas), 'por_aprobar': sum(r.total for r in aprobadas),
                      'en_tramite': c.rendiciones.filter(estado__in=Rendicion.POR_APROBAR).count()})
    return render(request, 'finanzas/rendicion_cajas.html', {
        'filas': filas, 'cuentas': Cuenta.objects.filter(activo=True), 'hoy': timezone.localdate()})


# ---------------------------------------------------------------- reportes
def _rango(request):
    hoy = timezone.localdate()
    try:
        desde = a_fecha(request.GET['desde']) if request.GET.get('desde') else hoy.replace(month=1, day=1)
        hasta = a_fecha(request.GET['hasta']) if request.GET.get('hasta') else hoy
    except ValueError:
        desde, hasta = hoy.replace(month=1, day=1), hoy
    return desde, hasta


@login_required
def tablero(request):
    desde, hasta = _rango(request)
    datos = rd.tablero(desde, hasta)
    if request.GET.get('formato') == 'excel':
        filas = [['Categoría', k, v] for k, v in datos['por_categoria']] + \
                [['Centro de costo', k, v] for k, v in datos['por_centro']] + \
                [['Persona', k, v] for k, v in datos['por_persona']] + [['Mes', k, v] for k, v in datos['por_mes']]
        return excel_response('tablero_gastos', f'Gastos rendidos {fmt_fecha(desde)} - {fmt_fecha(hasta)}',
                              ['Agrupación', 'Concepto', 'Gasto S/'], filas)
    return render(request, 'finanzas/rendicion_tablero.html', {'d': datos, 'desde': desde, 'hasta': hasta})


@login_required
def antiguedad(request):
    filas = rd.antiguedad()
    if request.GET.get('formato') == 'excel':
        return excel_response('saldos_por_rendir', 'Saldos por rendir con antigüedad', [
            'Entrega', 'Tipo', 'Responsable', 'Fecha', 'Moneda', 'Entregado', 'Rendido', 'Devuelto', 'Saldo', 'Días',
            'Tramo'], [[f['e'].numero, f['e'].get_tipo_display(), f['e'].responsable.nombre, fmt_fecha(f['e'].fecha),
                        f['e'].moneda, f['entregado'], f['rendido'], f['devuelto'], f['saldo'], f['dias'], f['tramo']]
                       for f in filas])
    tramos = [(etiqueta, sum((f['saldo'] for f in filas if f['tramo'] == t and f['saldo'] > 0), Decimal('0')), clase)
              for t, etiqueta, clase in (('0-15', '0 a 15 días', ''), ('16-30', '16 a 30 días', 'text-warning'),
                                         ('+30', 'Más de 30 días', 'text-danger'))]
    return render(request, 'finanzas/rendicion_antiguedad.html', {'filas': filas, 'tramos': tramos,
                                                                   'vencidas': rd.vencidas()})


# ---------------------------------------------------------------- configuración
@login_required
def configuracion(request):
    from .models import CategoriaCuentaHistorial
    from core.models import ReglaAprobacion as ReglaMotor
    return render(request, 'finanzas/rendicion_config.html', {
        'categorias': CategoriaGasto.objects.select_related('cuenta_contable'),
        'politicas': PoliticaGasto.objects.prefetch_related('no_permitidas'),
        'reglas': ReglaAprobacion.objects.select_related('aprobador', 'centro_costo'),
        # APR-05: las reglas vigentes son las de Ajustes › Reglas de aprobación (un solo lugar)
        'reglas_motor': ReglaMotor.objects.filter(documento='RENDICION', activo=True).select_related(
            'aprobador', 'centro_costo', 'puesto', 'reemplazo').order_by('nivel', 'monto_desde'),
        'indicadores': IndicadorImpuesto.objects.all(), 'motivos': MotivoObservacion.objects.all(),
        'plantillas': PlantillaDistribucion.objects.prefetch_related('lineas__centro_costo'),
        'mapeos': MapeoCuentaERP.objects.select_related('cuenta'),
        'cambios': CategoriaCuentaHistorial.objects.select_related('categoria', 'cuenta', 'cuenta_anterior',
                                                                   'usuario')[:30]})


_CONFIG = {'categoria': (CategoriaGasto, CategoriaGastoForm, 'categoría de gasto'),
           'politica': (PoliticaGasto, PoliticaGastoForm, 'política de gastos'),
           'regla': (ReglaAprobacion, ReglaAprobacionForm, 'regla de aprobación'),
           'indicador': (IndicadorImpuesto, IndicadorImpuestoForm, 'indicador de impuesto'),
           'motivo': (MotivoObservacion, MotivoObservacionForm, 'motivo de observación'),
           'mapeo': (MapeoCuentaERP, MapeoCuentaERPForm, 'cuenta del ERP externo')}


@login_required
def config_editar(request, tipo, pk=None):
    if tipo == 'plantilla':
        return _plantilla(request, pk)
    if tipo not in _CONFIG:
        raise Http404
    modelo, form_cls, nombre = _CONFIG[tipo]
    obj = get_object_or_404(modelo, pk=pk) if pk else None
    cuenta_antes = obj.cuenta_contable if tipo == 'categoria' and obj else None
    form = form_cls(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        nuevo = form.save(commit=False)
        cuenta_nueva = getattr(nuevo, 'cuenta_contable', None)
        if cuenta_antes is not None and cuenta_nueva != cuenta_antes:
            # el cambio de imputación queda con su vigencia (los gastos anteriores conservan su cuenta)
            nuevo.cuenta_contable = cuenta_antes
            nuevo.save()
            rd.cambiar_cuenta_categoria(nuevo, cuenta_nueva, form.cleaned_data.get('vigente_desde') or timezone.localdate(),
                                        request.user, form.cleaned_data.get('motivo_cambio') or '')
            messages.info(request, 'Cambio de cuenta registrado con su vigencia; se avisó a contabilidad.')
        else:
            nuevo.save()
            form.save_m2m()
        messages.success(request, f'{nombre.capitalize()} guardada.')
        return redirect('rendiciones:configuracion')
    return render(request, 'core/form.html', {'form': form,
                                              'titulo': f'{"Editar" if obj else "Nueva"} {nombre}'})


def _plantilla(request, pk):
    obj = get_object_or_404(PlantillaDistribucion, pk=pk) if pk else PlantillaDistribucion()
    form = PlantillaDistribucionForm(request.POST or None, instance=obj)
    formset = PlantillaLineasFormSet(request.POST or None, instance=obj, prefix='l')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            p = form.save()
            formset.instance = p
            formset.save()
        messages.success(request, f'Plantilla «{p}» guardada.')
        return redirect('rendiciones:configuracion')
    return render(request, 'finanzas/rendicion_plantilla.html', {'form': form, 'formset': formset, 'obj': obj})
