"""SIRE: propuestas de los registros de ventas (RVIE) y compras (RCE) traídas de SUNAT por la API o subidas a mano;
cruce con lo registrado, creación de lo que falta y aceptación de la propuesta en SUNAT. «Enviar al SIRE» (SIR-01/02):
reemplazo de la propuesta, no domiciliados 8.5 y ajustes posteriores armados con lo registrado. «Libro oficial SIRE»:
el registro generado en SUNAT y su constancia, guardados como sustento y comparados con el ERP."""
import io
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from compras import sire

from . import sire_api, sire_envio
from .forms import BootstrapMixin
from .models import TIPO_COMPROBANTE, Empresa, EnvioSIRE, PropuestaSIRE, RegistroSIRE
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
    archivo = forms.FileField(label='O suba el archivo de la propuesta', required=False,
                              help_text='TXT, ZIP, CSV o Excel descargado del SIRE. En compras puede agregarle las '
                                        'columnas Orden de compra, Clasificación (1 a 8) y Cuenta contable')

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


def _sin_filas(contenido, nombre):
    """El archivo de SUNAT no trae filas de comprobantes (vacío o solo títulos)."""
    try:
        filas = sire._filas(_archivo(contenido, nombre))
    except Exception:
        return False
    return sum(1 for f in filas if any(str(v or '').strip() for v in f)) <= 1


def _con_vencimiento(objetos):
    """v1.115.6: a cada propuesta o envío le pone la fecha de vencimiento del registro electrónico de su periodo
    (cronograma de SUNAT por último dígito del RUC) y su estado (vencido, vence hoy, por vencer, a tiempo)."""
    from .cronograma_sunat import estado_vencimiento, vencimiento_registros
    e = Empresa.actual()
    lista = list(objetos)
    for o in lista:
        o.vence = vencimiento_registros(e.ruc, o.periodo, e.buen_contribuyente)
        cumplido = bool(getattr(o, 'aceptada_en', None) or getattr(o, 'registrado_en', None))
        o.vence_estado, o.vence_dias = estado_vencimiento(o.vence, cumplido)
    return lista


def sin_datos(libro, periodo):
    """v1.115.3: mensaje cuando la propuesta no trae comprobantes."""
    registro = 'compras' if libro == 'RCE' else 'ventas'
    return f'No se encontraron datos: la propuesta de {registro} de {periodo[4:]}/{periodo[:4]} no trae comprobantes.'


def _actualizar(p):
    """Consulta el ticket; si SUNAT terminó, descarga el archivo y lee los comprobantes."""
    estado = sire_api.estado_ticket(p.libro, p.periodo, p.ticket)
    if estado['error']:
        p.estado, p.mensaje = 'ERROR', f'SUNAT: {estado["descripcion"]}'
    elif estado['terminado']:
        if not (estado['registro'] or {}).get('archivoReporte'):  # SUNAT terminó sin archivo: no hay comprobantes
            p.registros, p.estado, p.mensaje = [], 'LISTA', sin_datos(p.libro, p.periodo)
            p.save()
            return
        nombre, contenido = sire_api.descargar_archivo(p.libro, estado['registro'])
        p.archivo, p.nombre_archivo = contenido, nombre[:200]
        try:
            registros = _leer(contenido, nombre)
        except sire.ErrorSIRE as exc:
            if _sin_filas(contenido, nombre):  # archivo vacío o solo con títulos: no hay comprobantes
                p.registros, p.estado, p.mensaje = [], 'LISTA', sin_datos(p.libro, p.periodo)
            else:
                p.estado, p.mensaje = 'ERROR', str(exc)
        else:
            p.registros, p.estado = sire.a_json(registros), 'LISTA'
            p.mensaje = (f'{len(registros)} comprobantes en la propuesta.' if registros
                         else sin_datos(p.libro, p.periodo))
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
            if not registros:  # v1.115.3
                messages.warning(request, f'{sin_datos(libro, periodo)} Revise que sea el archivo correcto.')
                return redirect(request.get_full_path())
            aviso = sire.otro_periodo(registros, periodo)
            if aviso:  # v1.115: no se carga una propuesta de otro periodo
                messages.error(request, aviso)
                return redirect(request.get_full_path())
            archivo.seek(0)
            p = PropuestaSIRE.objects.create(
                libro=libro, periodo=periodo, origen='ARCHIVO', estado='LISTA', archivo=archivo.read(),
                nombre_archivo=archivo.name[:200], registros=sire.a_json(registros), usuario=request.user,
                mensaje=f'{len(registros)} comprobantes en la propuesta.')
            return redirect('sire_propuesta', p.pk)
        if not sire_api.configurada():
            messages.error(request, 'Suba el archivo de la propuesta, o registre las credenciales del SIRE en Ajustes › '
                                    'Credenciales SUNAT y SIRE para traerla de SUNAT.')
            return redirect(request.get_full_path())
        try:
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
        'form': form, 'propuestas': _con_vencimiento(PropuestaSIRE.objects.filter(libro__in=claves)
                                                     .select_related('usuario')[:60]),
        'configurada': sire_api.configurada(), 'sesion': sire_api.hay_token()})


