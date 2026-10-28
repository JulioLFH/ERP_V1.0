"""Reglas de Conversaciones, Calendario, Actividades pendientes y Proyectos."""
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, F, Max, Q
from django.urls import reverse
from django.utils import timezone

from core.permisos import puede

from .models import Canal, Evento, Mensaje, MiembroCanal, Pendiente, Proyecto, Tarea


class ErrorColaboracion(Exception):
    pass


def nombre(usuario):
    return (usuario.get_full_name() or usuario.username) if usuario else ''


def volver_seguro(request, destino):
    """La pantalla a la que se vuelve después de guardar, solo si es del propio sistema."""
    from django.utils.http import url_has_allowed_host_and_scheme
    if destino and destino.startswith('/') and url_has_allowed_host_and_scheme(
            destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return destino
    return None


def usuarios_internos():
    """Usuarios activos del ERP (sin los proveedores del portal ni el cliente del estudio contable)."""
    from core.modulos import GRUPOS
    return (get_user_model().objects.filter(is_active=True, acceso_proveedor__isnull=True)
            .exclude(Q(groups__name=GRUPOS['cliente']) & Q(is_superuser=False)).order_by('first_name', 'username'))


# ---------------------------------------------------------------- conversaciones
def canales_visibles(user):
    return (Canal.objects.filter(archivado=False)
            .filter(Q(tipo='CANAL', publico=True) | Q(membresias__usuario=user)).distinct())


def puede_ver(user, canal):
    if canal.archivado:
        return False
    return (canal.tipo == 'CANAL' and canal.publico) or canal.membresias.filter(usuario=user).exists()


def no_leidos(user):
    """{canal_id: mensajes nuevos} de los canales donde participa (sin contar los suyos)."""
    filas = (Mensaje.objects.filter(canal__archivado=False, canal__membresias__usuario=user,
                                    id__gt=F('canal__membresias__ultimo_leido'))
             .exclude(autor=user).values('canal_id').annotate(n=Count('id')))
    return {f['canal_id']: f['n'] for f in filas}


def marcar_leido(canal, user, hasta_id):
    MiembroCanal.objects.filter(canal=canal, usuario=user, ultimo_leido__lt=hasta_id).update(ultimo_leido=hasta_id)


def unirse(canal, user):
    MiembroCanal.objects.get_or_create(canal=canal, usuario=user)


def directo(user, otro):
    """Chat directo entre dos usuarios (uno solo por pareja)."""
    if otro.pk == user.pk:
        raise ErrorColaboracion('Elija a otro usuario.')
    a, b = sorted((user.pk, otro.pk))
    clave = f'd:{a}:{b}'
    canal = Canal.objects.filter(clave=clave).first()
    if canal is None:
        canal = Canal.objects.create(nombre=f'{nombre(user)} · {nombre(otro)}'[:80], tipo='DIRECTO', publico=False,
                                     clave=clave, creado_por=user)
        for u in (user, otro):
            unirse(canal, u)
    return canal


def titulo_canal(canal, user):
    """Nombre que ve el usuario: en un chat directo, el del otro."""
    if canal.tipo != 'DIRECTO':
        return f'# {canal.nombre}'
    otro = next((m.usuario for m in canal.membresias.select_related('usuario') if m.usuario_id != user.pk), None)
    return nombre(otro) or canal.nombre


def publicar(canal, user, texto):
    texto = (texto or '').strip()
    if not texto:
        raise ErrorColaboracion('Escriba el mensaje.')
    if len(texto) > 4000:
        raise ErrorColaboracion('El mensaje es muy largo (máximo 4000 caracteres).')
    if not puede_ver(user, canal):
        raise ErrorColaboracion('No participa en esta conversación.')
    unirse(canal, user)
    m = Mensaje.objects.create(canal=canal, autor=user, texto=texto)
    marcar_leido(canal, user, m.pk)
    return m


def mensaje_json(m, user):
    local = timezone.localtime(m.creado)
    return {'id': m.pk, 'autor': nombre(m.autor) or 'Usuario eliminado', 'mio': m.autor_id == user.pk,
            'texto': m.texto, 'hora': local.strftime('%d/%m %H:%M'), 'enlace': m.enlace}


def resumen(user, limite=8):
    """Chat de la barra superior (como en Odoo): mensajes sin leer y las últimas conversaciones del usuario, con su
    último mensaje."""
    nuevos = no_leidos(user)
    canales = (Canal.objects.filter(archivado=False, membresias__usuario=user)
               .annotate(ultimo=Max('mensajes__id')).filter(ultimo__isnull=False).order_by('-ultimo')
               .prefetch_related('membresias__usuario')[:limite])
    ultimos = {m.pk: m for m in Mensaje.objects.filter(pk__in=[c.ultimo for c in canales]).select_related('autor')}
    filas = []
    for c in canales:
        m = ultimos.get(c.ultimo)
        filas.append({'titulo': titulo_canal(c, user), 'directo': c.tipo == 'DIRECTO', 'nuevos': nuevos.get(c.pk, 0),
                      'texto': ('Usted: ' if m and m.autor_id == user.pk else '') + (m.texto[:90] if m else ''),
                      'hora': timezone.localtime(m.creado).strftime('%d/%m %H:%M') if m else '',
                      'url': f'{reverse("conversaciones:inicio")}?canal={c.pk}'})
    return {'total': sum(nuevos.values()), 'conversaciones': filas, 'url': reverse('conversaciones:inicio')}


# ---------------------------------------------------------------- proyectos
def ve_todos_proyectos(user):
    return user.is_superuser or puede(user, 'proyectos.ver_todos')


def proyectos_visibles(user):
    qs = Proyecto.objects.all()
    if ve_todos_proyectos(user):
        return qs
    return qs.filter(Q(responsable=user) | Q(miembros=user) | Q(creado_por=user) | Q(tareas__responsable=user)).distinct()


def puede_editar_proyecto(user, proyecto):
    return ve_todos_proyectos(user) or user.pk in (proyecto.responsable_id, proyecto.creado_por_id)


def puede_trabajar(user, proyecto):
    """Crea y mueve tareas: el jefe, los miembros y quien tiene una tarea del proyecto."""
    return (puede_editar_proyecto(user, proyecto) or proyecto.miembros.filter(pk=user.pk).exists()
            or proyecto.tareas.filter(responsable=user).exists())


def mover_tarea(tarea, etapa, user):
    if etapa not in dict(Tarea.ETAPAS):
        raise ErrorColaboracion('Etapa no válida.')
    if not (puede_trabajar(user, tarea.proyecto) or tarea.responsable_id == user.pk):
        raise ErrorColaboracion('No trabaja en este proyecto.')
    if tarea.proyecto.estado == 'CERRADO':
        raise ErrorColaboracion('El proyecto está cerrado.')
    tarea.etapa = etapa
    tarea.terminada_en = timezone.now() if etapa == 'HECHA' else None
    tarea.save(update_fields=['etapa', 'terminada_en'])


def avance(proyecto):
    """(% de tareas hechas, total de tareas)."""
    total = proyecto.tareas.count()
    hechas = proyecto.tareas.filter(etapa='HECHA').count()
    return (round(hechas * 100 / total) if total else 0), total


# ---------------------------------------------------------------- actividades pendientes
def _crm_de(user):
    from ventas.models import ActividadCRM
    return ActividadCRM.objects.filter(Q(responsable=user) | Q(responsable__isnull=True, usuario=user))


def por_hacer(user):
    """Todo lo que el usuario tiene pendiente: sus actividades, sus tareas de proyecto y sus actividades del CRM.
    [{origen, titulo, fecha, url, detalle, pk, prioridad_alta}] ordenado por fecha (sin fecha al final)."""
    filas = []
    for p in Pendiente.objects.filter(asignado_a=user, hecho=False).select_related('creado_por'):
        encargo = f'Encargada por {nombre(p.creado_por)}' if p.creado_por_id and p.creado_por_id != user.pk else ''
        filas.append({'origen': 'Actividad', 'icono': 'bi-check2-square', 'titulo': p.titulo, 'fecha': p.fecha,
                      'url': p.enlace, 'detalle': encargo, 'pk': p.pk, 'alta': p.prioridad == 'ALTA',
                      'pendiente': p})
    for t in Tarea.objects.filter(responsable=user).exclude(etapa='HECHA').exclude(proyecto__estado='CERRADO') \
            .select_related('proyecto'):
        filas.append({'origen': 'Tarea', 'icono': 'bi-list-check', 'titulo': t.titulo, 'fecha': t.fecha_limite,
                      'url': reverse('proyectos:tarea', args=[t.pk]),
                      'detalle': f'{t.proyecto} · {t.get_etapa_display()}', 'pk': t.pk, 'alta': t.prioridad != '0'})
    for a in _crm_de(user).filter(hecha=False).select_related('oportunidad', 'tercero'):
        url = reverse('crm:oportunidad', args=[a.oportunidad_id]) if a.oportunidad_id else reverse('crm:actividades')
        filas.append({'origen': 'CRM', 'icono': 'bi-kanban', 'titulo': f'{a.get_tipo_display()}: {a.descripcion}',
                      'fecha': a.fecha, 'url': url, 'detalle': a.cliente, 'pk': a.pk, 'alta': False})
    filas += _documentos_por_leer(user)
    return sorted(filas, key=lambda f: (f['fecha'] is None, f['fecha'] or timezone.localdate()))


def _documentos_por_leer(user):
    """CAL-09: documentos vigentes de lectura obligatoria del puesto del usuario que aún no leyó."""
    from calidad.models import DocumentoControlado, LecturaDocumento
    from planillas.organizacion import trabajador_de_usuario
    t = trabajador_de_usuario(user)
    if t is None or not t.puesto_id:
        return []
    leidos = set(LecturaDocumento.objects.filter(usuario=user).values_list('documento_id', flat=True))
    return [{'origen': 'Documento', 'icono': 'bi-file-earmark-text', 'titulo': f'Leer {d}', 'fecha': d.fecha_vigencia,
             'url': reverse('calidad:documento_flujo', args=[d.pk]), 'detalle': 'Lectura obligatoria de su puesto',
             'pk': d.pk, 'alta': False}
            for d in DocumentoControlado.objects.filter(vigente=True, estado_validacion='VALIDADO',
                                                        lectura_obligatoria=True, puestos=t.puesto)
            if d.pk not in leidos]


def agrupar(filas, hoy):
    """[(título, color, filas)]: vencidas, hoy, próximos 7 días y más adelante o sin fecha."""
    grupos = [('Vencidas', 'danger', []), ('Hoy', 'warning', []), ('Próximos 7 días', 'primary', []),
              ('Más adelante o sin fecha', 'secondary', [])]
    for f in filas:
        fecha = f['fecha']
        i = 3 if fecha is None or fecha > hoy + timedelta(days=7) else 0 if fecha < hoy else 1 if fecha == hoy else 2
        grupos[i][2].append(f)
    return grupos


def vencidas_y_hoy(user, hoy=None):
    hoy = hoy or timezone.localdate()
    return sum(1 for f in por_hacer(user) if f['fecha'] is not None and f['fecha'] <= hoy)


# ---------------------------------------------------------------- calendario
CAPAS = [('eventos', 'Eventos', '#e64980'), ('crm', 'Actividades del CRM', '#c2255c'),
         ('tareas', 'Tareas de proyectos', '#2b8a3e'), ('pendientes', 'Actividades pendientes', '#0ca678'),
         ('ausencias', 'Vacaciones y permisos', '#e67700'), ('sunat', 'Vencimientos SUNAT', '#c92a2a')]


def eventos_visibles(user):
    return Evento.objects.filter(Q(organizador=user) | Q(invitados=user) | Q(visibilidad='EMPRESA')).distinct()


def ve_ausencias_de_todos(user, mods):
    return user.is_superuser or 'tiempo_personal' in mods


def items(user, mods, desde, hasta, capas):
    """{fecha: [{hora, titulo, url, color, capa}]} del rango (ambas fechas incluidas)."""
    from planillas.models import Vacacion
    colores = {c: color for c, _, color in CAPAS}
    salida = {}

    def poner(fecha, **dato):
        if desde <= fecha <= hasta:
            salida.setdefault(fecha, []).append(dato)

    def dias(inicio, fin):
        d = max(inicio, desde)
        while d <= min(fin, hasta):
            yield d
            d += timedelta(days=1)

    if 'eventos' in capas:
        ini = timezone.make_aware(datetime.combine(desde, time.min))
        fin = timezone.make_aware(datetime.combine(hasta, time.max))
        for e in eventos_visibles(user).filter(inicio__lte=fin).filter(Q(fin__gte=ini) | Q(fin__isnull=True,
                                                                                            inicio__gte=ini)):
            local = timezone.localtime(e.inicio)
            ultimo = timezone.localtime(e.fin).date() if e.fin else local.date()
            for d in dias(local.date(), ultimo):
                poner(d, hora='' if e.todo_el_dia or d != local.date() else local.strftime('%H:%M'), titulo=e.titulo,
                      url=reverse('calendario:evento', args=[e.pk]), color=colores['eventos'], capa='eventos')
    if 'crm' in capas:
        for a in _crm_de(user).filter(fecha__range=(desde, hasta)):
            poner(a.fecha, hora=a.hora.strftime('%H:%M') if a.hora else '', titulo=f'{a.get_tipo_display()}: '
                  f'{a.descripcion}', url=reverse('crm:oportunidad', args=[a.oportunidad_id]) if a.oportunidad_id
                  else reverse('crm:actividades'), color=colores['crm'], capa='crm', hecho=a.hecha)
    if 'tareas' in capas:
        for t in Tarea.objects.filter(responsable=user, fecha_limite__range=(desde, hasta)).select_related('proyecto'):
            poner(t.fecha_limite, hora='', titulo=f'{t.titulo} ({t.proyecto})', url=reverse('proyectos:tarea',
                  args=[t.pk]), color=colores['tareas'], capa='tareas', hecho=t.etapa == 'HECHA')
    if 'pendientes' in capas:
        for p in Pendiente.objects.filter(asignado_a=user, fecha__range=(desde, hasta)):
            poner(p.fecha, hora='', titulo=p.titulo, url=reverse('actividades:lista'), color=colores['pendientes'],
                  capa='pendientes', hecho=p.hecho)
    if 'ausencias' in capas:
        qs = Vacacion.objects.filter(estado='APROBADA', fecha_inicio__lte=hasta, fecha_fin__gte=desde) \
            .select_related('trabajador')
        if not ve_ausencias_de_todos(user, mods):
            from planillas.organizacion import trabajador_de_usuario
            t = trabajador_de_usuario(user)
            qs = qs.filter(trabajador=t) if t else qs.none()
        for v in qs:
            for d in dias(v.fecha_inicio, v.fecha_fin):
                poner(d, hora='', titulo=f'{v.trabajador.nombre_completo}: {v.get_tipo_display().split(" (")[0]}',
                      url=reverse('planillas:vacaciones') if ve_ausencias_de_todos(user, mods) else reverse('mi_portal'),
                      color=colores['ausencias'], capa='ausencias')
    if 'sunat' in capas and (user.is_superuser or {'sunat', 'contabilidad'} & mods):
        from core.cronograma_sunat import vencimiento
        from core.models import Empresa
        ruc = Empresa.actual().ruc
        mes = desde.replace(day=1)
        while mes <= hasta:  # en cada mes vence el periodo anterior
            anterior = (mes - timedelta(days=1)).strftime('%Y%m')
            fecha = vencimiento(ruc, anterior)
            if fecha:
                poner(fecha, hora='', titulo=f'Vence IGV-Renta y PLE/SIRE de {anterior[4:]}/{anterior[:4]}',
                      url=reverse('sire_propuestas'), color=colores['sunat'], capa='sunat')
            mes = (mes + timedelta(days=32)).replace(day=1)
    for lista in salida.values():
        lista.sort(key=lambda x: (x['hora'] == '', x['hora']))
    return salida
