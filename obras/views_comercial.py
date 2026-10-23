"""Obras › Comercial: licitaciones y propuestas (CRM de constructoras) y proyectos inmobiliarios (unidades,
separaciones y ventas, cobro de cuotas con su comprobante de anticipo y entregas)."""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core import sustentos
from core.permisos import puede
from core.utils import excel_response, fmt_fecha

from . import inmobiliario as inm
from . import licitaciones as lic_srv
from .forms import (CobroForm, HitoForm, LicitacionForm, LicitacionNuevaForm, PerderForm, RequisitoForm, SepararForm,
                    UnidadForm)
from .models import CuotaInmueble, Licitacion, Obra, Unidad, VentaInmueble


# ---------------------------------------------------------------- licitaciones
@login_required
def licitaciones(request):
    datos = lic_srv.reportes()
    if request.GET.get('formato') == 'excel':
        return excel_response('Licitaciones', 'Cartera en licitación', [
            'Obra', 'Entidad / cliente', 'Procedimiento', 'N°', 'Valor referencial', 'Oferta', '% del VR',
            'Próximo hito', 'Fecha', 'Requisitos pendientes', 'Costo de la propuesta'],
            [[x.obra.nombre, x.obra.cliente.nombre if x.obra.cliente_id else '', x.get_procedimiento_display(),
              x.numero, x.valor_referencial, x.oferta.total if x.oferta else None, x.pct_referencial,
              x.proximo.get_tipo_display() if x.proximo else '', fmt_fecha(x.proximo.fecha) if x.proximo else '',
              x.pendientes, x.costo] for x in datos['abiertas']])
    datos['hitos'], datos['hoy'] = lic_srv.proximos_hitos(7), timezone.localdate()
    return render(request, 'obras/licitaciones.html', datos)


@login_required
def licitacion_nueva(request):
    form = LicitacionNuevaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = dict(form.cleaned_data)
        nombre, cliente = d.pop('nombre'), d.pop('cliente')
        d['valor_referencial'] = d.get('valor_referencial') or 0
        lic = lic_srv.crear(nombre, cliente, request.user, **d)
        messages.success(request, f'Licitación registrada: obra {lic.obra.codigo} en «Propuesta / licitación» y su '
                                  f'oportunidad en el CRM. Agregue el cronograma, los requisitos y el presupuesto de '
                                  f'oferta.')
        return redirect('obras:licitacion', lic.obra_id)
    return render(request, 'core/form.html', {
        'form': form, 'titulo': 'Nueva licitación / propuesta',
        'ayuda': 'Se crea la obra en «Propuesta / licitación» (su centro de costo va a Propuestas, gasto de ventas) y '
                 'la oportunidad en el embudo del CRM. Al ganar, la obra pasa a Adjudicada y la oferta se copia como '
                 'presupuesto contractual.'})


@login_required
def desde_oportunidad(request, pk):
    from ventas.models import Oportunidad
    o = get_object_or_404(Oportunidad.objects.select_related('tercero'), pk=pk)
    existente = Licitacion.objects.filter(oportunidad=o).first()
    if existente is not None:
        return redirect('obras:licitacion', existente.obra_id)
    if request.method != 'POST':
        return redirect('ventas:crm_oportunidad', pk)
    if not o.tercero_id:
        messages.error(request, 'Registre primero al cliente de la oportunidad: la obra necesita su entidad o cliente.')
    elif o.etapa in ('GANADA', 'PERDIDA'):
        messages.error(request, f'La oportunidad ya está {o.get_etapa_display().lower()}.')
    else:
        lic = lic_srv.crear(o.nombre, o.tercero, request.user, oportunidad=o, valor_referencial=o.monto or 0)
        messages.success(request, f'Obra {lic.obra.codigo} creada en «Propuesta / licitación» desde la oportunidad.')
        return redirect('obras:licitacion', lic.obra_id)
    return redirect('ventas:crm_oportunidad', pk)


