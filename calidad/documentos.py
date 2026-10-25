"""Flujo del documento controlado (v1.84, CAL-09): elaboró → revisó (firma) → aprobó (firma) con fecha de vigencia,
obsoleto al publicarse otra versión, distribución a puestos y constancia de lectura o capacitación de cada
trabajador."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.firmas import ErrorFirma, firmar, ip_de
from core.permisos import puede

from .models import DocumentoControlado, LecturaDocumento


class ErrorDocumento(Exception):
    pass


def trabajadores_obligados(doc):
    """Trabajadores activos de los puestos de la distribución."""
    from planillas.models import Trabajador
    if not doc.puestos.exists():
        return Trabajador.objects.none()
    return Trabajador.objects.filter(puesto__in=doc.puestos.all(), fecha_cese__isnull=True).select_related('usuario')


def revisar(doc, usuario, clave, ip=''):
    if doc.estado_validacion not in ('BORRADOR', 'OBSERVADO'):
        raise ErrorDocumento('Solo se revisa un documento en elaboración.')
    if doc.creado_por_id == usuario.pk:
        raise ErrorDocumento('Lo revisa otra persona (no quien lo elaboró).')
    firmar(usuario, clave, f'Revisado v{doc.version}', doc, ip)
    doc.revisado_por, doc.revisado_en = usuario, timezone.now()
    doc.save(update_fields=['revisado_por', 'revisado_en'])


def aprobar(doc, usuario, clave, vigencia=None, ip=''):
    """La aprobación con firma lo deja vigente (validado) y la versión anterior pasa a obsoleta."""
    if doc.estado_validacion in ('VALIDADO',):
        raise ErrorDocumento('El documento ya está aprobado.')
    if not doc.revisado_por_id:
        raise ErrorDocumento('Primero debe revisarlo otra persona (con su firma).')
    if usuario.pk in (doc.creado_por_id, doc.revisado_por_id):
        raise ErrorDocumento('Lo aprueba una persona distinta de quien lo elaboró y quien lo revisó.')
    if not (usuario.is_superuser or puede(usuario, 'calidad.validar')):
        raise ErrorDocumento('Aprueba los documentos quien valida en Excelencia organizacional (permiso «validar»).')
    firmar(usuario, clave, f'Aprobado v{doc.version}', doc, ip)
    doc.estado_validacion, doc.aprobado_por = 'VALIDADO', usuario
    doc.validador_nombre = (usuario.get_full_name() or usuario.username)[:120]
    doc.validador_cargo, doc.validado_en = 'Aprobación con firma electrónica', timezone.localdate()
    doc.fecha_vigencia = vigencia or timezone.localdate()
    doc.save()
    DocumentoControlado.objects.filter(codigo=doc.codigo).exclude(pk=doc.pk).update(vigente=False)


@login_required
def flujo(request, pk):
    doc = get_object_or_404(DocumentoControlado, pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'revisar':
                revisar(doc, request.user, request.POST.get('clave', ''), ip_de(request))
                messages.success(request, 'Documento revisado.')
            elif accion == 'aprobar':
                from datetime import date
                try:
                    vigencia = date.fromisoformat(request.POST.get('vigencia') or '')
                except ValueError:
                    vigencia = None
                aprobar(doc, request.user, request.POST.get('clave', ''), vigencia, ip_de(request))
                messages.success(request, f'{doc} aprobado y vigente.')
            elif accion == 'distribucion':
                if not puede(request.user, 'calidad.registrar'):
                    raise ErrorDocumento('Su usuario no registra en Excelencia organizacional.')
                doc.puestos.set(request.POST.getlist('puestos'))
                doc.lectura_obligatoria = request.POST.get('lectura_obligatoria') == '1'
                doc.save(update_fields=['lectura_obligatoria'])
                messages.success(request, 'Distribución guardada.')
            elif accion == 'leido':
                if not doc.vigente or doc.estado_validacion != 'VALIDADO':
                    raise ErrorDocumento('Solo se registra la lectura de la versión vigente aprobada.')
                from planillas.organizacion import trabajador_de_usuario
                LecturaDocumento.objects.get_or_create(
                    documento=doc, usuario=request.user,
                    defaults={'trabajador': trabajador_de_usuario(request.user),
                              'capacitacion': request.POST.get('capacitacion', '')[:200]})
                messages.success(request, 'Lectura registrada.')
        except (ErrorDocumento, ErrorFirma) as exc:
            messages.error(request, str(exc))
        return redirect('calidad:documento_flujo', pk)
    from planillas.models import Puesto
    obligados = list(trabajadores_obligados(doc))
    leyeron = {l.usuario_id for l in doc.lecturas.all()}
    pendientes = [t for t in obligados if not (t.usuario_id and t.usuario_id in leyeron)]
    from core.firmas import de
    return render(request, 'calidad/documento_flujo.html', {
        'd': doc, 'firmas': de(doc), 'lecturas': doc.lecturas.select_related('usuario', 'trabajador'),
        'pendientes': pendientes, 'puestos': Puesto.objects.all(), 'elegidos': set(doc.puestos.values_list('pk',
                                                                                                         flat=True)),
        'ya_lei': request.user.pk in leyeron, 'hoy': timezone.localdate()})


@login_required
def lecturas_pendientes(request):
    """Documentos vigentes de lectura obligatoria que el usuario (por su puesto) aún no leyó."""
    from planillas.organizacion import trabajador_de_usuario
    t = trabajador_de_usuario(request.user)
    docs = []
    if t is not None and t.puesto_id:
        leidos = set(LecturaDocumento.objects.filter(usuario=request.user).values_list('documento_id', flat=True))
        docs = [d for d in DocumentoControlado.objects.filter(vigente=True, estado_validacion='VALIDADO',
                                                              lectura_obligatoria=True, puestos=t.puesto)
                if d.pk not in leidos]
    return render(request, 'calidad/lecturas_pendientes.html', {'docs': docs, 'trabajador': t})
