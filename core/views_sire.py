"""SIRE: propuestas de los registros de ventas (RVIE) y compras (RCE) traídas de SUNAT por la API o subidas a mano;
cruce con lo registrado, creación de lo que falta y aceptación de la propuesta en SUNAT. «Enviar al SIRE» (SIR-01/02):
reemplazo de la propuesta, no domiciliados 8.5 y ajustes posteriores armados con lo registrado."""
import io

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from compras import sire

from . import sire_api, sire_envio
from .forms import BootstrapMixin
from .models import EnvioSIRE, PropuestaSIRE
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
                                    'Empresa para traerla de SUNAT.')
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
        'p': p, 'r': resultado, 'sesion': sire_api.hay_token(), 'configurada': sire_api.configurada(),
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
                messages.error(request, 'Registre las credenciales del SIRE en Ajustes › Empresa para enviar a SUNAT '
                                        '(o descargue el ZIP y súbalo en el portal SIRE).')
            else:
                return _enviar(request, datos, archivo, correlativo)
        else:
            messages.success(request, f'{archivo["nombre_zip"]}: {archivo["filas"]} comprobante(s) sin errores.')
    registros = [k for k, _ in EnvioSIRE.REGISTROS if sire_envio.REGISTROS[k] in libros]
    return render(request, 'core/sire_envios.html', {
        'form': form, 'errores': errores, 'avisos': avisos, 'configurada': sire_api.configurada(),
        'sesion': sire_api.hay_token(), 'puede_enviar': puede(request.user, PERMISO_ENVIAR),
        'envios': EnvioSIRE.objects.filter(registro__in=registros).select_related('usuario')[:60]})


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