def _accion_licitacion(request, obra, lic, accion, forms_):
    if accion == 'iniciar':
        if lic is not None or obra.estado != 'LICITACION':
            messages.error(request, 'La obra ya tiene licitación o no está en «Propuesta / licitación».')
        else:
            lic_srv.iniciar(obra, request.user)
            messages.success(request, 'Licitación registrada y oportunidad creada en el CRM.')
        return True
    if lic is None:
        return True
    abierta = lic.resultado == 'EN_CURSO'
    if accion == 'datos' and forms_['datos'].is_valid():
        forms_['datos'].save()
        lic_srv.sincronizar_oportunidad(lic)
        messages.success(request, 'Datos del proceso guardados.')
        return True
    if accion == 'hito' and forms_['hito'].is_valid():
        h = forms_['hito'].save(commit=False)
        h.licitacion = lic
        h.save()
        return True
    if accion in ('hito_hecho', 'hito_borrar'):
        h = lic.hitos.filter(pk=request.POST.get('id')).first()
        if h is not None and accion == 'hito_hecho':
            h.hecho = not h.hecho
            h.save(update_fields=['hecho'])
        elif h is not None:
            h.delete()
        return True
    if accion == 'requisito' and forms_['requisito'].is_valid():
        r = forms_['requisito'].save(commit=False)
        r.licitacion = lic
        r.save()
        archivo = forms_['requisito'].cleaned_data.get('archivo')
        if archivo:
            try:
                sustentos.adjuntar(r, archivo, request.user, r.descripcion)
            except sustentos.ErrorSustento as exc:
                messages.warning(request, f'Requisito guardado sin el documento: {exc}')
        return True
    if accion in ('cumple', 'requisito_borrar'):
        r = lic.requisitos.filter(pk=request.POST.get('id')).first()
        if r is not None and accion == 'cumple':
            r.cumple = not r.cumple
            r.save(update_fields=['cumple'])
        elif r is not None and not sustentos.tiene(r):
            r.delete()
        elif r is not None:
            messages.error(request, 'El requisito tiene documentos adjuntos: no se elimina (márquelo como no cumple).')
        return True
    if accion == 'ganar' and abierta:
        try:
            lic_srv.ganar(lic)
            messages.success(request, f'¡Buena pro! La obra pasó a Adjudicada'
                                      f'{" y la oferta se copió como presupuesto contractual" if lic.oferta else ""}.')
        except lic_srv.ErrorLicitacion as exc:
            messages.error(request, str(exc))
        return True
    if accion == 'perder' and abierta and forms_['perder'].is_valid():
        d = forms_['perder'].cleaned_data
        try:
            lic_srv.perder(lic, d.get('motivo') or '', d.get('detalle') or '', d.get('ganador') or '',
                           d.get('precio_ganador'), desierta=d.get('desierta'))
            messages.info(request, 'Resultado registrado: la obra quedó como no adjudicada.')
        except lic_srv.ErrorLicitacion as exc:
            messages.error(request, str(exc))
        return True
    return False


@login_required
def licitacion(request, pk):
    obra = get_object_or_404(Obra.objects.select_related('cliente', 'centro_costo'), pk=pk)
    lic = Licitacion.objects.filter(obra=obra).select_related('oportunidad').first()
    accion = request.POST.get('accion') if request.method == 'POST' else None
    forms_ = {
        'datos': LicitacionForm(request.POST if accion == 'datos' else None, instance=lic),
        'hito': HitoForm(request.POST if accion == 'hito' else None, initial={'fecha': timezone.localdate()}),
        'requisito': RequisitoForm(request.POST if accion == 'requisito' else None,
                                   request.FILES if accion == 'requisito' else None),
        'perder': PerderForm(request.POST if accion == 'perder' else None),
    }
    if accion and _accion_licitacion(request, obra, lic, accion, forms_):
        return redirect('obras:licitacion', obra.pk)
    if lic is not None and lic.resultado == 'EN_CURSO':
        lic_srv.sincronizar_oportunidad(lic)
    requisitos = list(lic.requisitos.all()) if lic else []
    for r in requisitos:
        r.archivos = list(sustentos.de(r))
    hoy = timezone.localdate()
    return render(request, 'obras/licitacion.html', {
        'o': obra, 'lic': lic, 'f': forms_, 'hitos': lic.hitos.all() if lic else [], 'requisitos': requisitos,
        'oferta': lic.oferta if lic else None, 'costo': lic_srv.costo_propuesta(obra) if lic else None,
        'hoy': hoy, 'aviso': hoy + timedelta(days=lic_srv.DIAS_AVISO)})


