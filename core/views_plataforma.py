"""REQ-03: consola de la plataforma Ceiba (/plataforma/). Ingreso propio del operador de la plataforma (no es un
usuario del ERP del cliente) y el plan del grupo: los módulos que como máximo usan todas sus empresas."""
from datetime import timedelta

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect

from erp import empresas as reg

from . import plataforma

MAX_INTENTOS, MINUTOS_BLOQUEO = 5, 15


def _ip(request):
    from .acceso import _ip as ip
    return ip(request)


@csrf_protect
def ingresar(request):
    from .models import IntentoAcceso, OperadorPlataforma
    if plataforma.operador_de(request):
        return redirect('plataforma_inicio')
    if request.method == 'POST':
        usuario = (request.POST.get('usuario') or '').strip()[:60]
        clave_limite = f'plataforma:{usuario.lower()}'[:150]
        desde = timezone.now() - timedelta(minutes=MINUTOS_BLOQUEO)
        if IntentoAcceso.objects.using('default').filter(usuario=clave_limite, creado__gte=desde).count() \
                >= MAX_INTENTOS:
            messages.error(request, f'Demasiados intentos. Espere {MINUTOS_BLOQUEO} minutos.')
            return redirect('plataforma_ingresar')
        op = OperadorPlataforma.objects.using('default').filter(usuario__iexact=usuario, activo=True).first()
        if op is None or not op.clave_correcta(request.POST.get('clave') or ''):
            IntentoAcceso.objects.using('default').create(usuario=clave_limite, ip=_ip(request))
            messages.error(request, 'Usuario o contraseña de la plataforma incorrectos.')
            return redirect('plataforma_ingresar')
        IntentoAcceso.objects.using('default').filter(usuario=clave_limite).delete()
        request.session.cycle_key()
        request.session[plataforma.SESION] = op.pk
        op.ultimo_ingreso = timezone.now()
        op.save(using='default', update_fields=['ultimo_ingreso'])
        return redirect('plataforma_inicio')
    return render(request, 'plataforma/ingresar.html')


def salir(request):
    request.session.pop(plataforma.SESION, None)
    messages.info(request, 'Sesión de la plataforma cerrada.')
    return redirect('plataforma_ingresar')


@csrf_protect
def inicio(request):
    op = plataforma.operador_de(request)
    if op is None:
        return redirect('plataforma_ingresar')
    from .modulos import MODULOS
    if request.method == 'POST':
        todos = request.POST.get('todos') == '1'
        elegidos = request.POST.getlist('modulos')
        plan = plataforma.guardar_plan(request.POST.get('nombre'), elegidos, todos, op)
        n = len(plataforma.modulos_del_plan())
        messages.success(request, f'Plan «{plan.nombre}» guardado: {"todos los módulos" if plan.todos else f"{n} módulos"}'
                                  f' para todas las empresas del grupo.')
        return redirect('plataforma_inicio')
    plan = plataforma.plan_actual()
    permitidos = plataforma.modulos_del_plan()  # el tope de Ceiba (el grupo puede apagar algunos dentro de él)
    empresas = []
    for alias in reg.empresas():
        empresas.append({'alias': alias, 'nombre': reg.razon_social(alias), 'principal': alias == 'default'})
    return render(request, 'plataforma/inicio.html', {
        'operador': op, 'plan': plan, 'todos': plan is None or plan.todos,
        'modulos': [{'clave': m['clave'], 'nombre': m['nombre'], 'icono': m.get('icono', 'bi-grid'),
                     'descripcion': m.get('descripcion', ''), 'fijo': m['clave'] in plataforma.FIJOS,
                     'activo': m['clave'] in permitidos} for m in MODULOS],
        'planes': {k: {'nombre': n, 'modulos': sorted(mods)} for k, (n, mods) in plataforma.PLANES.items()},
        'empresas': empresas})
