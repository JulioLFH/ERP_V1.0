"""SIRE: propuestas de los registros de ventas (RVIE) y compras (RCE) traídas de SUNAT por la API o subidas a mano;
cruce con lo registrado, creación de lo que falta y aceptación de la propuesta en SUNAT."""
import io

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from compras import sire

from . import sire_api
from .forms import BootstrapMixin
from .models import PropuestaSIRE
from .modulos import modulos_del_usuario
from .permisos import puede
from .utils import excel_response

# quién ve cada libro y qué permiso crea sus comprobantes
MODULOS_LIBRO = {'RVIE': {'ventas', 'contabilidad'}, 'RCE': {'compras', 'contabilidad'}}
PERMISO_CREAR = {'RVIE': 'ventas.emitir', 'RCE': 'compras.registrar'}
PERMISO_ACEPTAR = 'contabilidad.periodos'


def _libros(user):
    mods = modulos_del_usuario(user)
    return [(k, n) for k, n in PropuestaSIRE.LIBROS if MODULOS_LIBRO[k] & mods]


def _periodo_anterior():
    hoy = timezone.localdate()
    return f'{hoy.year - (hoy.month == 1)}-{(hoy.month - 2) % 12 + 1:02d}'


class PropuestaForm(BootstrapMixin, forms.Form):
    libro = forms.ChoiceField(label='Registro', choices=PropuestaSIRE.LIBROS)
    periodo = forms.CharField(label='Periodo', widget=forms.TextInput(attrs={'type': 'month'}))
    clave_sol = forms.CharField(label='Clave SOL', required=False, widget=forms.PasswordInput,
                                help_text='Solo para traer de SUNAT; no se guarda')
    archivo = forms.FileField(label='O suba el archivo de la propuesta', required=False,
                              help_text='TXT, ZIP, CSV o Excel descargado del SIRE')

    def __init__(self, *args, libros=None, **kwargs):
        super().__init__(*args, **kwargs)
        if libros is not None:
            self.fields['libro'].choices = libros

    def clean_periodo(self):
        valor = self.cleaned_data['periodo'].replace('-', '')
        if len(valor) != 6 or not valor.isdigit():
            raise forms.ValidationError('Periodo no válido.')
        return valor


def _archivo(contenido, nombre):
    archivo = io.BytesIO(contenido)
    archivo.name = nombre
    return archivo


def _leer(contenido, nombre):
    return sire.leer(_archivo(contenido, nombre))


def _actualizar(p):
    """Consulta el ticket; si SUNAT terminó, descarga el archivo y lee los comprobantes."""
    estado = sire_api.estado_ticket(p.libro, p.periodo, p.ticket)
    if estado['error']:
        p.estado, p.mensaje = 'ERROR', f'SUNAT: {estado["descripcion"]}'
    elif estado['terminado']:
        nombre, contenido = sire_api.descargar_archivo(p.libro, estado['registro'])
        p.archivo, p.nombre_archivo = contenido, nombre[:200]
        try:
            registros = _leer(contenido, nombre)
        except sire.ErrorSIRE as exc:
            p.estado, p.mensaje = 'ERROR', str(exc)
        else:
            p.registros, p.estado = sire.a_json(registros), 'LISTA'
            p.mensaje = f'{len(registros)} comprobantes en la propuesta.'
    else:
        p.mensaje = f'SUNAT está preparando el archivo ({estado["descripcion"]}).'
    p.save()


@login_required
def lista(request):
    libros = _libros(request.user)
    form = PropuestaForm(request.POST or None, request.FILES or None, libros=libros,
                         initial={'periodo': _periodo_anterior(), 'libro': request.GET.get('libro') or 'RCE'})
    if request.method == 'POST' and form.is_valid():
        libro, periodo = form.cleaned_data['libro'], form.cleaned_data['periodo']
        archivo = form.cleaned_data.get('archivo')
        if archivo:
            try:
                registros = sire.leer(archivo)
            except sire.ErrorSIRE as exc:
                messages.error(request, str(exc))
                return redirect(request.get_full_path())
            archivo.seek(0)
            p = PropuestaSIRE.objects.create(
                libro=libro, periodo=periodo, origen='ARCHIVO', estado='LISTA', archivo=archivo.read(),
                nombre_archivo=archivo.name[:200], registros=sire.a_json(registros), usuario=request.user,
                mensaje=f'{len(registros)} comprobantes en la propuesta.')
            return redirect('sire_propuesta', p.pk)
        if not sire_api.configurada():
            messages.error(request, 'Suba el archivo de la propuesta, o registre las credenciales del SIRE en Ajustes › '
                                    'Empresa para traerla de SUNAT.')
            return redirect(request.get_full_path())
        try:
            if form.cleaned_data.get('clave_sol'):
                sire_api.iniciar_sesion(form.cleaned_data['clave_sol'])
            ticket = sire_api.solicitar_propuesta(libro, periodo)
        except sire_api.ErrorSIRE as exc:
            messages.error(request, str(exc))
            return redirect(request.get_full_path())
        p = PropuestaSIRE.objects.create(libro=libro, periodo=periodo, ticket=ticket, usuario=request.user,
                                         mensaje='Solicitada a SUNAT.')
        try:
            _actualizar(p)
        except sire_api.ErrorSIRE as exc:
            messages.warning(request, f'Ticket {ticket} creado; aún no se pudo consultar: {exc}')
        return redirect('sire_propuesta', p.pk)
    claves = [k for k, _ in libros]
    return render(request, 'core/sire_lista.html', {
        'form': form, 'propuestas': PropuestaSIRE.objects.filter(libro__in=claves).select_related('usuario')[:60],
        'configurada': sire_api.configurada(), 'sesion': sire_api.hay_token()})