# ---------------------------------------------------------------- proyecto inmobiliario
def _numero(texto):
    """'385,000.50', '385000', '72,5' → Decimal (la coma es de miles si le siguen grupos de 3 dígitos)."""
    texto = (texto or '').strip().replace(' ', '')
    if not texto:
        return Decimal('0')
    if '.' in texto or re.fullmatch(r'-?\d{1,3}(,\d{3})+', texto):
        texto = texto.replace(',', '')
    else:
        texto = texto.replace(',', '.')
    return Decimal(texto)


TIPOS_UNIDAD = {n.lower(): c for c, n in Unidad.TIPOS} | {c.lower(): c for c, _ in Unidad.TIPOS} | {
    'dpto': 'DEPARTAMENTO', 'depa': 'DEPARTAMENTO', 'est': 'ESTACIONAMIENTO', 'cochera': 'ESTACIONAMIENTO',
    'dep': 'DEPOSITO', 'depósito': 'DEPOSITO', 'local': 'LOCAL'}


@transaction.atomic
def _carga_rapida(obra, texto):
    """Líneas «código; tipo; piso; área techada; área libre; dormitorios; precio de lista» (o pegadas desde Excel)."""
    creadas, errores = 0, []
    for n, linea in enumerate(texto.splitlines(), 1):
        if not linea.strip():
            continue
        partes = [p.strip() for p in re.split(r'\t|;', linea)]
        partes += [''] * (7 - len(partes))
        codigo = partes[0].upper()[:20]
        if not codigo or codigo.lower() in ('código', 'codigo'):
            continue
        if Unidad.objects.filter(obra=obra, codigo=codigo).exists():
            errores.append(f'Línea {n}: {codigo} ya existe.')
            continue
        tipo = TIPOS_UNIDAD.get(partes[1].lower(), 'DEPARTAMENTO' if not partes[1] else None)
        if tipo is None:
            errores.append(f'Línea {n}: tipo «{partes[1]}» no reconocido.')
            continue
        try:
            techada, libre, precio = _numero(partes[3]), _numero(partes[4]), _numero(partes[6])
            dormitorios = int(_numero(partes[5]))
        except (InvalidOperation, ValueError):
            errores.append(f'Línea {n}: revise las áreas, los dormitorios y el precio.')
            continue
        Unidad.objects.create(obra=obra, codigo=codigo, tipo=tipo, piso=partes[2][:10], area_techada=techada,
                              area_libre=libre, dormitorios=dormitorios, precio_lista=precio)
        creadas += 1
    return creadas, errores


def _obra_inmobiliaria(pk):
    obra = get_object_or_404(Obra.objects.select_related('cliente', 'cuenta_recaudadora'), pk=pk)
    return obra