def _excel(p, r):
    """El cruce con todas las columnas que entrega SUNAT en la propuesta (se vuelven a leer del archivo guardado)."""
    encabezados, registros = [], []
    if p.archivo:
        try:
            encabezados, registros = sire.leer_completo(_archivo(bytes(p.archivo), p.nombre_archivo or 'propuesta.zip'))
        except sire.ErrorSIRE:
            pass
    compras = p.libro == 'RCE'  # en compras: orden de compra, clasificación y cuenta para precargar
    encabezados, filas = sire.filas_cruce_completo(encabezados, registros, r, propias=compras)
    return excel_response(f'Cruce_SIRE_{p.libro}_{p.periodo}', f'CRUCE PROPUESTA SIRE {p.libro} {p.periodo}',
                          encabezados, filas, rojas=sire.celdas_en_rojo(encabezados, filas) if compras else ())


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
            elif sire.otro_periodo(sire.de_json(p.registros), p.periodo):
                messages.error(request, sire.otro_periodo(sire.de_json(p.registros), p.periodo))
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
            elif sire.otro_periodo(registros, p.periodo):
                messages.error(request, sire.otro_periodo(registros, p.periodo))
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
        resultado['otro_periodo'] = sire.otro_periodo(registros, p.periodo)
        if request.GET.get('formato') == 'excel':
            return _excel(p, resultado)
        for x in resultado['solo_sunat']:
            x['clave'] = f'{x["tipo"]}|{x["serie"]}|{x["numero"]}|{x["ruc"]}'
        sire.montos(resultado['solo_sunat'])  # detracción, retención y percepción del Excel (compras y ventas)
        if p.libro == 'RCE':  # orden de compra, clasificación y cuenta del Excel: lo que falta se ve en rojo
            sire.propios([x for x in resultado['solo_sunat'] if not x['otro_periodo']])
            resultado['sin_propios'] = sum(1 for x in resultado['solo_sunat'] if x.get('propios') and
                                           (x['propios']['faltan'] or x['propios']['errores']))
    return render(request, 'core/sire_propuesta.html', {
        'p': _con_vencimiento([p])[0], 'r': resultado, 'sesion': sire_api.hay_token(),
        'configurada': sire_api.configurada(),
        'clasificaciones': sire.clasificaciones(),
        'puede_crear': puede(request.user, PERMISO_CREAR[p.libro]), 'puede_aceptar': puede(request.user,
                                                                                            PERMISO_ACEPTAR)})


# ---------------------------------------------------------------- SIR-01 y SIR-02: enviar lo registrado al SIRE
PERMISO_ENVIAR = PERMISO_ACEPTAR  # reemplazar la propuesta o registrar ajustes en SUNAT, como aceptarla


def _libros_envio(user):
    """Registros que el usuario envía: los de sus módulos (ventas / compras / contabilidad) o los dos con SUNAT."""
    mods = modulos_del_usuario(user)
    return [(k, n) for k, n in PropuestaSIRE.LIBROS if (MODULOS_LIBRO[k] | {'sunat'}) & mods]


