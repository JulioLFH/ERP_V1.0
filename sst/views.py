"""Seguridad y salud en el trabajo: tablero, registros obligatorios (lista, nuevo y ficha con su validación),
líneas del IPERC, entrega de EPP con salida de almacén e índices de accidentabilidad."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core import registros
from core.permisos import puede
from core.registros import Tipo

from . import servicios as srv
from .models import (Capacitacion, EntregaEPP, EquipoEmergencia, Evento, ExamenMedico, HorasTrabajadas, Inspeccion,
                     Iperc, LineaEPP, LineaIperc, Monitoreo, PermisoTrabajo, Sctr)

LUGAR = ['obra', 'area', 'lugar']
TIPOS = {
    'emo': Tipo('emo', ExamenMedico, 'Exámenes médicos ocupacionales', [
        ('Trabajador', 'trabajador.nombre_completo'), ('Tipo', 'get_tipo_display'), ('Fecha', 'fecha'),
        ('Vence', 'vence'), ('Resultado', 'get_resultado_display'), ('Lugar', 'donde')],
        ['trabajador', 'tipo', 'fecha', 'vence', 'resultado', 'restricciones', 'clinica'] + LUGAR),
    'monitoreos': Tipo('monitoreos', Monitoreo, 'Monitoreo de agentes', [
        ('Fecha', 'fecha'), ('Agente', 'agente'), ('Tipo', 'get_tipo_agente_display'), ('Resultado', 'resultado'),
        ('Dentro del límite', 'cumple'), ('Próximo', 'proximo'), ('Lugar', 'donde')],
        ['fecha', 'tipo_agente', 'agente', 'resultado', 'limite', 'cumple', 'proximo', 'responsable'] + LUGAR),
    'inspecciones': Tipo('inspecciones', Inspeccion, 'Inspecciones de SST', [
        ('Fecha', 'fecha'), ('Tipo', 'get_tipo_display'), ('Inspector', 'inspector'), ('Lugar', 'donde'),
        ('Sin observaciones', 'conforme')],
        ['fecha', 'tipo', 'inspector', 'hallazgos', 'conforme'] + LUGAR, extra='calidad/_acciones.html'),
    'capacitaciones': Tipo('capacitaciones', Capacitacion, 'Inducción, capacitación y simulacros', [
        ('Fecha', 'fecha'), ('Tipo', 'get_tipo_display'), ('Tema', 'tema'), ('Horas', 'horas'),
        ('Asistentes', 'asistentes.count'), ('Lugar', 'donde')],
        ['tipo', 'tema', 'fecha', 'horas', 'expositor', 'asistentes', 'evaluacion'] + LUGAR),
    'equipos': Tipo('equipos', EquipoEmergencia, 'Equipos de seguridad y emergencia', [
        ('Tipo', 'get_tipo_display'), ('Código', 'codigo'), ('Lugar', 'donde'), ('Última inspección', 'fecha_inspeccion'),
        ('Vence', 'vence'), ('Estado', 'get_estado_display')],
        ['tipo', 'codigo', 'descripcion', 'fecha_inspeccion', 'vence', 'estado'] + LUGAR),
    'iperc': Tipo('iperc', Iperc, 'IPERC: identificación de peligros y evaluación de riesgos', [
        ('Fecha', 'fecha'), ('Proceso', 'proceso'), ('Puesto', 'puesto'), ('Lugar', 'donde'),
        ('Peligros', 'lineas.count')],
        ['proceso', 'puesto', 'fecha', 'elaborado_por'] + LUGAR, extra='sst/_iperc.html'),
    'epp': Tipo('epp', EntregaEPP, 'Entrega de EPP', [
        ('Fecha', 'fecha'), ('Trabajador', 'trabajador.nombre_completo'), ('Almacén', 'almacen'),
        ('Salida de almacén', 'operacion.numero'), ('Lugar', 'donde')],
        ['trabajador', 'fecha', 'almacen'] + LUGAR, extra='sst/_epp.html'),
    'sctr': Tipo('sctr', Sctr, 'SCTR (seguro complementario de trabajo de riesgo)', [
        ('Trabajador', 'trabajador.nombre_completo'), ('Obra', 'obra'), ('Tipo', 'get_tipo_display'),
        ('Aseguradora', 'aseguradora'), ('Póliza', 'poliza'), ('Desde', 'desde'), ('Hasta', 'hasta')],
        ['trabajador', 'obra', 'tipo', 'aseguradora', 'poliza', 'desde', 'hasta']),
    'permisos': Tipo('permisos', PermisoTrabajo, 'Permisos de trabajo de alto riesgo', [
        ('Fecha', 'fecha'), ('Tipo', 'get_tipo_display'), ('Trabajo', 'descripcion'), ('Responsable', 'responsable'),
        ('Lugar', 'donde'), ('Cerrado', 'cerrado')],
        ['tipo', 'fecha', 'hora_inicio', 'hora_fin', 'descripcion', 'responsable', 'trabajadores', 'controles',
         'cerrado'] + LUGAR),
    'eventos': Tipo('eventos', Evento, 'Accidentes, incidentes y enfermedades ocupacionales', [
        ('Fecha', 'fecha'), ('Tipo', 'get_tipo_display'), ('Trabajador', 'trabajador'), ('Lugar', 'donde'),
        ('Días de descanso', 'dias_descanso'), ('Notificar hasta', 'plazo_notificacion'),
        ('Notificado', 'notificado_en')],
        ['tipo', 'fecha', 'hora', 'trabajador', 'descripcion', 'parte_cuerpo', 'dias_descanso', 'causas_inmediatas',
         'causas_basicas', 'costo', 'centro_costo', 'notificado_en'] + LUGAR, extra='calidad/_acciones.html'),
}


@login_required
def tablero(request):
    from calidad.servicios import acciones_vencidas
    from core.validacion import pendientes
    hoy = timezone.localdate()
    filas, total = srv.indices(hoy.year)
    vence = srv.vencimientos(30)
    return render(request, 'sst/tablero.html', {
        'total': total, 'mes': filas[hoy.month - 1], 'vencimientos': vence,
        'avisos': sum(len(v) for k, v in vence.items() if k != 'Trabajadores sin examen médico'),
        'por_validar': [(m._meta.verbose_name_plural, getattr(m, 'SLUG', ''), pv, ob) for m, pv, ob in pendientes()
                        if getattr(m, 'MODULO', '') == 'sst'],
        'acciones': acciones_vencidas('sst'), 'tipos': TIPOS, 'hoy': hoy,
        'eventos': Evento.objects.filter(vigente=True).select_related('trabajador', 'obra')[:8]})


@login_required
def registros_lista(request):
    return registros.lista(request, 'sst', TIPOS, request.GET.get('tipo') or 'emo')


@login_required
def registro_nuevo(request):
    tipo = request.GET.get('tipo') or 'emo'
    return registros.nuevo(request, 'sst', TIPOS, tipo, inicial={'fecha': timezone.localdate()})


def _accion_extra(request, obj, accion):
    """Líneas del IPERC y de la entrega de EPP, salida de almacén y cierre del permiso."""
    if accion == 'linea_iperc' and isinstance(obj, Iperc):
        try:
            LineaIperc.objects.create(
                iperc=obj, tarea=request.POST['tarea'][:150], peligro=request.POST['peligro'][:150],
                riesgo=request.POST['riesgo'][:150], probabilidad=int(request.POST.get('probabilidad') or 2),
                severidad=int(request.POST.get('severidad') or 2), controles=request.POST.get('controles', '')[:300],
                probabilidad_residual=int(request.POST.get('probabilidad_residual') or 1),
                severidad_residual=int(request.POST.get('severidad_residual') or 2))
        except (KeyError, ValueError):
            messages.error(request, 'Complete la tarea, el peligro y el riesgo.')
    elif accion == 'borrar_iperc' and isinstance(obj, Iperc):
        obj.lineas.filter(pk=request.POST.get('id')).delete()
    elif accion == 'linea_epp' and isinstance(obj, EntregaEPP):
        from core.models import Producto
        producto = Producto.objects.filter(pk=request.POST.get('producto')).first()
        try:
            cantidad = Decimal(request.POST.get('cantidad') or '1')
            dias = int(request.POST.get('reposicion_dias') or 0)
        except (InvalidOperation, ValueError):
            producto = None
        if producto is None or obj.operacion_id:
            messages.error(request, 'Elija el EPP y la cantidad (una entrega que ya salió del almacén no se cambia).')
        else:
            LineaEPP.objects.create(entrega=obj, producto=producto, cantidad=cantidad, reposicion_dias=dias)
    elif accion == 'borrar_epp' and isinstance(obj, EntregaEPP) and not obj.operacion_id:
        obj.lineas.filter(pk=request.POST.get('id')).delete()
    elif accion == 'entregar_epp' and isinstance(obj, EntregaEPP):
        try:
            op = srv.entregar_epp(obj, request.user)
            messages.success(request, f'EPP entregados: salida de almacén {op.numero}. Adjunte el cargo firmado.')
        except srv.ErrorSST as exc:
            messages.error(request, str(exc))
    return redirect(request.path)


@login_required
def registro(request, tipo, pk):
    if tipo not in TIPOS:
        from django.http import Http404
        raise Http404
    obj = get_object_or_404(TIPOS[tipo].modelo, pk=pk)
    puede_registrar, puede_validar = puede(request.user, 'sst.registrar'), puede(request.user, 'sst.validar')
    accion = request.POST.get('accion') if request.method == 'POST' else None
    if accion and accion != 'datos':
        if not puede_registrar or not getattr(obj, 'editable', True) and accion != 'entregar_epp':
            messages.error(request, 'El registro está validado o no tiene permiso para cambiarlo.')
            return redirect(request.path)
        return _accion_extra(request, obj, accion)
    contexto = {}
    if isinstance(obj, Iperc):
        contexto['lineas'] = [(l, LineaIperc.clasificar(l.nivel), LineaIperc.clasificar(l.nivel_residual))
                              for l in obj.lineas.all()]
        contexto['niveles'] = LineaIperc.NIVELES
    if isinstance(obj, EntregaEPP):
        from core.models import Producto
        contexto['lineas'] = obj.lineas.select_related('producto')
        contexto['productos'] = Producto.objects.filter(activo=True, tipo='BIEN', es_plantilla=False).order_by(
            'nombre')[:500]
    if hasattr(obj, 'acciones'):
        from django.contrib.auth import get_user_model
        contexto['acciones'] = obj.acciones.select_related('responsable')
        contexto['usuarios'] = get_user_model().objects.filter(is_active=True).order_by('username')
    return registros.ficha(request, 'sst', TIPOS, tipo, pk, contexto, puede_registrar, puede_validar)


@login_required
def indices(request):
    from obras.models import Obra
    hoy = timezone.localdate()
    anio = int(request.GET['anio']) if (request.GET.get('anio') or '').isdigit() else hoy.year
    obra = Obra.objects.filter(pk=request.GET.get('obra')).first() if (request.GET.get('obra') or '').isdigit() \
        else None
    if request.method == 'POST':
        try:
            periodo = request.POST['periodo'].replace('-', '')[:6]
            horas = Decimal(request.POST['horas'])
            HorasTrabajadas.objects.update_or_create(periodo=periodo, obra=obra, defaults={'horas': horas})
            messages.success(request, f'Horas-hombre de {periodo[4:]}/{periodo[:4]} registradas.')
        except (KeyError, InvalidOperation):
            messages.error(request, 'Indique el mes y las horas-hombre.')
        return redirect(request.get_full_path())
    filas, total = srv.indices(anio, obra)
    return render(request, 'sst/indices.html', {
        'filas': filas, 'total': total, 'anio': anio, 'obra': obra, 'anios': range(hoy.year - 4, hoy.year + 1),
        'obras': Obra.objects.exclude(estado__in=('NO_ADJUDICADA',)).order_by('codigo')})