@login_required
def unidades(request, pk):
    obra = _obra_inmobiliaria(pk)
    form = UnidadForm(request.POST if request.POST.get('accion') == 'unidad' else None, obra=obra)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if obra.tipo != 'INMOBILIARIA':
            messages.error(request, 'Cambie el tipo de la obra a «Proyecto inmobiliario» para registrar unidades.')
            return redirect('obras:unidades', pk)
        if accion == 'unidad' and form.is_valid():
            u = form.save(commit=False)
            u.obra = obra
            u.save()
            messages.success(request, f'Unidad {u.codigo} registrada.')
            return redirect('obras:unidades', pk)
        if accion == 'carga':
            creadas, errores = _carga_rapida(obra, request.POST.get('lineas', ''))
            if creadas:
                messages.success(request, f'{creadas} unidad(es) registradas.')
            for e in errores[:10]:
                messages.warning(request, e)
            return redirect('obras:unidades', pk)
        if accion in ('bloquear', 'liberar', 'borrar', 'precio'):
            u = obra.unidades.filter(pk=request.POST.get('id')).first()
            if u is None:
                return redirect('obras:unidades', pk)
            if accion == 'precio':
                try:
                    u.precio_lista = _numero(request.POST.get('precio_lista'))
                    u.save(update_fields=['precio_lista'])
                except InvalidOperation:
                    messages.error(request, 'Precio no válido.')
            elif accion == 'bloquear' and u.estado == 'DISPONIBLE':
                Unidad.objects.filter(pk=u.pk).update(estado='BLOQUEADA')
            elif accion == 'liberar' and u.estado == 'BLOQUEADA':
                Unidad.objects.filter(pk=u.pk).update(estado='DISPONIBLE')
            elif accion == 'borrar' and u.estado in ('DISPONIBLE', 'BLOQUEADA') and not u.ventas.exists():
                u.delete()
            else:
                messages.error(request, 'La unidad ya tiene ventas: no se modifica su estado desde aquí.')
            return redirect('obras:unidades', pk)
    t = inm.tablero(obra)
    if request.GET.get('formato') == 'excel':
        return excel_response(f'Unidades_{obra.codigo}', f'Unidades del proyecto {obra.nombre}', [
            'Código', 'Tipo', 'Piso', 'Área techada', 'Área libre', 'Dorm.', 'Precio de lista', 'Estado', 'Comprador',
            'Precio (con IGV)', 'Valor sin IGV', 'Costo asignado', 'Margen', 'Margen %'],
            [[u.codigo, u.get_tipo_display(), u.piso, u.area_techada, u.area_libre, u.dormitorios, u.precio_lista,
              u.get_estado_display(), u.venta.cliente.nombre if u.venta else '', u.precio, u.valor, u.costo, u.margen,
              u.margen_pct] for u in t['unidades']])
    ventas = obra.ventas_inmuebles.select_related('cliente').prefetch_related('unidades', 'cuotas__cobros')
    return render(request, 'obras/unidades.html', {'o': obra, 't': t, 'form': form, 'ventas': ventas,
                                                   'estados': Unidad.ESTADOS})


@login_required
def separar(request, pk):
    obra = _obra_inmobiliaria(pk)
    if obra.tipo != 'INMOBILIARIA':
        messages.error(request, 'La obra no es un proyecto inmobiliario.')
        return redirect('obras:obra', pk)
    hoy = timezone.localdate()
    form = SepararForm(request.POST or None, obra=obra, initial={
        'fecha': hoy, 'vence_separacion': hoy + timedelta(days=15), 'unidades': request.GET.getlist('unidad')})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        unidades_ = list(d['unidades'])
        precio = d.get('precio') or sum((u.precio_lista for u in unidades_), Decimal('0'))
        try:
            filas = inm.cronograma(precio, d['financiamiento'], d['fecha'], d.get('separacion'), d.get('inicial'),
                                   d.get('fecha_inicial'), d.get('cuotas'), d.get('primera_cuota'), d.get('bono'),
                                   d.get('fecha_banco'))
            v = inm.separar(obra, unidades_, d['cliente'], precio, request.user, filas, d['financiamiento'],
                            d.get('banco') or '', d['fecha'], d.get('vence_separacion'), d.get('oportunidad'),
                            notas=d.get('notas') or '')
            messages.success(request, f'Venta {v.numero} registrada como separación con {v.cuotas.count()} cuota(s). '
                                      f'Cobre la separación para emitir su comprobante.')
            return redirect('obras:venta_inmueble', v.pk)
        except inm.ErrorInmobiliario as exc:
            form.add_error(None, str(exc))
    return render(request, 'core/form.html', {
        'form': form, 'titulo': f'Separar unidades · {obra.nombre}',
        'ayuda': 'El cronograma se arma con la separación, la cuota inicial, el bono y el saldo: en cuotas mensuales '
                 '(financiamiento directo), con el desembolso del banco (crédito hipotecario o MiVivienda) o al '
                 'contado en la fecha de la cuota inicial. Cada cobro emite su comprobante de anticipo.'})


