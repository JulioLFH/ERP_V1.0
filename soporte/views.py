"""Soporte y tickets: cualquier usuario de cualquier empresa crea tickets (desarrollo, problema, consulta u otro) y
conversa con soporte. Todo va a la base central de tickets (ver models.py).

Quién ve qué (siempre dentro de esta instalación):
  · cada usuario, sus propios tickets en la empresa activa;
  · el administrador de la empresa, todos los de su empresa;
  · el usuario corporativo (Panel del grupo), además, los de todas las empresas del grupo.
"""
import logging
import threading

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import Adjunto, Comentario, Ticket

log = logging.getLogger(__name__)
MAX_ARCHIVOS = 5


# ---------------------------------------------------------------- de dónde viene el ticket
def instalacion():
    """Nombre de esta instalación de Ceiba ERP en la central (INSTALACION; si no, el nombre de su base principal)."""
    nombre = getattr(settings, 'TICKETS_INSTALACION', '') or str(settings.DATABASES['default'].get('NAME', 'ceiba'))
    return nombre.replace('\\', '/').rsplit('/', 1)[-1][:60]


def _origen(request):
    from core.models import Empresa
    from erp import empresas as reg
    alias = reg.actual()
    empresa = Empresa.actual()
    token = reg.activar('default')
    try:
        grupo = Empresa.actual()
    finally:
        reg.restaurar(token)
    u = request.user
    return {'instalacion': instalacion(), 'dominio': request.get_host()[:120],
            'version_erp': getattr(settings, 'ERP_VERSION', '')[:20],
            'grupo_ruc': (grupo.ruc or '')[:11], 'grupo_nombre': (grupo.razon_social or '')[:200],
            'empresa_alias': alias[:30], 'empresa_ruc': (empresa.ruc or '')[:11],
            'empresa_nombre': (empresa.razon_social or '')[:200],
            'usuario': u.get_username()[:150], 'usuario_nombre': (u.get_full_name() or '')[:150],
            'usuario_correo': (u.email or '')[:254]}


def _alcance(request):
    """'grupo' (corporativo), 'empresa' (administrador) o 'propios'."""
    from core.grupo import corporativo
    if corporativo(request) is not None:
        return 'grupo'
    return 'empresa' if request.user.is_superuser else 'propios'


def _visibles(request, ver_grupo=False):
    from erp import empresas as reg
    qs = Ticket.objects.filter(instalacion=instalacion())
    alcance = _alcance(request)
    if alcance == 'grupo' and ver_grupo:
        return qs
    qs = qs.filter(empresa_alias=reg.actual())
    if alcance == 'propios':
        qs = qs.filter(usuario=request.user.get_username())
    return qs


def _ticket(request, pk):
    t = _visibles(request, ver_grupo=True).filter(pk=pk).first()
    if t is None:
        raise Http404('Ticket no encontrado')
    return t


