"""Pantallas de flota y equipos. La lista y la ficha del equipo se abren desde Logística (flota:…) y desde
Manufactura › Mantenimiento (manufactura:equipos / manufactura:equipo): es el mismo maestro."""
from decimal import InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.modulos import modulos_del_usuario
from core.permisos import puede

from core.utils import leer_numero

from . import servicios as srv
from .forms import ComponenteForm, DocumentoForm, EquipoForm
from .models import DocumentoFlota, LecturaEquipo


def _fecha(texto):
    from datetime import date
    try:
        return date.fromisoformat(texto or '')
    except ValueError:
        return timezone.localdate()


def puede_flota(user):
    """Registra en la flota quien tiene la acción de Logística o, desde Manufactura, la de mantenimiento."""
    return puede(user, 'logistica.flota') or puede(user, 'manufactura.mantenimiento')


def accion_flota(request):
    """Acción exigida a las rutas de flota (core/permisos.RUTAS)."""
    if request.method != 'POST':
        return None
    return 'logistica.flota' if 'logistica' in modulos_del_usuario(request.user) else 'manufactura.mantenimiento'


def _ns(request):
    return request.resolver_match.namespace or 'flota'


@login_required
def equipos(request):
    from produccion.models import Equipo, PlanMantenimiento
    qs = Equipo.objects.select_related('centro', 'responsable').annotate(
        n_ordenes=Count('ordenes', distinct=True),
        parada=Sum('ordenes__horas_parada', filter=Q(ordenes__estado='CERRADA')))
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(codigo__icontains=q) | Q(nombre__icontains=q) | Q(placa__icontains=q))
    for campo in ('tipo', 'estado'):
        if request.GET.get(campo):
            qs = qs.filter(**{campo: request.GET[campo]})
    hoy = timezone.localdate()
    vencidos = [p for p in PlanMantenimiento.objects.filter(activo=True, equipo__activo=True).select_related('equipo')
                if p.vence(hoy)]
    docs, licencias = srv.por_vencer()
    con_alerta = {d.equipo_id for d in docs if d.equipo_id}
    equipos = list(qs)
    for e in equipos:
        e.doc_alerta = e.pk in con_alerta
    return render(request, 'flota/equipos.html', {
        'equipos': equipos, 'vencidos': vencidos, 'ns': _ns(request), 'tipos': Equipo.TIPOS,
        'estados': Equipo.ESTADOS, 'n_docs': len(docs) + len(licencias), 'puede_flota': puede_flota(request.user)})


@login_required
def equipo(request, pk=None):
    from produccion.forms_planta import PlanForm
    from produccion.models import Equipo
    eq = get_object_or_404(Equipo, pk=pk) if pk else Equipo()
    ns = _ns(request)
    accion = request.POST.get('accion')
    form = EquipoForm(request.POST if accion == 'equipo' else None, instance=eq)
    plan_form = PlanForm(request.POST if accion == 'plan' else None, equipo=eq)
    comp_form = ComponenteForm(request.POST if accion == 'componente' else None, equipo=eq, prefix='comp')
    doc_form = DocumentoForm(request.POST if accion == 'documento' else None,
                             request.FILES if accion == 'documento' else None, prefix='doc')
    if request.method == 'POST':
        if not puede_flota(request.user):
            messages.error(request, 'Su usuario no registra equipos.')
            return redirect(f'{ns}:equipo', eq.pk) if eq.pk else redirect(f'{ns}:equipos')
        if accion == 'equipo' and form.is_valid():
            eq = form.save()
            srv.vehiculo_de_equipo(eq)
            messages.success(request, f'Equipo {eq} guardado.')
            return redirect(f'{ns}:equipo', eq.pk)
        if accion == 'plan' and eq.pk and plan_form.is_valid():
            plan = plan_form.save(commit=False)
            plan.equipo = eq
            plan.save()
            messages.success(request, 'Plan preventivo agregado.')
            return redirect(f'{ns}:equipo', eq.pk)
        if accion == 'quitar_plan' and eq.pk:
            eq.planes.filter(pk=request.POST.get('plan')).update(activo=False)
            return redirect(f'{ns}:equipo', eq.pk)
        if accion == 'componente' and eq.pk and comp_form.is_valid():
            c = comp_form.save(commit=False)
            c.equipo = eq
            c.save()
            messages.success(request, f'Componente {c} agregado.')
            return redirect(f'{ns}:equipo', eq.pk)
        if accion == 'quitar_componente' and eq.pk:
            eq.componentes.filter(pk=request.POST.get('componente')).update(activo=False)
            return redirect(f'{ns}:equipo', eq.pk)
        if accion == 'lectura' and eq.pk:
            from .lecturas import ErrorLectura, registrar
            try:
                registrar(eq, leer_numero(request.POST.get('valor')), _fecha(request.POST.get('fecha')),
                          request.user, 'PARTE', observacion=request.POST.get('observacion', ''))
                messages.success(request, 'Lectura registrada: queda por aprobar.')
            except ErrorLectura as exc:
                messages.error(request, str(exc))
            except InvalidOperation:
                messages.error(request, 'Lectura inválida.')
            return redirect(f'{ns}:equipo', eq.pk)
        if accion == 'documento' and eq.pk and doc_form.is_valid():
            doc = doc_form.save(commit=False)
            doc.equipo = eq
            try:
                srv.registrar_documento(doc, request.user, doc_form.cleaned_data.get('archivo_subido'))
                messages.success(request, f'{doc} registrado.')
                return redirect(f'{ns}:equipo', eq.pk)
            except srv.ErrorFlota as exc:
                messages.error(request, str(exc))
    contexto = {'eq': eq, 'form': form, 'plan_form': plan_form, 'doc_form': doc_form, 'comp_form': comp_form,
                'ns': ns, 'puede_flota': puede_flota(request.user), 'hoy': timezone.localdate()}
    if eq.pk:
        from .lecturas import uso_diario
        componentes = list(eq.componentes.filter(activo=True).select_related('padre', 'repuesto'))
        componentes.sort(key=lambda c: c.ruta)
        contexto.update(planes=eq.planes.filter(activo=True).select_related('componente'), ordenes=eq.ordenes.all()[:30],
                        documentos=eq.documentos.order_by('renovado', 'vencimiento'),
                        vehiculo=getattr(eq, 'vehiculo', None), componentes=componentes,
                        lecturas=eq.lecturas.select_related('registrado_por')[:12],
                        uso_diario=uso_diario(eq) if eq.medidor else None)
    return render(request, 'flota/equipo.html', contexto)


