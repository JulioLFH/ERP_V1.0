"""Ajustes › IP permitidas e intentos de ingreso (solo administradores): lista de IP o redes autorizadas, interruptor
del control, listado de las IP que abrieron o intentaron abrir el ERP, con «Permitir» de un clic, y los celulares con
la app Ceiba (aprobar, revocar y versión mínima)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import acceso_ip, app_movil
from .models import AccesoIp, ControlIp, DispositivoApp, IpPermitida


@csrf_exempt  # la app no tiene sesión todavía: la firma del celular reemplaza al token CSRF
@require_POST
def autorizar_app(request):
    datos, codigo = app_movil.autorizar(request.body, acceso_ip.ip_cliente(request))
    return JsonResponse(datos, status=codigo, json_dumps_params={'ensure_ascii': False})


def _celular(request, accion, control):
    """Aprobar, revocar, describir o borrar un celular, o fijar la versión mínima de la app."""
    if accion == 'version_minima':
        version = request.POST.get('version', '').strip()[:20]
        if not app_movil.re.fullmatch(r'\d+(\.\d+){0,2}', version):
            messages.error(request, f'«{version}» no es una versión válida (ej. 1.1.0).')
            return
        fila = ControlIp.objects.using('default').first() or ControlIp(activo=control.activo)
        fila.version_minima_app, fila.modificado_por_id = version, request.user.pk
        fila.save(using='default')
        acceso_ip.invalidar()
        messages.success(request, f'Versión mínima de la app: {version}.')
        return
    celular = DispositivoApp.objects.using('default').filter(pk=request.POST.get('id') or 0).first()
    if celular is None:
        return
    if accion == 'aprobar_celular':
        celular.estado, celular.aprobado_por_id, celular.aprobado_en = 'APROBADO', request.user.pk, timezone.now()
        celular.descripcion = (request.POST.get('descripcion') or celular.descripcion).strip()[:120]
        celular.save(using='default')
        messages.success(request, f'Celular {celular.codigo_corto} aprobado: entra desde cualquier red con la app.')
    elif accion == 'revocar_celular':
        celular.estado = 'REVOCADO'
        celular.save(using='default')
        messages.success(request, f'Celular {celular.codigo_corto} revocado: ya no entra desde fuera de las IP '
                                  f'permitidas.')
    elif accion == 'borrar_celular':
        celular.delete()
        messages.success(request, f'Celular {celular.codigo_corto} borrado del listado.')
    acceso_ip.invalidar()


def _agregar(texto, descripcion, usuario):
    """(IpPermitida, mensaje de error)."""
    try:
        red = acceso_ip.red_valida(texto)
    except ValueError:
        return None, f'«{texto}» no es una IP ni una red válida (ej. 190.12.34.56 o 192.168.1.0/24).'
    valor = str(red.network_address) if red.num_addresses == 1 else str(red)
    if IpPermitida.objects.using('default').filter(ip=valor).exists():
        return None, f'{valor} ya está en la lista.'
    fila = IpPermitida.objects.using('default').create(ip=valor, descripcion=(descripcion or '').strip()[:120],
                                                       creado_por_id=usuario.pk)
    acceso_ip.olvidar(valor)
    acceso_ip.invalidar()
    return fila, ''


@login_required
def acceso_ip_vista(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    mi_ip = acceso_ip.ip_cliente(request)
    control = ControlIp.actual()
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'agregar':
            fila, error = _agregar(request.POST.get('ip', ''), request.POST.get('descripcion', ''), request.user)
            messages.error(request, error) if error else messages.success(request, f'{fila.ip} puede usar el ERP.')
        elif accion == 'permitir':
            fila, error = _agregar(request.POST.get('ip', ''), request.POST.get('descripcion', '') or
                                   'Permitida desde los intentos de ingreso', request.user)
            if error:
                messages.error(request, error)
            else:
                AccesoIp.objects.using('default').filter(ip=fila.ip).update(permitida=True)
                messages.success(request, f'{fila.ip} ya puede usar el ERP.')
        elif accion == 'eliminar':
            fila = IpPermitida.objects.using('default').filter(pk=request.POST.get('id') or 0).first()
            if fila is not None:
                acceso_ip.invalidar()
                if control.activo and not acceso_ip.permitida(mi_ip, {'activo': True, 'redes': [
                        acceso_ip.red_valida(x.ip) for x in IpPermitida.objects.using('default').exclude(pk=fila.pk)]}):
                    messages.error(request, f'No se elimina {fila.ip}: es la IP desde la que usted entra ({mi_ip}) y '
                                            f'quedaría fuera del ERP. Desactive el control o agregue otra primero.')
                else:
                    fila.delete()
                    acceso_ip.invalidar()
                    messages.success(request, f'{fila.ip} ya no puede usar el ERP (si el control está activo).')
        elif accion == 'borrar_intento':
            AccesoIp.objects.using('default').filter(pk=request.POST.get('id') or 0).delete()
        elif accion in ('aprobar_celular', 'revocar_celular', 'borrar_celular', 'version_minima'):
            _celular(request, accion, control)
        elif accion in ('activar', 'desactivar'):
            activar = accion == 'activar'
            redes = [acceso_ip.red_valida(x) for x in IpPermitida.objects.using('default').values_list('ip', flat=True)]
            if activar and not acceso_ip.permitida(mi_ip, {'activo': True, 'redes': redes}):
                messages.error(request, f'Agregue primero su IP ({mi_ip}) a la lista: si no, quedaría fuera del ERP.')
            else:
                fila = ControlIp.objects.using('default').first() or ControlIp()
                fila.activo, fila.modificado_por_id = activar, request.user.pk
                fila.save(using='default')
                acceso_ip.invalidar()
                messages.success(request, 'Control por IP activo: solo entran las IP de la lista (y esta PC).'
                                 if activar else 'Control por IP desactivado: cualquier IP puede abrir el ERP.')
        return redirect('acceso_ip')
    ver = request.GET.get('ver', 'rechazadas' if control.activo else 'todas')
    intentos = AccesoIp.objects.using('default').all()
    if ver == 'rechazadas':
        intentos = intentos.filter(bloqueos__gt=0)
    permitidas = list(IpPermitida.objects.using('default').select_related('creado_por'))
    estado = {'activo': True, 'redes': [acceso_ip.red_valida(p.ip) for p in permitidas]}
    pagina = Paginator(intentos, 50).get_page(request.GET.get('page'))
    for a in pagina:
        a.en_lista = acceso_ip.permitida(a.ip, estado)
    celulares = list(DispositivoApp.objects.using('default').select_related('aprobado_por'))
    for c in celulares:
        c.vigente = app_movil.version_vigente(c.version_app, control.version_minima_app)
    return render(request, 'core/acceso_ip.html', {
        'control': control, 'permitidas': permitidas, 'page_obj': pagina, 'ver': ver, 'mi_ip': mi_ip,
        'mi_ip_en_lista': acceso_ip.permitida(mi_ip, estado),
        'rechazadas': AccesoIp.objects.using('default').filter(bloqueos__gt=0).count(),
        'celulares': celulares,
        'pase_horas': app_movil.PASE_HORAS})