class EnvioForm(BootstrapMixin, forms.Form):
    registro = forms.ChoiceField(label='Registro', choices=EnvioSIRE.REGISTROS)
    periodo = forms.CharField(label='Periodo', widget=forms.TextInput(attrs={'type': 'month'}))
    oportunidad = forms.ChoiceField(label='Oportunidad', choices=EnvioSIRE.OPORTUNIDADES,
                                    help_text='02 reemplaza la propuesta de SUNAT; 03 a 05 son ajustes posteriores al '
                                              'registro ya generado (llevan su correlativo)')
    ind_operaciones = forms.ChoiceField(label='Indicador de operaciones', initial='1',
                                        choices=[('1', '1 Con operaciones en el periodo'),
                                                 ('0', '0 Sin operaciones')])
    ind_moneda = forms.ChoiceField(label='Moneda', initial='1', choices=[('1', '1 Soles (PEN)'),
                                                                         ('2', '2 Dólares (USD)')])
    solo_nuevos = forms.BooleanField(
        label='Ajuste: solo los comprobantes registrados después del último envío', required=False, initial=True,
        help_text='Sin marcar, el ajuste lleva todos los comprobantes del periodo')

    def __init__(self, *args, libros=None, **kwargs):
        super().__init__(*args, **kwargs)
        if libros is not None:
            self.fields['registro'].choices = [(k, n) for k, n in EnvioSIRE.REGISTROS
                                               if sire_envio.REGISTROS[k] in libros]

    def clean_periodo(self):
        valor = self.cleaned_data['periodo'].replace('-', '')
        if len(valor) != 6 or not valor.isdigit() or not 1 <= int(valor[4:]) <= 12:
            raise forms.ValidationError('Periodo no válido.')
        return valor

    def clean(self):
        datos = super().clean()
        if datos.get('ind_operaciones') == '0' and datos.get('oportunidad') in sire_envio.AJUSTES:
            raise forms.ValidationError('Un ajuste posterior lleva comprobantes: el indicador de operaciones es 1.')
        return datos


def _ultimo_envio(registro, periodo):
    return (EnvioSIRE.objects.filter(registro=registro, periodo=periodo).exclude(ticket='')
            .exclude(estado='ERROR').order_by('-creado').first())


def _correlativo(registro, periodo):
    return EnvioSIRE.objects.filter(registro=registro, periodo=periodo, oportunidad__in=sire_envio.AJUSTES) \
        .exclude(ticket='').count() + 1


def _preparar(datos):
    """(archivo generado, errores, avisos, correlativo) con lo registrado en el periodo."""
    registro, periodo, oportunidad = datos['registro'], datos['periodo'], datos['oportunidad']
    desde = None
    if oportunidad in sire_envio.AJUSTES and datos.get('solo_nuevos'):
        ultimo = _ultimo_envio(registro, periodo)
        desde = ultimo.creado if ultimo else None
    docs = [] if datos['ind_operaciones'] == '0' else sire_envio.documentos(registro, periodo, desde)
    errores, avisos = sire_envio.validar(registro, periodo, docs)
    if oportunidad in sire_envio.AJUSTES and not docs:
        errores.append('No hay comprobantes para el ajuste posterior en el periodo.')
    if datos['ind_operaciones'] == '1' and not docs and oportunidad == '02':
        avisos.append('No hay comprobantes registrados en el periodo: el archivo va vacío (indicador de contenido 0).')
    correlativo = _correlativo(registro, periodo) if oportunidad in sire_envio.AJUSTES else None
    archivo = sire_envio.generar(registro, periodo, oportunidad, datos['ind_operaciones'], datos['ind_moneda'],
                                 correlativo, docs=docs)
    return archivo, errores, avisos, correlativo


def _actualizar_envio(e):
    """Consulta el ticket del envío; al terminar descarga el reporte de SUNAT (inconsistencias) si lo hay."""
    estado = sire_api.estado_ticket(e.libro, e.periodo, e.ticket)
    registro = estado['registro'] or {}
    if not (estado['terminado'] or estado['error']):
        e.estado, e.mensaje = 'ENVIADO', f'SUNAT procesa el archivo ({estado["descripcion"]}).'
        e.save()
        return
    aviso = ''
    if registro.get('archivoReporte'):
        try:
            nombre, contenido = sire_api.descargar_archivo(e.libro, registro)
        except sire_api.ErrorSIRE as exc:
            aviso = f' No se pudo descargar su reporte: {exc}'
        else:
            e.archivo_respuesta, e.nombre_respuesta = contenido, nombre[:200]
            e.inconsistencias = sire_envio.leer_respuesta(contenido)
    e.estado = 'ERROR' if estado['error'] else 'OBSERVADO' if e.inconsistencias else 'TERMINADO'
    e.mensaje = (f'SUNAT: {estado["descripcion"]}' + (f' · {len(e.inconsistencias)} línea(s) en su reporte de '
                                                      f'inconsistencias' if e.inconsistencias else '') + aviso)
    e.save()


def _periodo_inicial(request):
    p = request.GET.get('periodo', '')
    return f'{p[:4]}-{p[4:6]}' if len(p) == 6 and p.isdigit() else _periodo_anterior()