@login_required
def venta_inmueble(request, pk):
    v = get_object_or_404(VentaInmueble.objects.select_related('obra', 'cliente', 'vendedor', 'oportunidad'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            fecha = request.POST.get('fecha')
            fecha = date.fromisoformat(fecha) if fecha else None
            if accion == 'minuta':
                inm.firmar_minuta(v, fecha)
                messages.success(request, 'Minuta firmada: la venta pasó a Vendida.')
            elif accion == 'entregar':
                inm.entregar(v, fecha)
                messages.success(request, f'Unidades entregadas: ingreso S/ {v.ingreso:,.2f} y costo S/ {v.costo:,.2f} '
                                          f'(se contabilizan al centralizar el mes).')
            elif accion == 'anular':
                inm.anular(v, request.POST.get('motivo', ''))
                messages.info(request, 'Venta anulada: las unidades vuelven a estar disponibles.')
        except (inm.ErrorInmobiliario, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect('obras:venta_inmueble', pk)
    cuotas = list(v.cuotas.prefetch_related('cobros__comprobante', 'cobros__movimiento'))
    return render(request, 'obras/venta_inmueble.html', {
        'v': v, 'cuotas': cuotas, 'unidades': v.unidades.all(), 'hoy': timezone.localdate(),
        'cobrado': sum((c.cobrado for c in cuotas), Decimal('0')), 'margen': v.ingreso - v.costo,
        'puede_cobrar': puede(request.user, 'tesoreria.registrar') and puede(request.user, 'obras.vender')})


@login_required
def cobrar(request, pk):
    cuota = get_object_or_404(CuotaInmueble.objects.select_related('venta__obra__cuenta_recaudadora',
                                                                    'venta__cliente'), pk=pk)
    v = cuota.venta
    if not puede(request.user, 'tesoreria.registrar'):
        messages.error(request, 'Registrar el cobro requiere el permiso de Tesorería «registrar cobros y pagos».')
        return redirect('obras:venta_inmueble', v.pk)
    form = CobroForm(request.POST or None, request.FILES or None, initial={
        'monto': cuota.saldo, 'fecha': timezone.localdate(), 'cuenta': v.obra.cuenta_recaudadora_id,
        'medio_pago': 'TRANSFERENCIA'})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            cobro = inm.cobrar(cuota, d['monto'], d['fecha'], d['cuenta'], request.user, d['medio_pago'],
                               d.get('numero_operacion') or '', d.get('sustento'))
            messages.success(request, f'Cobro registrado con el comprobante {cobro.comprobante.numero_completo} '
                                      f'(anticipo) y su ingreso en {cobro.movimiento.cuenta}.')
            return redirect('obras:venta_inmueble', v.pk)
        except inm.ErrorInmobiliario as exc:
            form.add_error(None, str(exc))
    tipo = 'factura' if v.cliente.tipo_doc == '6' else 'boleta'
    return render(request, 'core/form.html', {
        'form': form, 'titulo': f'Cobrar {cuota} · {v.numero} {v.cliente.nombre}',
        'ayuda': f'Saldo de la cuota: S/ {cuota.saldo:,.2f}. Se emite una {tipo} de anticipo: la construcción con IGV '
                 f'y el terreno ({v.terreno_pct:g} % del valor) inafecto; el ingreso queda como anticipo hasta la '
                 f'entrega.'})


@login_required
def cobranza(request):
    hoy = timezone.localdate()
    obra_id = request.GET.get('obra')
    vencidas = list(inm.cuotas_vencidas())
    proximas = [c for c in CuotaInmueble.objects.exclude(venta__estado='ANULADA')
                .filter(vence__range=[hoy, hoy + timedelta(days=30)])
                .select_related('venta__cliente', 'venta__obra').prefetch_related('cobros').order_by('vence')
                if c.saldo > 0]
    if obra_id:
        vencidas = [c for c in vencidas if str(c.venta.obra_id) == obra_id]
        proximas = [c for c in proximas if str(c.venta.obra_id) == obra_id]
    for c in vencidas:
        c.dias = (hoy - c.vence).days
        c.pendiente = c.monto - c.pagado
    if request.GET.get('formato') == 'excel':
        return excel_response('Cobranza_inmobiliaria', 'Cuotas inmobiliarias vencidas', [
            'Proyecto', 'Venta', 'Comprador', 'Cuota', 'Vence', 'Días de atraso', 'Monto', 'Saldo'],
            [[c.venta.obra.nombre, c.venta.numero, c.venta.cliente.nombre, str(c), fmt_fecha(c.vence), c.dias,
              c.monto, c.pendiente] for c in vencidas])
    return render(request, 'obras/cobranza.html', {
        'vencidas': vencidas, 'proximas': proximas, 'separaciones': inm.separaciones_por_vencer(7),
        'total_vencido': sum((c.pendiente for c in vencidas), Decimal('0')), 'hoy': hoy,
        'obras': Obra.objects.filter(tipo='INMOBILIARIA'), 'obra_id': obra_id})