def _excel(p, r):
    """El cruce con todas las columnas que entrega SUNAT en la propuesta (se vuelven a leer del archivo guardado)."""
    encabezados, registros = [], []
    if p.archivo:
        try:
            encabezados, registros = sire.leer_completo(_archivo(bytes(p.archivo), p.nombre_archivo or 'propuesta.zip'))
        except sire.ErrorSIRE:
            pass
    encabezados, filas = sire.filas_cruce_completo(encabezados, registros, r)
    return excel_response(f'Cruce_SIRE_{p.libro}_{p.periodo}', f'CRUCE PROPUESTA SIRE {p.libro} {p.periodo}',
                          encabezados, filas)


@login_required
def detalle(request, pk):
    p = get_object_or_404(PropuestaSIRE, pk=pk)
    if p.libro not in dict(_libros(request.user)):
        messages.error(request, 'Su usuario no tiene el módulo de este registro.')
        return redirect('sire_propuestas')
    accion = request.POST.get('accion') if request.method == 'POST' else None
    if request.GET.get('descargar') and p.archivo:
        from django.http import HttpResponse
        resp = HttpResponse(bytes(p.archivo), content_type='application/octet-stream')
        resp['Content-Disposition'] = f'attachment; filename="{p.nombre_archivo or "propuesta.zip"}"'
        return resp
    try:
        if accion in ('consultar', 'aceptar') and request.POST.get('clave_sol'):
            sire_api.iniciar_sesion(request.POST['clave_sol'])
        if accion == 'consultar' and p.estado == 'SOLICITADA':
            _actualizar(p)
            (messages.success if p.estado == 'LISTA' else messages.info)(request, p.mensaje)
            return redirect('sire_propuesta', pk)
        if accion == 'aceptar':
            if not puede(request.user, PERMISO_ACEPTAR):
                messages.error(request, 'Aceptar la propuesta en SUNAT exige el permiso de cerrar periodos '
                                        'contables.')
            elif p.estado != 'LISTA' or p.aceptada_en:
                messages.error(request, 'La propuesta no está lista o ya fue aceptada.')
            else:
                p.ticket_aceptacion = sire_api.aceptar_propuesta(p.libro, p.periodo)
                p.aceptada_en = timezone.now()
                p.save(update_fields=['ticket_aceptacion', 'aceptada_en', 'actualizado'])
                from .auditoria import registrar
                registrar('MODIFICAR', p, {'Propuesta SIRE': ['Pendiente', f'Aceptada en SUNAT (ticket '
                                                                           f'{p.ticket_aceptacion or "sin ticket"})']})
                messages.success(request, f'Propuesta {p} aceptada en SUNAT. Complete la generación del registro en '
                                          f'el portal SIRE si SUNAT lo pide.')
            return redirect('sire_propuesta', pk)
    except sire_api.ErrorSIRE as exc:
        messages.error(request, str(exc))
        return redirect('sire_propuesta', pk)
    resultado = None
    if p.estado == 'LISTA':
        registros = sire.de_json(p.registros)
        if accion == 'crear':
            if not puede(request.user, PERMISO_CREAR[p.libro]):
                messages.error(request, 'Su usuario no registra comprobantes de este tipo.')
            else:
                creados, errores = sire.crear_faltantes(p.libro, registros, p.periodo,
                                                        request.POST.getlist('falta'), request.user)
                p.creados += creados
                p.save(update_fields=['creados', 'actualizado'])
                if creados:
                    messages.success(request, f'{creados} comprobante(s) creados desde la propuesta.')
                for e in errores[:10]:
                    messages.warning(request, e)
            return redirect('sire_propuesta', pk)
        resultado = sire.cruzar(p.libro, registros, p.periodo)
        if request.GET.get('formato') == 'excel':
            return _excel(p, resultado)
        for x in resultado['solo_sunat']:
            x['clave'] = f'{x["tipo"]}|{x["serie"]}|{x["numero"]}|{x["ruc"]}'
    return render(request, 'core/sire_propuesta.html', {
        'p': p, 'r': resultado, 'sesion': sire_api.hay_token(), 'configurada': sire_api.configurada(),
        'puede_crear': puede(request.user, PERMISO_CREAR[p.libro]), 'puede_aceptar': puede(request.user,
                                                                                            PERMISO_ACEPTAR)})
