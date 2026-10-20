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
        fields = ['documento', 'nivel', 'tipo', 'aprobador', 'puesto', 'niveles_arriba', 'monto_desde',
                  'centro_costo', 'aplica_a', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from contabilidad.models import CentroCosto
        self.fields['aprobador'].queryset = get_user_model().objects.filter(is_active=True).order_by('username')
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)

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
        'centros': CentroCosto.objects.filter(activo=True).select_related('responsable_usuario')})


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
        try:
            messages.success(request, _resolver(request.user, doc, pk, accion, comentario))
        except (ErrorAprobacionOC, ErrorSolped, ErrorRequerimiento, ErrorPersonal, motor.ErrorAprobacion) as exc:
            messages.error(request, str(exc))
        return redirect('aprobaciones_bandeja')
    filas = _pendientes(request.user)
    rendiciones = 0
    try:
        from finanzas.models_rendicion import Rendicion
        from finanzas.rendiciones import puede_aprobar as rend_puede
        rendiciones = sum(1 for r in Rendicion.objects.filter(estado__in=Rendicion.POR_APROBAR)
                          if rend_puede(request.user, r))
    except ImportError:
        pass
    return render(request, 'core/aprobaciones_bandeja.html', {'filas': filas, 'rendiciones': rendiciones})


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
            requerimientos.aprobar(r, user)
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
    raise motor.ErrorAprobacion('Documento no válido.')
