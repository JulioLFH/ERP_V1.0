"""Ajustes › IP permitidas e intentos de ingreso (solo administradores): lista de IP o redes autorizadas, interruptor
del control y listado de las IP que abrieron o intentaron abrir el ERP, con «Permitir» de un clic."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from . import acceso_ip
from .models import AccesoIp, ControlIp, IpPermitida


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
    return render(request, 'core/acceso_ip.html', {
        'control': control, 'permitidas': permitidas, 'page_obj': pagina, 'ver': ver, 'mi_ip': mi_ip,
        'mi_ip_en_lista': acceso_ip.permitida(mi_ip, estado),
        'rechazadas': AccesoIp.objects.using('default').filter(bloqueos__gt=0).count()})
