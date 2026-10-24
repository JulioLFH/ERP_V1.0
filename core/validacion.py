"""Validación por especialista de los registros de SST y de calidad (Ley 29783 y su reglamento, ISO 9001/14001/45001).

Cada tipo de registro indica en Reglas de validación si requiere validación y quién valida (jefe o supervisor de SST,
médico ocupacional, comité, auditor). Estados: Borrador → Por validar → Validado u Observado (vuelve a corregirse y a
enviarse). Para darlo por validado se adjunta el registro validado y firmado (PDF o imagen) y se registra quién validó
(nombre, cargo y N° de colegiatura o registro), la fecha y la versión. Un registro validado queda bloqueado: un cambio
es una versión nueva (la anterior queda como historia) y todo queda en la auditoría."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.forms.models import model_to_dict
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import ReglaValidacion

ESTADOS = [('BORRADOR', 'Borrador'), ('POR_VALIDAR', 'Por validar'), ('VALIDADO', 'Validado'),
           ('OBSERVADO', 'Observado')]


class ErrorValidacion(Exception):
    pass


class Validable(models.Model):
    """Campos de la validación por especialista (modelo abstracto). La subclase define TIPO_VALIDACION (clave de la
    regla), VALIDACION_DEFECTO (requiere por defecto) y ESPECIALISTA (quién valida por defecto)."""
    TIPO_VALIDACION = ''
    VALIDACION_DEFECTO = True
    ESPECIALISTA = ''
    MODULO = ''

    estado_validacion = models.CharField('Estado', max_length=11, choices=ESTADOS, default='BORRADOR', editable=False)
    version = models.PositiveSmallIntegerField('Versión', default=1, editable=False)
    version_anterior = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, editable=False,
                                         related_name='+')
    vigente = models.BooleanField(default=True, editable=False)
    validador_nombre = models.CharField('Validado por', max_length=120, blank=True, editable=False)
    validador_cargo = models.CharField('Cargo', max_length=120, blank=True, editable=False)
    validador_registro = models.CharField('N° de colegiatura o registro', max_length=40, blank=True, editable=False)
    validado_en = models.DateField('Validado el', null=True, blank=True, editable=False)
    observacion = models.CharField('Observación', max_length=250, blank=True, editable=False)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    @property
    def editable(self):
        return self.estado_validacion in ('BORRADOR', 'OBSERVADO') and self.vigente

    @property
    def regla(self):
        return regla(type(self))

    def copiar_relaciones(self, nuevo):
        """La subclase copia aquí sus líneas o relaciones a la versión nueva."""


def regla(modelo):
    """(requiere, especialista) del tipo de registro: la regla configurada o la de por defecto."""
    r = ReglaValidacion.objects.filter(tipo=modelo.TIPO_VALIDACION).first()
    if r is None:
        return modelo.VALIDACION_DEFECTO, modelo.ESPECIALISTA
    return r.requiere, r.especialista or modelo.ESPECIALISTA


def _registrar(obj, accion, cambios, motivo):
    from .auditoria import registrar
    registrar(accion, obj, cambios, motivo)


@transaction.atomic
def enviar(obj, usuario):
    """Borrador u observado: a validar (o validado directamente si su tipo no requiere especialista)."""
    if not obj.editable:
        raise ErrorValidacion('Solo se envía un registro en borrador u observado.')
    requiere, especialista = regla(type(obj))
    anterior = obj.estado_validacion
    if requiere:
        obj.estado_validacion = 'POR_VALIDAR'
    else:
        obj.estado_validacion, obj.validado_en = 'VALIDADO', timezone.localdate()
        obj.validador_nombre = (usuario.get_full_name() or usuario.username)[:120] if usuario else ''
    obj.save(update_fields=['estado_validacion', 'validado_en', 'validador_nombre'])
    _registrar(obj, 'MODIFICAR', {'estado': [anterior, obj.estado_validacion]},
               f'Enviado a validar ({especialista})' if requiere else 'Registrado (no requiere validación)')
    return obj


@transaction.atomic
def validar(obj, usuario, nombre, cargo, registro, archivo, fecha=None):
    """Por validar → validado, con el registro validado y firmado adjunto."""
    from .sustentos import ErrorSustento, adjuntar
    if obj.estado_validacion != 'POR_VALIDAR':
        raise ErrorValidacion('Solo se valida un registro enviado a validar.')
    if not (nombre or '').strip() or not (cargo or '').strip():
        raise ErrorValidacion('Indique el nombre y el cargo de quien valida.')
    if archivo is None:
        raise ErrorValidacion('Adjunte el registro validado y firmado por el especialista (PDF o imagen).')
    try:
        adjuntar(obj, archivo, usuario, f'Registro validado v{obj.version} por {nombre.strip()}')
    except ErrorSustento as exc:
        raise ErrorValidacion(str(exc))
    obj.estado_validacion = 'VALIDADO'
    obj.validador_nombre, obj.validador_cargo = nombre.strip()[:120], cargo.strip()[:120]
    obj.validador_registro, obj.validado_en = (registro or '').strip()[:40], fecha or timezone.localdate()
    obj.observacion = ''
    obj.save(update_fields=['estado_validacion', 'validador_nombre', 'validador_cargo', 'validador_registro',
                            'validado_en', 'observacion'])
    _registrar(obj, 'MODIFICAR', {'estado': ['POR_VALIDAR', 'VALIDADO']},
               f'Validado por {obj.validador_nombre} ({obj.validador_cargo})')
    return obj


@transaction.atomic
def observar(obj, usuario, motivo):
    if obj.estado_validacion != 'POR_VALIDAR':
        raise ErrorValidacion('Solo se observa un registro enviado a validar.')
    if not (motivo or '').strip():
        raise ErrorValidacion('Indique la observación.')
    obj.estado_validacion, obj.observacion = 'OBSERVADO', motivo.strip()[:250]
    obj.save(update_fields=['estado_validacion', 'observacion'])
    _registrar(obj, 'MODIFICAR', {'estado': ['POR_VALIDAR', 'OBSERVADO']}, f'Observado: {obj.observacion}')
    return obj


@transaction.atomic
def nueva_version(obj, usuario, motivo):
    """Registro validado: una copia en borrador con la versión siguiente (la anterior queda como historia)."""
    if obj.estado_validacion != 'VALIDADO' or not obj.vigente:
        raise ErrorValidacion('Solo un registro validado y vigente pasa a una versión nueva.')
    if not (motivo or '').strip():
        raise ErrorValidacion('Indique el motivo del cambio.')
    modelo = type(obj)
    datos = model_to_dict(obj, exclude=['id', 'estado_validacion', 'version', 'version_anterior', 'vigente',
                                        'validador_nombre', 'validador_cargo', 'validador_registro', 'validado_en',
                                        'observacion', 'creado_por', 'creado'])
    simples = {}
    for campo in modelo._meta.concrete_fields:
        if campo.name in datos and not campo.many_to_many:
            simples[campo.attname if campo.is_relation else campo.name] = datos[campo.name]
    for propio in ('codigo', 'numero'):  # la versión nueva conserva el código del registro
        if hasattr(obj, propio) and propio not in simples:
            simples[propio] = getattr(obj, propio)
    nuevo = modelo.objects.create(**simples, version=obj.version + 1, version_anterior=obj,
                                  creado_por=usuario if usuario and usuario.is_authenticated else None)
    for campo in modelo._meta.many_to_many:
        getattr(nuevo, campo.name).set(getattr(obj, campo.name).all())
    obj.copiar_relaciones(nuevo)
    modelo.objects.filter(pk=obj.pk).update(vigente=False)
    _registrar(nuevo, 'CREAR', {'version': [obj.version, nuevo.version]}, f'Nueva versión: {motivo.strip()}')
    return nuevo


def pendientes():
    """[(modelo, por validar, observados)] de los modelos validables instalados."""
    from django.apps import apps
    filas = []
    for modelo in apps.get_models():
        if issubclass(modelo, Validable):
            por_validar = modelo.objects.filter(estado_validacion='POR_VALIDAR', vigente=True).count()
            observados = modelo.objects.filter(estado_validacion='OBSERVADO', vigente=True).count()
            if por_validar or observados:
                filas.append((modelo, por_validar, observados))
    return filas


def _permiso(request, obj, accion):
    """Módulo del registro y permiso: validar u observar exige «validar»; enviar y versionar, «registrar»."""
    from .modulos import modulos_del_usuario
    from .permisos import puede
    modulo = getattr(type(obj), 'MODULO', '')
    if not modulo or modulo not in modulos_del_usuario(request.user):
        raise Http404
    return puede(request.user, f'{modulo}.{"validar" if accion in ("validar", "observar") else "registrar"}')


@login_required
def accion(request, ct_id, obj_id):
    ct = get_object_or_404(ContentType, pk=ct_id)
    modelo = ct.model_class()
    if modelo is None or not issubclass(modelo, Validable):
        raise Http404
    obj = get_object_or_404(modelo, pk=obj_id)
    destino = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'home'
    if request.method != 'POST':
        return redirect(destino)
    que = request.POST.get('accion')
    if not _permiso(request, obj, que):
        messages.error(request, 'No tiene el permiso para esta acción del registro.')
        return redirect(destino)
    try:
        if que == 'enviar':
            enviar(obj, request.user)
            messages.success(request, 'Registro enviado a validar.' if obj.estado_validacion == 'POR_VALIDAR' else
                             'Registro registrado (su tipo no requiere validación de especialista).')
        elif que == 'validar':
            validar(obj, request.user, request.POST.get('nombre'), request.POST.get('cargo'),
                    request.POST.get('registro'), request.FILES.get('archivo'))
            messages.success(request, 'Registro validado y bloqueado.')
        elif que == 'observar':
            observar(obj, request.user, request.POST.get('motivo'))
            messages.info(request, 'Registro observado: vuelve a quien lo registró para corregirlo.')
        elif que == 'version':
            nuevo = nueva_version(obj, request.user, request.POST.get('motivo'))
            messages.info(request, f'Versión {nuevo.version} creada en borrador.')
            if hasattr(nuevo, 'get_absolute_url'):
                return redirect(nuevo.get_absolute_url())
    except ErrorValidacion as exc:
        messages.error(request, str(exc))
    return redirect(destino)


@login_required
def reglas(request):
    """Qué tipos de registro requieren validación y quién valida."""
    from django.apps import apps

    from .permisos import puede
    tipos = sorted({(m.TIPO_VALIDACION, str(m._meta.verbose_name), m) for m in apps.get_models()
                    if issubclass(m, Validable) and m.TIPO_VALIDACION}, key=lambda t: t[1])
    if request.method == 'POST':
        if not (puede(request.user, 'sst.configurar') or puede(request.user, 'calidad.validar') or
                request.user.is_superuser):
            messages.error(request, 'No tiene permiso para cambiar las reglas de validación.')
            return redirect('validacion_reglas')
        for tipo, _, modelo in tipos:
            ReglaValidacion.objects.update_or_create(tipo=tipo, defaults={
                'requiere': bool(request.POST.get(f'requiere_{tipo}')),
                'especialista': (request.POST.get(f'especialista_{tipo}') or '')[:120]})
        messages.success(request, 'Reglas de validación guardadas.')
        return redirect('validacion_reglas')
    filas = [(tipo, nombre, *regla(modelo)) for tipo, nombre, modelo in tipos]
    return render(request, 'core/validacion_reglas.html', {'filas': filas})