@login_required
def envios(request):
    """«Enviar al SIRE»: arma el TXT (en ZIP) con lo registrado, lo valida y lo descarga o lo envía a SUNAT."""
    libros = dict(_libros_envio(request.user))
    form = EnvioForm(request.POST or None, libros=libros, initial={
        'periodo': _periodo_inicial(request), 'oportunidad': request.GET.get('oportunidad') or '02',
        'registro': request.GET.get('registro') or {'RVIE': '140400'}.get(request.GET.get('libro'), '080400')})
    errores = avisos = None
    if request.method == 'POST' and form.is_valid():
        datos = form.cleaned_data
        archivo, errores, avisos, correlativo = _preparar(datos)
        accion = request.POST.get('accion')
        if errores:
            messages.error(request, f'{len(errores)} error(es) en lo registrado: corríjalos antes de generar el '
                                    f'archivo.')
        elif accion == 'descargar':
            from django.http import HttpResponse
            resp = HttpResponse(archivo['zip'], content_type='application/zip')
            resp['Content-Disposition'] = f'attachment; filename="{archivo["nombre_zip"]}"'
            return resp
        elif accion == 'enviar':
            if not puede(request.user, PERMISO_ENVIAR):
                messages.error(request, 'Enviar el registro a SUNAT exige el permiso de cerrar periodos contables.')
            elif not sire_api.configurada():
                messages.error(request, 'Registre las credenciales del SIRE en Ajustes › Credenciales SUNAT y SIRE para enviar a SUNAT '
                                        '(o descargue el ZIP y súbalo en el portal SIRE).')
            else:
                return _enviar(request, datos, archivo, correlativo)
        else:
            messages.success(request, f'{archivo["nombre_zip"]}: {archivo["filas"]} comprobante(s) sin errores.')
    registros = [k for k, _ in EnvioSIRE.REGISTROS if sire_envio.REGISTROS[k] in libros]
    return render(request, 'core/sire_envios.html', {
        'form': form, 'errores': errores, 'avisos': avisos, 'configurada': sire_api.configurada(),
        'sesion': sire_api.hay_token(), 'puede_enviar': puede(request.user, PERMISO_ENVIAR),
        'envios': _con_vencimiento(EnvioSIRE.objects.filter(registro__in=registros).select_related('usuario')[:60])})


def _enviar(request, datos, archivo, correlativo):
    e = EnvioSIRE.objects.create(
        libro=sire_envio.REGISTROS[datos['registro']], registro=datos['registro'], periodo=datos['periodo'],
        oportunidad=datos['oportunidad'], correlativo=correlativo, ind_operaciones=datos['ind_operaciones'],
        ind_moneda=datos['ind_moneda'], nombre_archivo=archivo['nombre_zip'], archivo=archivo['zip'],
        filas=archivo['filas'], usuario=request.user, mensaje='Generado.')
    try:
        e.ticket = sire_api.subir_archivo(e.libro, e.registro, e.oportunidad, e.periodo, e.nombre_archivo,
                                          archivo['zip'])
    except sire_api.ErrorSIRE as exc:
        e.estado, e.mensaje = 'ERROR', f'SUNAT no recibió el archivo: {exc}'
        e.save()
        messages.error(request, e.mensaje)
        return redirect('sire_envio', e.pk)
    e.estado, e.mensaje = 'ENVIADO', 'Enviado a SUNAT.'
    e.save()
    from .auditoria import registrar
    registrar('CREAR', e, {'Envío SIRE': ['', f'{e.nombre_archivo} (ticket {e.ticket})']})
    messages.success(request, f'{e.nombre_archivo} enviado a SUNAT con {e.filas} comprobante(s): ticket {e.ticket}.')
    try:
        _actualizar_envio(e)
    except sire_api.ErrorSIRE as exc:
        messages.warning(request, f'Ticket {e.ticket} creado; aún no se pudo consultar: {exc}')
    return redirect('sire_envio', e.pk)