@login_required
def documentos(request):
    """Documentos de equipos y conductores por vencer o vencidos (y todos los vigentes)."""
    from logistica.models import Conductor
    dias = int(request.GET.get('dias') or srv.DIAS_AVISO)
    if request.GET.get('todos'):
        docs = list(srv.vigentes(DocumentoFlota.objects.all()).select_related('equipo', 'conductor'))
        licencias = []
    else:
        docs, licencias = srv.por_vencer(dias)
    form = DocumentoForm(request.POST if request.method == 'POST' else None,
                         request.FILES if request.method == 'POST' else None, prefix='doc', para_conductor=True)
    if request.method == 'POST' and puede_flota(request.user) and form.is_valid():
        doc = form.save(commit=False)
        doc.conductor = Conductor.objects.filter(pk=request.POST.get('conductor') or 0).first()
        try:
            srv.registrar_documento(doc, request.user, form.cleaned_data.get('archivo_subido'))
            messages.success(request, f'{doc} de {doc.conductor} registrado.')
            return redirect('flota:documentos')
        except srv.ErrorFlota as exc:
            messages.error(request, str(exc))
    return render(request, 'flota/documentos.html', {
        'docs': docs, 'licencias': licencias, 'dias': dias, 'form': form,
        'conductores': Conductor.objects.filter(activo=True), 'puede_flota': puede_flota(request.user)})


@login_required
def documento_archivo(request, pk):
    d = get_object_or_404(DocumentoFlota, pk=pk)
    if not d.archivo:
        raise Http404
    resp = HttpResponse(bytes(d.archivo), content_type=d.archivo_tipo or 'application/octet-stream')
    resp['Content-Disposition'] = f'inline; filename="{d.archivo_nombre or "documento"}"'
    return resp


# ---------------------------------------------------------------- lecturas (FLO-02)
@login_required
def parte(request):
    """Parte diario de odómetros y horómetros: una fila por equipo con medidor; todo el lote queda por aprobar."""
    from produccion.models import Equipo

    from .lecturas import ErrorLectura, registrar_parte, uso_diario
    fecha = _fecha(request.POST.get('fecha') or request.GET.get('fecha'))
    equipos = Equipo.objects.filter(activo=True).exclude(medidor='').exclude(estado='BAJA')
    if request.GET.get('tipo'):
        equipos = equipos.filter(tipo=request.GET['tipo'])
    equipos = list(equipos)
    if request.method == 'POST' and puede_flota(request.user):
        filas, errores = [], []
        for e in equipos:
            texto = (request.POST.get(f'valor_{e.pk}') or '').strip()
            if texto:
                try:
                    filas.append((e, leer_numero(texto)))
                except InvalidOperation:
                    errores.append(f'{e}: lectura inválida.')
        try:
            if errores:
                raise ErrorLectura(' '.join(errores))
            numero, lecturas = registrar_parte(filas, fecha, request.user)
            messages.success(request, f'Parte {numero}: {len(lecturas)} lectura(s) registradas, por aprobar.')
            return redirect('flota:lecturas')
        except ErrorLectura as exc:
            messages.error(request, str(exc))
    for e in equipos:
        e.valor_txt = request.POST.get(f'valor_{e.pk}', '')
        e.uso = uso_diario(e)
    return render(request, 'flota/parte.html', {'equipos': equipos, 'fecha': fecha, 'tipos': Equipo.TIPOS,
                                                'puede_flota': puede_flota(request.user)})


@login_required
def lecturas(request):
    """Lecturas por aprobar (y el historial): el supervisor aprueba o rechaza el parte."""
    from .lecturas import resolver
    puede_aprobar = puede(request.user, 'logistica.aprobar_lecturas')
    if request.method == 'POST':
        if not puede_aprobar:
            messages.error(request, 'Su usuario no aprueba lecturas.')
        else:
            ids = request.POST.getlist('lectura')
            accion = request.POST.get('accion')
            n = resolver(list(LecturaEquipo.objects.filter(pk__in=ids, estado='PENDIENTE').select_related('equipo')),
                         request.user, accion == 'aprobar', request.POST.get('motivo', ''))
            messages.success(request, f'Lecturas {"aprobadas" if accion == "aprobar" else "rechazadas"} '
                                      f'({n} equipo(s)).')
        return redirect('flota:lecturas')
    estado = request.GET.get('estado', 'PENDIENTE')
    qs = LecturaEquipo.objects.select_related('equipo', 'registrado_por', 'aprobado_por')
    if estado:
        qs = qs.filter(estado=estado)
    if request.GET.get('equipo'):
        qs = qs.filter(equipo_id=request.GET['equipo'])
    from django.core.paginator import Paginator
    return render(request, 'flota/lecturas.html', {
        'page_obj': Paginator(qs, 100).get_page(request.GET.get('page')), 'estado': estado,
        'estados': LecturaEquipo.ESTADOS, 'puede_aprobar': puede_aprobar})
