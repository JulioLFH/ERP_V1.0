"""Pantallas del motor único de aprobación: reglas por documento (Ajustes › Aprobaciones), suplencias (cada usuario
delega sus aprobaciones mientras está de vacaciones) y la bandeja «Mis aprobaciones» con todo lo que le toca
aprobar hoy, sin importar el módulo del documento."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import aprobaciones as motor
from .forms import BootstrapMixin
from .models import Delegacion, ReglaAprobacion


class ReglaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ReglaAprobacion
        fields = ['documento', 'nivel', 'tipo', 'aprobador', 'puesto', 'niveles_arriba', 'reemplazo', 'monto_desde',
                  'centro_costo', 'aplica_a', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from contabilidad.models import CentroCosto
        self.fields['aprobador'].queryset = get_user_model().objects.filter(is_active=True).order_by('username')
        self.fields['reemplazo'].queryset = self.fields['aprobador'].queryset
        self.fields['centro_costo'].queryset = CentroCosto.para_formulario(self)

    def clean(self):
        d = super().clean()
        if d.get('tipo') == 'USUARIO' and not d.get('aprobador'):
            self.add_error('aprobador', 'Elija el usuario que aprueba.')
        if d.get('tipo') == 'PUESTO' and not d.get('puesto'):
            self.add_error('puesto', 'Elija el puesto que aprueba.')
        return d


@login_required
def reglas(request):
    if not request.user.is_superuser:
        from .permisos import puede
        if not (puede(request.user, 'compras.aprobar_oc') or puede(request.user, 'rendiciones.configurar')):
            return render(request, 'core/sin_acceso.html',
                          {'motivo': 'Solo el administrador configura las reglas de aprobación.'}, status=403)
    documento = request.GET.get('documento') or 'OC'
    regla = get_object_or_404(ReglaAprobacion, pk=request.GET['editar']) if request.GET.get('editar') else None
    form = ReglaForm(request.POST or None, instance=regla, initial={'documento': documento})
    if request.method == 'POST':
        if request.POST.get('quitar'):
            ReglaAprobacion.objects.filter(pk=request.POST['quitar']).update(activo=False)
            messages.success(request, 'Regla desactivada.')
            return redirect(f'{request.path}?documento={documento}')
        if form.is_valid():
            r = form.save()
            messages.success(request, 'Regla de aprobación guardada.')
            return redirect(f'{request.path}?documento={r.documento}')
    from contabilidad.models import CentroCosto
    return render(request, 'core/aprobaciones_reglas.html', {
        'form': form, 'regla': regla, 'documento': documento,
        'documentos': [(d, nombre, ReglaAprobacion.objects.filter(documento=d, activo=True).count())
                       for d, nombre in ReglaAprobacion.DOCUMENTOS],
        'reglas': ReglaAprobacion.objects.filter(documento=documento).select_related('aprobador', 'centro_costo',
                                                                                     'puesto'),
        'centros': CentroCosto.vigentes().select_related('responsable_usuario')})


class DelegacionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Delegacion
        fields = ['titular', 'suplente', 'desde', 'hasta', 'motivo']

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        activos = get_user_model().objects.filter(is_active=True).order_by('username')
        self.fields['suplente'].queryset = activos
        self.fields['titular'].queryset = activos if usuario.is_superuser else activos.filter(pk=usuario.pk)
        self.fields['titular'].initial = usuario.pk

    def clean(self):
        d = super().clean()
        if d.get('desde') and d.get('hasta') and d['hasta'] < d['desde']:
            self.add_error('hasta', 'Debe ser posterior al inicio.')
        if d.get('titular') and d.get('titular') == d.get('suplente'):
            self.add_error('suplente', 'El suplente debe ser otra persona.')
        return d


@login_required
def delegaciones(request):
    """Suplencias: el usuario delega sus aprobaciones por un periodo (el administrador, las de cualquiera)."""
    form = DelegacionForm(request.POST or None, usuario=request.user)
    if request.method == 'POST':
        if request.POST.get('quitar'):
            Delegacion.objects.filter(pk=request.POST['quitar']).filter(
                **({} if request.user.is_superuser else {'titular': request.user})).update(activo=False)
            messages.success(request, 'Delegación terminada.')
            return redirect('aprobaciones_delegaciones')
        if form.is_valid():
            form.save()
            messages.success(request, 'Delegación registrada: el suplente aprobará lo suyo en esas fechas.')
            return redirect('aprobaciones_delegaciones')
    qs = Delegacion.objects.select_related('titular', 'suplente')
    if not request.user.is_superuser:
        from django.db.models import Q
        qs = qs.filter(Q(titular=request.user) | Q(suplente=request.user))
    return render(request, 'core/aprobaciones_delegaciones.html', {'form': form, 'delegaciones': qs[:100],
                                                                   'hoy': timezone.localdate()})


def _pendientes(user):
    """[(código, documento, objeto, nivel, url, detalle)] que el usuario puede aprobar hoy."""
    from django.urls import reverse

    from compras.aprobacion_oc import puede_aprobar as oc_puede
    from compras.models import OrdenCompra, SolicitudPedido
    from compras.solped import puede_liberar
    from inventario.models import RequerimientoInterno
    from inventario.requerimientos import puede_aprobar as req_puede
    from planillas.models import MovimientoPersonal, Vacacion
    from planillas.organizacion import puede_aprobar as mov_puede, puede_aprobar_vacacion
    filas = []
    for oc in OrdenCompra.objects.filter(estado='PENDIENTE').exclude(niveles=[]).select_related('tercero'):
        if oc_puede(user, oc):
            filas.append(('OC', 'Orden de compra', oc, motor.pendiente(oc.niveles, oc.nivel_aprobado),
                          reverse('compras:oc_detalle', args=[oc.pk]), f'{oc.tercero.nombre} · S/ {oc.total:,.2f}'))
    for sp in SolicitudPedido.objects.filter(estado='ENVIADA').select_related('centro_costo'):
        if puede_liberar(user, sp):
            filas.append(('SOLPED', 'Solicitud de pedido', sp, sp.nivel_pendiente, reverse('solicitudes:detalle', args=[sp.pk]),
                          f'{sp.centro_costo.codigo} · {sp.motivo[:60]}'))
    for r in RequerimientoInterno.objects.filter(estado='ENVIADO').exclude(niveles=[]).select_related('centro_costo'):
        if req_puede(user, r):
            filas.append(('REQUERIMIENTO', 'Requerimiento', r, motor.pendiente(r.niveles, r.nivel_aprobado),
                          reverse('requerimientos:detalle', args=[r.pk]), f'{r.centro_costo.codigo} · {r.motivo[:60]}'))
    for v in Vacacion.objects.filter(estado='SOLICITADA').select_related('trabajador'):
        if puede_aprobar_vacacion(user, v):
            filas.append(('VACACION', 'Vacaciones / permiso', v, motor.pendiente(v.niveles, v.nivel_aprobado),
                          reverse('planillas:solicitudes_personal'), f'{v.dias} día(s)'))
    for m in MovimientoPersonal.objects.filter(estado='ENVIADO').select_related('trabajador'):
        if mov_puede(user, m):
            filas.append(('MOVIMIENTO', 'Movimiento de personal', m, motor.pendiente(m.niveles, m.nivel_aprobado),
                          reverse('planillas:movimiento', args=[m.pk]), m.motivo[:60]))
    # APR-03: también pedidos de venta, conformidades de servicio y rendiciones de gastos
    from compras.hes import puede_liberar as hes_puede
    from compras.models import ConformidadServicio
    from ventas.aprobacion_pedido import enviado_a_aprobacion, estado_aprobacion
    from ventas.models import Cotizacion
    for cot in Cotizacion.objects.filter(tipo='PED', estado='PENDIENTE').select_related('tercero'):
        _, enviar, espera = estado_aprobacion(cot, user)
        if not enviar and not espera and enviado_a_aprobacion(cot):
            filas.append(('PEDIDO', 'Pedido de venta', cot, motor.pendiente(cot.niveles, cot.nivel_aprobado),
                          reverse('ventas:cot_detalle', args=[cot.pk]),
                          f'{cot.tercero.nombre} · S/ {cot.total:,.2f} · {cot.motivo_aprobacion[:60]}'))
    for h in ConformidadServicio.objects.filter(estado='POR_LIBERAR').select_related('orden_compra__tercero'):
        if hes_puede(user, h):
            filas.append(('HES', 'Conformidad de servicio', h, motor.pendiente(h.niveles, h.nivel_aprobado),
                          reverse('compras:hes', args=[h.pk]),
                          f'{h.orden_compra.tercero.nombre} · {h.descripcion[:50]} · S/ {h.valor_pen:,.2f}'))
    try:
        from finanzas.models_rendicion import Rendicion
        from finanzas.rendiciones import puede_aprobar as rend_puede
        for r in Rendicion.objects.filter(estado__in=Rendicion.POR_APROBAR).select_related('rendidor'):
            if rend_puede(user, r):
                filas.append(('RENDICION', 'Rendición de gastos', r, r.nivel_pendiente,
                              reverse('rendiciones:detalle', args=[r.pk]),
                              f'{r.rendidor.get_full_name() or r.rendidor.username} · '
                              f'S/ {r.total:,.2f}'))
    except ImportError:  # pragma: no cover
        pass
    return filas


@login_required
def bandeja(request):
    """Todo lo que el usuario puede aprobar hoy (como titular, suplente o porque el nivel escaló)."""
    if request.method == 'POST':  # aprobar o rechazar desde la bandeja (también desde el celular)
        doc, pk, accion = request.POST.get('doc'), request.POST.get('pk'), request.POST.get('accion')
        comentario = request.POST.get('comentario', '')
        from compras.aprobacion_oc import ErrorAprobacionOC
        from compras.solped import ErrorSolped
        from inventario.requerimientos import ErrorRequerimiento
        from planillas.organizacion import ErrorPersonal
        from compras.hes import ErrorHES
        from finanzas.rendiciones import ErrorRendicion
        from ventas.aprobacion_pedido import ErrorAprobacionPedido
        try:
            messages.success(request, _resolver(request.user, doc, pk, accion, comentario))
        except (ErrorAprobacionOC, ErrorSolped, ErrorRequerimiento, ErrorPersonal, motor.ErrorAprobacion, ErrorHES,
                ErrorRendicion, ErrorAprobacionPedido) as exc:
            messages.error(request, str(exc))
        return redirect('aprobaciones_bandeja')
    filas = _pendientes(request.user)
    trabados = motor.sin_aprobador() if request.user.is_superuser else []  # APR-02
    return render(request, 'core/aprobaciones_bandeja.html', {'filas': filas, 'trabados': trabados})


def _resolver(user, doc, pk, accion, comentario):
    aprobar = accion == 'aprobar'
    if doc == 'OC':
        from compras import aprobacion_oc
        from compras.models import OrdenCompra
        oc = get_object_or_404(OrdenCompra, pk=pk)
        return aprobacion_oc.aprobar(oc, user, comentario) if aprobar else aprobacion_oc.rechazar(oc, user, comentario)
    if doc == 'SOLPED':
        from compras import solped
        from compras.models import SolicitudPedido
        sp = get_object_or_404(SolicitudPedido, pk=pk)
        (solped.liberar if aprobar else solped.rechazar)(sp, user, comentario)
        return f'{sp}: {sp.get_estado_display().lower()}.'
    if doc == 'REQUERIMIENTO':
        from inventario import requerimientos
        from inventario.models import RequerimientoInterno
        r = get_object_or_404(RequerimientoInterno, pk=pk)
        if aprobar:
            requerimientos.aprobar(r, user, comentario)  # el comentario es el motivo si aprueba en lugar de otro
        else:
            requerimientos.rechazar(r, user, comentario)
        return f'{r}: {r.get_estado_display().lower()}.'
    if doc == 'VACACION':
        from planillas import organizacion
        from planillas.models import Vacacion
        v = get_object_or_404(Vacacion, pk=pk)
        organizacion.resolver_vacacion(v, user, aprobar, comentario)
        return f'Solicitud de {v.trabajador.nombre_completo}: {v.get_estado_display().lower()}.'
    if doc == 'MOVIMIENTO':
        from planillas import organizacion
        from planillas.models import MovimientoPersonal
        m = get_object_or_404(MovimientoPersonal, pk=pk)
        (organizacion.aprobar if aprobar else organizacion.rechazar)(m, user, comentario)
        return f'{m}: {m.get_estado_display().lower()}.'
    if doc == 'PEDIDO':
        from ventas import aprobacion_pedido
        from ventas.models import Cotizacion
        cot = get_object_or_404(Cotizacion, pk=pk, tipo='PED')
        if aprobar:
            return aprobacion_pedido.aprobar(cot, user)
        if len((comentario or '').strip()) < 5:
            raise motor.ErrorAprobacion('Indique el motivo del rechazo.')
        motor.registrar('PEDIDO', cot, user, 'RECHAZADA', comentario,
                        (motor.pendiente(cot.niveles, cot.nivel_aprobado) or {}).get('nivel'))
        cot.niveles, cot.nivel_aprobado = [], 0  # vuelve al vendedor para corregirlo y enviarlo otra vez
        cot.save(update_fields=['niveles', 'nivel_aprobado'])
        return f'{cot.numero}: rechazado; vuelve al vendedor.'
    if doc == 'HES':
        from compras import hes
        from compras.models import ConformidadServicio
        h = get_object_or_404(ConformidadServicio, pk=pk)
        hes.resolver(h, user, 'liberar' if aprobar else 'rechazar', comentario)
        return f'{h}: {h.get_estado_display().lower()}.'
    if doc == 'RENDICION':
        from finanzas import rendiciones
        from finanzas.models_rendicion import Rendicion
        r = get_object_or_404(Rendicion, pk=pk)
        if aprobar:
            rendiciones.aprobar(r, user, comentario)
        else:
            rendiciones.rechazar(r, user, comentario)
        return f'{r}: {r.get_estado_display().lower()}.'
    raise motor.ErrorAprobacion('Documento no válido.')