@login_required
def envio(request, pk):
    e = get_object_or_404(EnvioSIRE, pk=pk)
    if e.libro not in dict(_libros_envio(request.user)):
        messages.error(request, 'Su usuario no tiene el módulo de este registro.')
        return redirect('sire_envios')
    descargar = request.GET.get('descargar')
    contenido = {'archivo': e.archivo, 'respuesta': e.archivo_respuesta}.get(descargar)
    if contenido:
        from django.http import HttpResponse
        resp = HttpResponse(bytes(contenido), content_type='application/zip')
        nombre = e.nombre_archivo if descargar == 'archivo' else (e.nombre_respuesta or 'respuesta_sunat.zip')
        resp['Content-Disposition'] = f'attachment; filename="{nombre}"'
        return resp
    accion = request.POST.get('accion') if request.method == 'POST' else None
    try:
        if accion == 'consultar' and e.ticket and e.estado == 'ENVIADO':
            _actualizar_envio(e)
            (messages.success if e.estado == 'TERMINADO' else messages.info)(request, e.mensaje)
        elif accion == 'registrar':
            if not puede(request.user, PERMISO_ENVIAR):
                messages.error(request, 'Registrar el preliminar en SUNAT exige el permiso de cerrar periodos '
                                        'contables.')
            elif e.estado not in ('TERMINADO', 'OBSERVADO') or e.registrado_en:
                messages.error(request, 'El envío aún no termina en SUNAT o ya se registró.')
            else:
                e.ticket_registro = sire_api.registrar_preliminar(e.libro, e.periodo, ajuste=e.es_ajuste)
                e.registrado_en = timezone.now()
                e.save(update_fields=['ticket_registro', 'registrado_en', 'actualizado'])
                from .auditoria import registrar
                registrar('MODIFICAR', e, {'Envío SIRE': ['Cargado', 'Preliminar registrado en SUNAT' + (
                    f' (ticket {e.ticket_registro})' if e.ticket_registro else '')]})
                messages.success(request, 'Preliminar registrado en SUNAT. Complete la generación del registro en '
                                          'el portal SIRE si SUNAT lo pide.')
    except sire_api.ErrorSIRE as exc:
        messages.error(request, str(exc))
    if accion:
        return redirect('sire_envio', pk)
    return render(request, 'core/sire_envio.html', {
        'e': e, 'puede_enviar': puede(request.user, PERMISO_ENVIAR), 'sesion': sire_api.hay_token(),
        'registra': e.libro in (sire_api.REGISTRAR_AJUSTES if e.es_ajuste else sire_api.REGISTRAR_PRELIMINAR)})


# ---------------------------------------------------------------- libro oficial: el registro generado en el SIRE
# El registro del periodo ya generado en SUNAT (RVIE 14.4 / RCE 8.4) se baja con un ticket (sire_api.solicitar_registro),
# se guarda como sustento con su constancia de recepción (PDF) y se compara con lo registrado en el ERP: cantidad y
# totales por tipo de comprobante (en soles) y la lista de diferencias (mismas claves que compras/sire.py cruzar).
# Mismos permisos que las propuestas: el registro se ve con su módulo (ventas / compras / contabilidad).

class LibroOficialForm(BootstrapMixin, forms.Form):
    libro = forms.ChoiceField(label='Registro', choices=RegistroSIRE.LIBROS)
    periodo = forms.CharField(label='Periodo', widget=forms.TextInput(attrs={'type': 'month'}))

    def __init__(self, *args, libros=None, **kwargs):
        super().__init__(*args, **kwargs)
        if libros is not None:
            self.fields['libro'].choices = libros

    def clean_periodo(self):
        valor = self.cleaned_data['periodo'].replace('-', '')
        if len(valor) != 6 or not valor.isdigit() or not 1 <= int(valor[4:]) <= 12:
            raise forms.ValidationError('Periodo no válido.')
        return valor


def _texto_registro(datos):
    for codificacion in ('utf-8-sig', 'latin-1'):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return ''


