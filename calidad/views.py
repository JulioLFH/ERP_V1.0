"""Excelencia organizacional y calidad: tablero de objetivos e indicadores (BSC) con semáforo, no conformidades con
análisis de causa, auditorías con hallazgos, documentos controlados, sugerencias (Kaizen) y acciones correctivas y
preventivas (compartidas con SST)."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core import registros
from core.modulos import modulos_del_usuario
from core.permisos import puede
from core.registros import Tipo

from . import servicios as srv
from .models import (Accion, Auditoria, DocumentoControlado, Hallazgo, Indicador, Medicion, NoConformidad,
                     Sugerencia)

TIPOS = {
    'no-conformidades': Tipo('no-conformidades', NoConformidad, 'No conformidades', [
        ('Código', 'codigo'), ('Fecha', 'fecha'), ('Origen', 'get_origen_display'), ('Descripción', 'descripcion'),
        ('Área', 'area'), ('Estado', 'get_estado_display')],
        ['fecha', 'origen', 'norma', 'descripcion', 'area', 'proceso', 'detectado_por', 'metodo'],
        extra='calidad/_nc.html'),
    'auditorias': Tipo('auditorias', Auditoria, 'Auditorías', [
        ('Código', 'codigo'), ('Fecha', 'fecha'), ('Norma', 'get_norma_display'), ('Tipo', 'get_tipo_display'),
        ('Auditor líder', 'auditor_lider'), ('Hallazgos', 'hallazgos.count'), ('Estado', 'get_estado_display')],
        ['anio', 'norma', 'tipo', 'fecha', 'area', 'alcance', 'auditor_lider', 'equipo', 'estado'],
        extra='calidad/_hallazgos.html'),
    'documentos': Tipo('documentos', DocumentoControlado, 'Documentos controlados', [
        ('Código', 'codigo'), ('Título', 'titulo'), ('Tipo', 'get_tipo_display'), ('Versión', 'version'),
        ('Próxima revisión', 'proxima_revision'), ('Área', 'area')],
        ['codigo', 'titulo', 'tipo', 'norma', 'area', 'elaborado_por', 'proxima_revision', 'resumen']),
    'sugerencias': Tipo('sugerencias', Sugerencia, 'Sugerencias de mejora (Kaizen)', [
        ('N°', 'numero'), ('Fecha', 'fecha'), ('Título', 'titulo'), ('Propuesta por', 'autor'), ('Área', 'area'),
        ('Ahorro estimado', 'ahorro_estimado'), ('Estado', 'get_estado_display')],
        ['fecha', 'autor', 'area', 'titulo', 'descripcion', 'beneficio', 'ahorro_estimado', 'estado', 'responsable',
         'respuesta', 'fecha_implementacion']),
    'indicadores': Tipo('indicadores', Indicador, 'Objetivos e indicadores', [
        ('Indicador', 'nombre'), ('Objetivo', 'objetivo'), ('Perspectiva', 'get_perspectiva_display'),
        ('Área', 'area'), ('Meta', 'meta'), ('Fuente', 'get_fuente_display'), ('Activo', 'activo')],
        ['objetivo', 'nombre', 'perspectiva', 'area', 'formula', 'unidad', 'meta', 'sentido', 'tolerancia_pct',
         'frecuencia', 'fuente', 'responsable', 'activo'], extra='calidad/_mediciones.html'),
}


def _periodo(request):
    hoy = timezone.localdate()
    texto = (request.GET.get('mes') or '').replace('-', '')
    return texto if len(texto) == 6 and texto.isdigit() else hoy.strftime('%Y%m')


@login_required
def tablero(request):
    periodo = _periodo(request)
    return render(request, 'calidad/tablero.html', {
        'periodo': periodo, 'mes': f'{periodo[:4]}-{periodo[4:]}', 'perspectivas': srv.tablero(periodo),
        'nc_abiertas': NoConformidad.objects.filter(vigente=True).exclude(estado='CERRADA').count(),
        'vencidas': srv.acciones_vencidas(), 'tipos': TIPOS,
        'sugerencias': Sugerencia.objects.filter(estado__in=('RECIBIDA', 'EVALUACION')).count(),
        'revisiones': DocumentoControlado.objects.filter(vigente=True, proxima_revision__lte=timezone.localdate())})


@login_required
def registros_lista(request):
    return registros.lista(request, 'calidad', TIPOS, request.GET.get('tipo') or 'no-conformidades')


@login_required
def registro_nuevo(request):
    inicial = {'fecha': timezone.localdate()}
    if request.GET.get('tipo') == 'sugerencias':
        inicial['autor'] = request.user.get_full_name() or request.user.username
    return registros.nuevo(request, 'calidad', TIPOS, request.GET.get('tipo') or 'no-conformidades', inicial)


def _accion_extra(request, obj, accion):
    if accion == 'analisis' and isinstance(obj, NoConformidad):
        obj.porques = [request.POST.get(f'porque_{i}', '').strip()[:250] for i in range(1, 6)]
        obj.ishikawa = {k: request.POST.get(f'm_{k}', '').strip()[:500] for k, _ in NoConformidad.SEIS_M}
        obj.causa_raiz = request.POST.get('causa_raiz', '').strip()
        if obj.estado == 'ABIERTA' and obj.causa_raiz:
            obj.estado = 'TRATAMIENTO'
        obj.save(update_fields=['porques', 'ishikawa', 'causa_raiz', 'estado'])
        messages.success(request, 'Análisis de causa guardado.')
    elif accion == 'cerrar_nc' and isinstance(obj, NoConformidad):
        try:
            srv.cerrar_nc(obj, request.POST.get('eficaz') == '1')
            messages.success(request, 'No conformidad cerrada.')
        except srv.ErrorCalidad as exc:
            messages.error(request, str(exc))
    elif accion == 'hallazgo' and isinstance(obj, Auditoria):
        tipo = request.POST.get('tipo')
        if tipo not in dict(Hallazgo.TIPOS) or not request.POST.get('descripcion', '').strip():
            messages.error(request, 'Indique el tipo y la descripción del hallazgo.')
        else:
            Hallazgo.objects.create(auditoria=obj, tipo=tipo, requisito=request.POST.get('requisito', '')[:60],
                                    descripcion=request.POST['descripcion'].strip()[:400])
    elif accion == 'nc_hallazgo' and isinstance(obj, Auditoria):
        h = obj.hallazgos.filter(pk=request.POST.get('id'), no_conformidad=None).first()
        if h is not None:
            nc = NoConformidad.objects.create(
                fecha=obj.fecha, origen='AUDITORIA', norma=obj.norma, area=obj.area,
                descripcion=f'{h.get_tipo_display()} ({h.requisito}): {h.descripcion}' if h.requisito else
                f'{h.get_tipo_display()}: {h.descripcion}', detectado_por=obj.auditor_lider, creado_por=request.user)
            h.no_conformidad = nc
            h.save(update_fields=['no_conformidad'])
            messages.success(request, f'No conformidad {nc.codigo} creada desde el hallazgo.')
    elif accion == 'medicion' and isinstance(obj, Indicador):
        try:
            periodo = request.POST['periodo'].replace('-', '')[:6]
            valor = Decimal(request.POST['valor'].replace(',', ''))
            m, _ = Medicion.objects.update_or_create(indicador=obj, periodo=periodo, defaults={
                'valor': valor, 'nota': request.POST.get('nota', '')[:250], 'registrado_por': request.user})
            archivo = request.FILES.get('evidencia')
            if archivo:
                from core.sustentos import ErrorSustento, adjuntar
                try:
                    adjuntar(m, archivo, request.user, f'Evidencia {periodo[4:]}/{periodo[:4]}')
                except ErrorSustento as exc:
                    messages.warning(request, f'Medición guardada sin la evidencia: {exc}')
            messages.success(request, 'Medición registrada.')
        except (KeyError, InvalidOperation):
            messages.error(request, 'Indique el mes y el valor.')
    return redirect(request.path)


@login_required
def registro(request, tipo, pk):
    if tipo not in TIPOS:
        raise Http404
    obj = get_object_or_404(TIPOS[tipo].modelo, pk=pk)
    puede_registrar, puede_validar = puede(request.user, 'calidad.registrar'), puede(request.user, 'calidad.validar')
    accion = request.POST.get('accion') if request.method == 'POST' else None
    if accion and accion != 'datos':
        if not puede_registrar or (not getattr(obj, 'editable', True) and accion not in ('cerrar_nc', 'medicion',
                                                                                          'nc_hallazgo')):
            messages.error(request, 'El registro está validado o no tiene permiso para cambiarlo.')
            return redirect(request.path)
        return _accion_extra(request, obj, accion)
    contexto = {}
    if isinstance(obj, NoConformidad):
        contexto['porques'] = [(i, (obj.porques or [''] * 5)[i - 1] if len(obj.porques or []) >= i else '')
                               for i in range(1, 6)]
        contexto['seis_m'] = [(k, n, (obj.ishikawa or {}).get(k, '')) for k, n in NoConformidad.SEIS_M]
    if hasattr(obj, 'acciones'):
        contexto['acciones'] = obj.acciones.select_related('responsable')
        contexto['usuarios'] = get_user_model().objects.filter(is_active=True).order_by('username')
    if isinstance(obj, Auditoria):
        contexto['hallazgos'] = obj.hallazgos.select_related('no_conformidad')
        contexto['tipos_hallazgo'] = Hallazgo.TIPOS
    if isinstance(obj, Indicador):
        anio = timezone.localdate().year
        contexto['serie'] = srv.serie(obj, anio)
        contexto['mediciones'] = obj.mediciones.order_by('-periodo')[:24]
    return registros.ficha(request, 'calidad', TIPOS, tipo, pk, contexto, puede_registrar, puede_validar)


# ---------------------------------------------------------------- acciones correctivas (también de SST)
def _origen(ct_id, obj_id):
    ct = get_object_or_404(ContentType, pk=ct_id)
    modelo = ct.model_class()
    if modelo is None or not hasattr(modelo, 'acciones'):
        raise Http404
    return get_object_or_404(modelo, pk=obj_id)


def _puede_accion(request, origen):
    modulo = getattr(type(origen), 'MODULO', 'calidad')
    return modulo in modulos_del_usuario(request.user) and puede(request.user, f'{modulo}.registrar')


@login_required
def accion_nueva(request, ct_id, obj_id):
    origen = _origen(ct_id, obj_id)
    destino = request.POST.get('next') or origen.get_absolute_url()
    if request.method != 'POST' or not _puede_accion(request, origen):
        messages.error(request, 'No tiene permiso para registrar acciones aquí.')
        return redirect(destino)
    try:
        plazo = date.fromisoformat(request.POST.get('plazo', ''))
    except ValueError:
        plazo = None
    descripcion = (request.POST.get('descripcion') or '').strip()
    if not descripcion or plazo is None:
        messages.error(request, 'Indique la acción y su plazo.')
        return redirect(destino)
    responsable = get_user_model().objects.filter(pk=request.POST.get('responsable')).first() or request.user
    Accion.objects.create(origen=origen, tipo=request.POST.get('tipo') or 'CORRECTIVA', descripcion=descripcion[:300],
                          responsable=responsable, plazo=plazo)
    if isinstance(origen, NoConformidad) and origen.estado == 'ABIERTA':
        NoConformidad.objects.filter(pk=origen.pk).update(estado='TRATAMIENTO')
    messages.success(request, 'Acción registrada.')
    return redirect(destino)


@login_required
def accion(request, pk):
    a = get_object_or_404(Accion, pk=pk)
    destino = request.POST.get('next') or (a.origen.get_absolute_url() if a.origen is not None else 'calidad:acciones')
    if request.method != 'POST' or a.origen is None or not _puede_accion(request, a.origen):
        messages.error(request, 'No tiene permiso para actualizar esta acción.')
        return redirect(destino)
    estado = request.POST.get('estado')
    hoy = timezone.localdate()
    if estado in ('EN_CURSO', 'HECHA'):
        a.estado = estado
        if estado == 'HECHA':
            a.fecha_ejecucion = hoy
    elif estado in ('VERIFICADA', 'NO_EFICAZ'):
        if a.estado != 'HECHA' or not (request.POST.get('verificacion') or '').strip():
            messages.error(request, 'Primero se ejecuta la acción; luego se verifica su eficacia (indique cómo).')
            return redirect(destino)
        a.verificacion = request.POST['verificacion'].strip()[:250]
        a.eficaz = estado == 'VERIFICADA'
        a.verificado_por, a.verificado_en = request.user, hoy
        # una acción no eficaz se reabre: hay que replantearla
        a.estado = 'VERIFICADA' if a.eficaz else 'PENDIENTE'
    a.save()
    return redirect(destino)


@login_required
def acciones(request):
    qs = Accion.objects.select_related('responsable', 'origen_tipo')
    if request.GET.get('estado') == 'abiertas':
        qs = qs.exclude(estado='VERIFICADA')
    if request.GET.get('mias'):
        qs = qs.filter(responsable=request.user)
    filas = [a for a in qs[:500] if a.modulo in modulos_del_usuario(request.user)]
    return render(request, 'calidad/acciones.html', {'filas': filas, 'hoy': timezone.localdate()})
