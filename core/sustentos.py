"""Documentos de sustento adjuntos a los registros del ERP (solo se agregan, nunca se borran)."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect

from .models import ArchivoSustento, Sustento

MAX_BYTES = 5 * 1024 * 1024
EXTENSIONES = ('.pdf', '.png', '.jpg', '.jpeg', '.webp', '.xlsx', '.xls', '.xml', '.zip', '.docx', '.doc', '.csv')
# modelo -> módulo que puede ver y adjuntar sus sustentos
MODULO_DE = {'contabilidad.asiento': 'contabilidad', 'finanzas.movimiento': 'finanzas', 'finanzas.cuenta': 'finanzas',
             'inventario.operacion': 'inventario', 'ventas.venta': 'ventas', 'compras.compra': 'compras',
             'compras.ordencompra': 'compras', 'logistica.guiaremision': 'logistica',
             'produccion.ordenproduccion': 'manufactura', 'activos.activofijo': 'activos',
             'finanzas.gastorendicion': 'finanzas', 'finanzas.entregarendir': 'finanzas', 'finanzas.letra': 'finanzas',
             'finanzas.cheque': 'finanzas', 'finanzas.canjeletras': 'finanzas'}


class ErrorSustento(Exception):
    pass


def validar_archivo(archivo):
    if archivo is None:
        raise ErrorSustento('Adjunte el documento de sustento.')
    if archivo.size > MAX_BYTES:
        raise ErrorSustento('El sustento supera los 5 MB.')
    if not archivo.name.lower().endswith(EXTENSIONES):
        raise ErrorSustento('Formato no admitido: adjunte PDF, imagen, Excel, Word, XML o ZIP.')


def guardar_archivo(archivo, usuario=None, datos=None, nombre=None, tipo=''):
    """ArchivoSustento desde un archivo subido (o desde bytes ya leídos)."""
    if datos is None:
        validar_archivo(archivo)
        archivo.seek(0)
        datos, nombre, tipo = archivo.read(), archivo.name, getattr(archivo, 'content_type', '') or ''
    return ArchivoSustento.objects.create(nombre=(nombre or 'sustento')[:150], tipo=(tipo or '')[:100], datos=datos,
                                          tamano=len(datos), subido_por=usuario if usuario and
                                          usuario.is_authenticated else None)


def vincular(obj, archivo_sustento, usuario=None, descripcion=''):
    from .auditoria import registrar
    s = Sustento.objects.create(archivo=archivo_sustento, content_type=ContentType.objects.get_for_model(obj),
                                object_id=obj.pk, descripcion=descripcion[:200],
                                usuario=usuario if usuario and usuario.is_authenticated else None)
    registrar('ADJUNTAR', obj, {'archivo': archivo_sustento.nombre}, descripcion)
    return s


def adjuntar(obj, archivo, usuario=None, descripcion=''):
    return vincular(obj, guardar_archivo(archivo, usuario), usuario, descripcion)


def de(obj):
    if obj is None or not obj.pk:
        return Sustento.objects.none()
    return Sustento.objects.filter(content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk) \
        .select_related('archivo', 'usuario')


def tiene(obj):
    return de(obj).exists()


class SustentoField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('label', 'Documento de sustento')
        kwargs.setdefault('help_text', 'PDF, imagen, Excel, Word, XML o ZIP (máx. 5 MB)')
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        archivo = super().clean(data, initial)
        if archivo:
            try:
                validar_archivo(archivo)
            except ErrorSustento as exc:
                raise forms.ValidationError(str(exc))
        return archivo


def _puede(request, ct):
    from .modulos import modulos_del_usuario
    modulo = MODULO_DE.get(f'{ct.app_label}.{ct.model}')
    return modulo is not None and (request.user.is_superuser or modulo in modulos_del_usuario(request.user))


@login_required
def subir(request, ct_id, obj_id):
    ct = get_object_or_404(ContentType, pk=ct_id)
    obj = get_object_or_404(ct.model_class(), pk=obj_id)
    destino = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'home'
    if request.method == 'POST':
        if not _puede(request, ct):
            messages.error(request, 'No tiene acceso para adjuntar sustentos a este documento.')
        else:
            try:
                adjuntar(obj, request.FILES.get('archivo'), request.user, request.POST.get('descripcion', ''))
                messages.success(request, 'Sustento adjuntado.')
            except ErrorSustento as exc:
                messages.error(request, str(exc))
    return redirect(destino)


@login_required
def ver(request, pk):
    s = get_object_or_404(Sustento.objects.select_related('archivo', 'content_type'), pk=pk)
    if not _puede(request, s.content_type):
        raise Http404
    resp = HttpResponse(bytes(s.archivo.datos), content_type=s.archivo.tipo or 'application/octet-stream')
    resp['Content-Disposition'] = f'inline; filename="{s.archivo.nombre}"'
    return resp