def leer_registro(libro, contenido, nombre=''):
    """(registros, nombre del TXT) del archivo del registro generado que entrega SUNAT (ZIP con el TXT o el TXT). Si
    el TXT viene sin títulos, se leen sus columnas con la estructura del SIRE (14.4 / 8.4, core/sire_envio.py)."""
    import zipfile
    codigo = sire_api.CODIGO_REGISTRO[libro]
    datos, nombre_txt = bytes(contenido or b''), nombre or 'registro.txt'
    if datos[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                txts = [n for n in z.namelist() if n.lower().endswith(('.txt', '.csv'))]
                if not txts:
                    raise sire.ErrorSIRE('El ZIP de SUNAT no contiene el TXT del registro.')
                nombre_txt = next((n for n in txts if codigo in n), txts[0])
                datos = z.read(nombre_txt)
        except zipfile.BadZipFile as exc:
            raise sire.ErrorSIRE('El ZIP del registro que entregó SUNAT está dañado.') from exc
    texto = _texto_registro(datos)
    primera = sire._norm(texto.splitlines()[0] if texto.strip() else '')
    if 'serie' not in primera or 'total' not in primera:  # TXT plano, sin la fila de títulos
        texto = '|'.join(sire_envio.COLUMNAS[codigo]) + '\n' + texto
    return sire.leer(_archivo(texto.encode('utf-8'), 'registro.txt')), nombre_txt


def _soles_sunat(libro, r):
    """Total del comprobante del registro de SUNAT en soles: el RCE ya viene en soles; el RVIE viene en la moneda
    del comprobante con su tipo de cambio."""
    total = r.get('total') or sire.D0
    if libro == 'RVIE' and (r.get('moneda') or 'PEN') != 'PEN' and r.get('tc'):
        return (total * r['tc']).quantize(Decimal('0.01'))
    return total


def _signo(tipo):
    return -1 if tipo == '07' else 1  # las notas de crédito restan en el total del registro


def comparar_registro(libro, registros, periodo):
    """Compara el registro generado en SUNAT con el del ERP (datos guardables en JSON): {'tipos': [{tipo, nombre,
    sunat_n, sunat_total, erp_n, erp_total, dif_n, dif_total}], 'totales': {...}, 'coinciden', 'diferencias': [...]}.
    Importes en soles; las notas de crédito restan en los totales generales."""
    r = sire.cruzar(libro, registros, periodo)
    nombres = dict(TIPO_COMPROBANTE)
    tipos = {}

    def fila(tipo):
        return tipos.setdefault(tipo, {'tipo': tipo, 'nombre': nombres.get(tipo, tipo), 'sunat_n': 0,
                                       'sunat_total': sire.D0, 'erp_n': 0, 'erp_total': sire.D0})
    for x in registros:
        f = fila(x['tipo'])
        f['sunat_n'] += 1
        f['sunat_total'] += _soles_sunat(libro, x)
    for d in [x['compra'] for x in r['coinciden'] + r['diferencias']] + list(r['solo_erp']):
        f = fila(d.tipo_comprobante)
        f['erp_n'] += 1
        f['erp_total'] += sire.soles_del_erp(d)
    totales = {'sunat_n': 0, 'sunat_total': sire.D0, 'erp_n': 0, 'erp_total': sire.D0}
    lista = []
    for tipo in sorted(tipos):
        f = tipos[tipo]
        f['dif_n'], f['dif_total'] = f['erp_n'] - f['sunat_n'], f['erp_total'] - f['sunat_total']
        for k in totales:
            totales[k] += f[k] * (_signo(tipo) if k.endswith('total') else 1)
        lista.append({**f, **{k: str(f[k]) for k in ('sunat_total', 'erp_total', 'dif_total')}})
    totales['dif_n'], totales['dif_total'] = (totales['erp_n'] - totales['sunat_n'],
                                              totales['erp_total'] - totales['sunat_total'])
    diferencias = []
    for x in r['diferencias']:
        d = x['compra']
        sunat, erp = _soles_sunat(libro, x), sire.soles_del_erp(d)
        diferencias.append({'resultado': 'Diferencia de importe', 'tipo': x['tipo'], 'serie': x['serie'],
                            'numero': x['numero'], 'ruc': x['ruc'], 'nombre': x['nombre'], 'sunat_total': str(sunat),
                            'erp_total': str(erp), 'dif': str(erp - sunat), 'id': d.pk})
    for x in r['solo_sunat']:
        otro = x.get('otro_periodo')
        sunat = _soles_sunat(libro, x)
        diferencias.append({'resultado': 'Solo en SUNAT' + (f' (registrado en {otro.periodo})' if otro else ''),
                            'tipo': x['tipo'], 'serie': x['serie'], 'numero': x['numero'], 'ruc': x['ruc'],
                            'nombre': x['nombre'], 'sunat_total': str(sunat), 'erp_total': None,
                            'dif': str(-sunat), 'id': None})
    for d in r['solo_erp']:
        erp = sire.soles_del_erp(d)
        diferencias.append({'resultado': 'Solo en el ERP', 'tipo': d.tipo_comprobante, 'serie': d.serie,
                            'numero': d.numero, 'ruc': d.tercero.numero_doc, 'nombre': d.tercero.nombre,
                            'sunat_total': None, 'erp_total': str(erp), 'dif': str(erp), 'id': d.pk})
    return {'tipos': lista, 'totales': {k: str(v) for k, v in totales.items()}, 'coinciden': len(r['coinciden']),
            'con_diferencia': len(r['diferencias']), 'solo_sunat': len(r['solo_sunat']),
            'solo_erp': len(r['solo_erp']), 'diferencias': diferencias,
            'fecha': timezone.localtime().strftime('%d/%m/%Y %H:%M')}


def _bajar_constancia(x):
    """Descarga la constancia de recepción del registro y la guarda."""
    x.constancia = sire_api.descargar_constancia(x.libro, x.nombre_constancia)
    x.constancia_en = timezone.now()


def _actualizar_registro(x):
    """Consulta el ticket; si SUNAT terminó, baja el registro (ZIP con el TXT), lo lee, lo compara con el ERP y baja
    su constancia de recepción."""
    estado = sire_api.estado_ticket(x.libro, x.periodo, x.ticket)
    if estado['error']:
        x.estado, x.mensaje = 'ERROR', f'SUNAT: {estado["descripcion"]}'
    elif estado['terminado']:
        ticket = estado['registro'] or {}
        if not ticket.get('archivoReporte'):
            x.estado = 'ERROR'
            x.mensaje = ('SUNAT terminó sin entregar el archivo del registro: revise en el portal SIRE que el '
                         f'registro de {x.periodo[4:]}/{x.periodo[:4]} esté generado.')
            x.save()
            return
        nombre, contenido = sire_api.descargar_archivo(x.libro, ticket)
        x.archivo, x.nombre_archivo = contenido, nombre[:200]
        try:
            registros, nombre_txt = leer_registro(x.libro, contenido, nombre)
        except sire.ErrorSIRE as exc:
            x.estado, x.mensaje = 'ERROR', str(exc)
        else:
            x.registros, x.estado = sire.a_json(registros), 'DESCARGADO'
            x.nombre_constancia = (x.nombre_constancia or sire_api.nombre_constancia(
                nombre_txt, nombre, *[a.get('nomArchivoContenido') for a in ticket['archivoReporte']]))[:200]
            x.comparacion = comparar_registro(x.libro, registros, x.periodo)
            x.mensaje = f'{len(registros)} comprobantes en el registro generado en SUNAT.'
            if x.nombre_constancia and not x.constancia:
                try:
                    _bajar_constancia(x)
                except sire_api.ErrorSIRE as exc:
                    x.mensaje += f' No se pudo bajar la constancia de recepción: {exc}'
    else:
        x.mensaje = f'SUNAT está preparando el archivo del registro ({estado["descripcion"]}).'
    x.save()


@login_required
def libro_oficial(request):
    """«Libro oficial SIRE»: baja de SUNAT el registro generado del periodo y su constancia, y lo compara con el ERP."""
    libros = _libros(request.user)
    form = LibroOficialForm(request.POST or None, libros=libros, initial={
        'periodo': _periodo_inicial(request), 'libro': request.GET.get('libro') or 'RCE'})
    if request.method == 'POST' and form.is_valid():
        libro, periodo = form.cleaned_data['libro'], form.cleaned_data['periodo']
        if not sire_api.configurada():
            messages.error(request, 'Registre las credenciales del SIRE en Ajustes › Credenciales SUNAT y SIRE para '
                                    'descargar el registro de SUNAT.')
            return redirect(request.get_full_path())
        try:
            ticket = sire_api.solicitar_registro(libro, periodo)
        except sire_api.ErrorSIRE as exc:
            messages.error(request, str(exc))
            return redirect(request.get_full_path())
        x = RegistroSIRE.objects.create(libro=libro, periodo=periodo, ticket=ticket, usuario=request.user,
                                        mensaje='Solicitado a SUNAT.')
        from .auditoria import registrar
        registrar('CREAR', x, {'Libro oficial SIRE': ['', f'Solicitado a SUNAT (ticket {ticket})']})
        try:
            _actualizar_registro(x)
        except sire_api.ErrorSIRE as exc:
            messages.warning(request, f'Ticket {ticket} creado; aún no se pudo consultar: {exc}')
        return redirect('sire_libro_oficial_detalle', x.pk)
    claves = [k for k, _ in libros]
    return render(request, 'core/sire_libro_oficial.html', {
        'form': form, 'configurada': sire_api.configurada(), 'sesion': sire_api.hay_token(),
        'registros': RegistroSIRE.objects.filter(libro__in=claves).select_related('usuario')
        .defer('archivo', 'constancia')[:60]})


def _excel_registro(x, comparacion):
    filas = [[t['tipo'], t['nombre'], t['sunat_n'], Decimal(t['sunat_total']), t['erp_n'], Decimal(t['erp_total']),
              t['dif_n'], Decimal(t['dif_total'])] for t in comparacion.get('tipos', [])]
    tot = comparacion.get('totales') or {}
    if tot:
        filas.append(['', 'Total (las notas de crédito restan)', tot['sunat_n'], Decimal(tot['sunat_total']),
                      tot['erp_n'], Decimal(tot['erp_total']), tot['dif_n'], Decimal(tot['dif_total'])])
    filas.append([])
    filas.append(['Resultado', 'Tipo', 'Serie', 'Número', 'Doc. tercero', 'Nombre', 'Total SUNAT (S/)',
                  'Total ERP (S/)', 'Diferencia (ERP - SUNAT)'])
    for d in comparacion.get('diferencias', []):
        filas.append([d['resultado'], d['tipo'], d['serie'], d['numero'], d['ruc'], d['nombre'],
                      Decimal(d['sunat_total']) if d['sunat_total'] is not None else None,
                      Decimal(d['erp_total']) if d['erp_total'] is not None else None, Decimal(d['dif'])])
    return excel_response(f'Libro_oficial_SIRE_{x.libro}_{x.periodo}', f'LIBRO OFICIAL SIRE {x.libro} {x.periodo}',
                          ['Tipo', 'Comprobante', 'SUNAT: cantidad', 'SUNAT: total (S/)', 'ERP: cantidad',
                           'ERP: total (S/)', 'Diferencia: cantidad', 'Diferencia: total (S/)'], filas)


@login_required
def libro_oficial_detalle(request, pk):
    x = get_object_or_404(RegistroSIRE, pk=pk)
    if x.libro not in dict(_libros(request.user)):
        messages.error(request, 'Su usuario no tiene el módulo de este registro.')
        return redirect('sire_libro_oficial')
    descargar = request.GET.get('descargar')
    contenido = {'archivo': x.archivo, 'constancia': x.constancia}.get(descargar)
    if contenido:
        from django.http import HttpResponse
        if descargar == 'archivo':
            resp = HttpResponse(bytes(contenido), content_type='application/zip')
            nombre = x.nombre_archivo or f'registro_{x.libro}_{x.periodo}.zip'
        else:
            resp = HttpResponse(bytes(contenido), content_type='application/pdf')
            nombre = x.nombre_constancia or f'constancia_{x.libro}_{x.periodo}.pdf'
        resp['Content-Disposition'] = f'attachment; filename="{nombre}"'
        return resp
    accion = request.POST.get('accion') if request.method == 'POST' else None
    try:
        if accion == 'consultar' and x.ticket and x.estado == 'SOLICITADO':
            _actualizar_registro(x)
            (messages.success if x.estado == 'DESCARGADO' else messages.info)(request, x.mensaje)
        elif accion == 'constancia' and x.estado == 'DESCARGADO':
            nombre = (request.POST.get('nombre_constancia') or x.nombre_constancia or '').strip()
            if not nombre.lower().endswith('.pdf'):
                messages.error(request, 'Indique el nombre de la constancia como lo muestra el portal SIRE (LE….pdf).')
            else:
                x.nombre_constancia = nombre[:200]
                _bajar_constancia(x)
                x.save(update_fields=['nombre_constancia', 'constancia', 'constancia_en', 'actualizado'])
                messages.success(request, 'Constancia de recepción descargada de SUNAT y guardada como sustento.')
        elif accion == 'comparar' and x.estado == 'DESCARGADO':
            x.comparacion = comparar_registro(x.libro, sire.de_json(x.registros), x.periodo)
            x.save(update_fields=['comparacion', 'actualizado'])
            messages.success(request, 'Comparación con el ERP actualizada.')
    except sire_api.ErrorSIRE as exc:
        messages.error(request, str(exc))
    if accion:
        return redirect('sire_libro_oficial_detalle', pk)
    if request.GET.get('formato') == 'excel' and x.comparacion:
        return _excel_registro(x, x.comparacion)
    return render(request, 'core/sire_libro_oficial_detalle.html', {
        'x': x, 'c': x.comparacion or None, 'sesion': sire_api.hay_token(),
        'configurada': sire_api.configurada()})