# ---------------------------------------------------------------- formularios y archivos
class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['tipo', 'prioridad', 'modulo', 'titulo', 'descripcion']
        widgets = {'descripcion': forms.Textarea(attrs={
            'rows': 7, 'placeholder': 'Qué pasó o qué necesita, paso a paso. Si es un error: qué hizo, qué esperaba '
                                      'y qué salió. Si es un desarrollo: para qué lo necesita y cómo lo imagina.'}),
                   'titulo': forms.TextInput(attrs={'placeholder': 'Ej.: No puedo emitir la factura F001-120'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.modulos import MODULOS
        self.fields['modulo'] = forms.ChoiceField(
            label='Módulo', required=False,
            choices=[('', 'General / no sé')] + [(m['clave'], m['nombre']) for m in MODULOS])
        for f in self.fields.values():
            f.widget.attrs.setdefault('class', 'form-select' if isinstance(f.widget, forms.Select) else 'form-control')

    def clean_descripcion(self):
        d = self.cleaned_data['descripcion'].strip()
        if len(d) < 15:
            raise forms.ValidationError('Cuente con un poco más de detalle (al menos 15 caracteres).')
        return d[:20000]


def _archivos(request):
    """Valida los adjuntos del formulario; devuelve (lista, error)."""
    archivos = request.FILES.getlist('adjuntos')
    if len(archivos) > MAX_ARCHIVOS:
        return [], f'Puede adjuntar hasta {MAX_ARCHIVOS} archivos a la vez.'
    for a in archivos:
        ext = a.name.rsplit('.', 1)[-1].lower() if '.' in a.name else ''
        if ext not in Adjunto.EXTENSIONES:
            return [], f'«{a.name}»: tipo de archivo no permitido (imágenes, PDF, Excel, Word, texto o ZIP).'
        if a.size > Adjunto.MAXIMO:
            return [], f'«{a.name}» pesa más de 5 MB.'
    return archivos, ''


def _guardar_archivos(ticket, archivos, usuario, comentario=None):
    for a in archivos:
        Adjunto.objects.create(ticket=ticket, comentario=comentario, nombre=a.name[:200],
                               tipo=(a.content_type or '')[:100], tamano=a.size, contenido=a.read(),
                               subido_por=usuario[:150])


def _avisar(ticket, que, texto):
    """Correo al administrador de Ceiba (TICKETS_AVISO). En otro hilo y sin causar errores."""
    destinos = getattr(settings, 'TICKETS_AVISO', [])
    if not destinos:
        return

    def enviar():
        try:
            from core.correo import enviar_simple
            enviar_simple(destinos, f'Ceiba ERP · {ticket.codigo} · {que}: {ticket.titulo}',
                          f'{que} · {ticket.get_tipo_display()} · prioridad {ticket.get_prioridad_display()}\n'
                          f'Instalación: {ticket.instalacion} · Empresa: {ticket.empresa_nombre} ({ticket.empresa_ruc})\n'
                          f'Usuario: {ticket.usuario_nombre or ticket.usuario} {ticket.usuario_correo}\n\n{texto}')
        except Exception:
            log.warning('No se pudo avisar el ticket %s', ticket.codigo, exc_info=True)

    if getattr(settings, 'MONITOREO_SINCRONO', False):
        enviar()
    else:
        import contextvars
        threading.Thread(target=contextvars.copy_context().run, args=(enviar,), daemon=True).start()


def _sin_central(request, destino='home'):
    messages.error(request, 'No se pudo conectar con la central de tickets. Intente de nuevo en unos minutos; si '
                            'persiste, avise al administrador de Ceiba ERP.')
    log.exception('Central de tickets no disponible')
    return redirect(destino)


# ---------------------------------------------------------------- pantallas
@login_required
def lista(request):
    alcance = _alcance(request)
    ver_grupo = alcance == 'grupo' and request.GET.get('ver') == 'grupo'
    try:
        base = _visibles(request, ver_grupo)
        qs = base
        estado = request.GET.get('estado', 'abiertos')
        if estado == 'abiertos':
            qs = qs.filter(estado__in=Ticket.ABIERTOS)
        elif estado:
            qs = qs.filter(estado=estado)
        if request.GET.get('tipo'):
            qs = qs.filter(tipo=request.GET['tipo'])
        q = request.GET.get('q', '').strip()
        if q:
            filtro = Q(titulo__icontains=q) | Q(descripcion__icontains=q)
            if q.upper().startswith('TK-') and q[3:].isdigit():
                filtro |= Q(pk=int(q[3:]))
            qs = qs.filter(filtro)
        resumen = {e: base.filter(estado=e).count() for e, _ in Ticket.ESTADOS}
        pagina = Paginator(qs, 25).get_page(request.GET.get('page'))
        list(pagina)
    except DatabaseError:
        return _sin_central(request)
    return render(request, 'soporte/lista.html', {
        'page_obj': pagina, 'estados': Ticket.ESTADOS, 'tipos': Ticket.TIPOS, 'estado': estado, 'resumen': resumen,
        'alcance': alcance, 'ver_grupo': ver_grupo, 'abiertos': sum(resumen[e] for e in Ticket.ABIERTOS)})


@login_required
def nuevo(request):
    desde = request.GET.get('desde') or request.POST.get('url', '')
    if not url_has_allowed_host_and_scheme(desde, allowed_hosts={request.get_host()}):
        desde = ''
    inicial = {'tipo': request.GET.get('tipo', 'PROBLEMA'), 'modulo': request.session.get('modulo', '')}
    form = TicketForm(request.POST or None, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        archivos, error = _archivos(request)
        if error:
            messages.error(request, error)
        else:
            try:
                t = form.save(commit=False)
                for k, v in _origen(request).items():
                    setattr(t, k, v)
                t.url, t.navegador = desde[:500], request.META.get('HTTP_USER_AGENT', '')[:300]
                t.save()
                _guardar_archivos(t, archivos, t.usuario)
            except DatabaseError:
                return _sin_central(request, 'soporte:lista')
            _avisar(t, 'Ticket nuevo', t.descripcion)
            messages.success(request, f'Ticket {t.codigo} creado. Soporte lo revisará y le responderá aquí.')
            return redirect('soporte:detalle', pk=t.pk)
    return render(request, 'soporte/nuevo.html', {'form': form, 'desde': desde})


@login_required
def detalle(request, pk):
    try:
        t = _ticket(request, pk)
        if request.method == 'POST':
            accion = request.POST.get('accion', 'comentar')
            usuario = request.user.get_username()
            if accion == 'comentar':
                texto = request.POST.get('texto', '').strip()
                archivos, error = _archivos(request)
                if error or not (texto or archivos):
                    messages.error(request, error or 'Escriba un mensaje o adjunte un archivo.')
                else:
                    c = Comentario.objects.create(ticket=t, autor=usuario,
                                                  autor_nombre=request.user.get_full_name()[:150],
                                                  texto=texto[:20000] or '(adjuntó archivos)')
                    _guardar_archivos(t, archivos, usuario, c)
                    if t.estado in ('ESPERA', 'RESUELTO'):  # respondió el cliente: vuelve a revisión de soporte
                        t.estado = 'REVISION'
                    t.save()
                    _avisar(t, 'Respuesta del cliente', texto)
                    messages.success(request, 'Mensaje enviado.')
            elif accion == 'cerrar' and t.estado != 'CERRADO':
                t.estado, t.cerrado_en = 'CERRADO', timezone.now()
                t.save()
                Comentario.objects.create(ticket=t, autor=usuario, autor_nombre=request.user.get_full_name()[:150],
                                          texto='Cerró el ticket.')
                messages.success(request, f'Ticket {t.codigo} cerrado.')
            elif accion == 'reabrir' and t.estado in ('RESUELTO', 'CERRADO'):
                t.estado, t.cerrado_en = 'REVISION', None
                t.save()
                Comentario.objects.create(ticket=t, autor=usuario, autor_nombre=request.user.get_full_name()[:150],
                                          texto='Reabrió el ticket.')
                _avisar(t, 'Ticket reabierto', t.titulo)
                messages.success(request, f'Ticket {t.codigo} reabierto.')
            return redirect('soporte:detalle', pk=t.pk)
        comentarios = list(t.comentarios.prefetch_related('adjuntos'))
        adjuntos = [a for a in t.adjuntos.defer('contenido') if a.comentario_id is None]
    except DatabaseError:
        return _sin_central(request, 'soporte:lista')
    from core.modulos import POR_CLAVE
    return render(request, 'soporte/detalle.html', {
        't': t, 'comentarios': comentarios, 'adjuntos': adjuntos, 'ajeno': t.usuario != request.user.get_username(),
        'modulo_nombre': POR_CLAVE.get(t.modulo, {}).get('nombre', t.modulo) or 'General'})


@login_required
def adjunto(request, pk):
    try:
        a = Adjunto.objects.filter(pk=pk).select_related('ticket').first()
        if a is None or not _visibles(request, ver_grupo=True).filter(pk=a.ticket_id).exists():
            raise Http404
        datos = bytes(a.contenido)
    except DatabaseError:
        return _sin_central(request, 'soporte:lista')
    r = HttpResponse(datos, content_type='application/octet-stream')
    r['Content-Disposition'] = f'attachment; filename="{a.nombre.replace(chr(34), "")}"'
    r['X-Content-Type-Options'] = 'nosniff'
    return r


@login_required
def reportar(request):
    """«Reportar un problema» del menú del usuario: crea un ticket de tipo Problema con la página donde estaba. Si la
    central de tickets no responde, queda como incidencia local (Ajustes › Incidencias) para no perder el reporte."""
    volver = request.POST.get('url', '')
    if not url_has_allowed_host_and_scheme(volver, allowed_hosts={request.get_host()}):
        volver = '/'
    if request.method != 'POST':
        return redirect(volver)
    texto = request.POST.get('descripcion', '').strip()
    if len(texto) < 10:
        messages.error(request, 'Cuente con un poco más de detalle qué pasó (al menos 10 caracteres).')
        return redirect(volver)
    try:
        t = Ticket(tipo='PROBLEMA', prioridad='MEDIA', titulo=texto.splitlines()[0][:150], descripcion=texto[:20000],
                   modulo=request.session.get('modulo', '')[:40], url=volver[:500],
                   navegador=request.META.get('HTTP_USER_AGENT', '')[:300], **_origen(request))
        t.save()
    except DatabaseError:
        log.exception('Central de tickets no disponible: el reporte queda como incidencia local')
        from core.monitoreo import reportar as reportar_local
        return reportar_local(request)
    _avisar(t, 'Problema reportado', texto)
    messages.success(request, f'Gracias: su reporte quedó registrado como ticket {t.codigo}. Puede seguirlo en '
                              f'su menú › Soporte y tickets.')
    return redirect(volver)
