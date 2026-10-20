"""Ajustes › Empresas del grupo: empresas, creación de empresas y usuarios corporativos."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from erp import empresas as reg

from . import grupo
from .models import EmpresaGrupo, UsuarioCorporativo


def _exigir_corporativo(request):
    if grupo.corporativo(request) is None:
        raise Http404


@login_required
def empresas(request):
    _exigir_corporativo(request)
    principal = grupo.corporativo(request)
    accion = request.POST.get('accion') if request.method == 'POST' else None
    if accion == 'crear':
        if not grupo.puede_crear(request):
            messages.error(request, 'Su usuario corporativo no crea empresas.')
        else:
            try:
                r = grupo.crear_empresa(request.POST.get('razon_social'), request.POST.get('ruc'), principal)
                messages.success(request, f'Creando {r.razon_social}: tarda un par de minutos. Esta página se '
                                          f'actualiza sola.')
            except grupo.ErrorGrupo as exc:
                messages.error(request, str(exc))
        return redirect('grupo_panel' if request.POST.get('volver') == 'panel' else 'grupo_empresas')
    if accion == 'reintentar' and grupo.puede_crear(request):
        r = get_object_or_404(EmpresaGrupo.objects.using('default'), pk=request.POST.get('id'), estado='ERROR')
        grupo.reintentar(r, principal)
        messages.info(request, f'Reintentando la creación de {r.razon_social}.')
        return redirect('grupo_empresas')
    if accion == 'corporativo' and principal.is_superuser:
        User = get_user_model()
        u = get_object_or_404(User.objects.using('default'), pk=request.POST.get('usuario'))
        if request.POST.get('quitar'):
            UsuarioCorporativo.objects.filter(usuario_id=u.pk).delete()
            messages.info(request, f'{u.username} ya no es usuario corporativo.')
        else:
            elegidas = [a for a in request.POST.getlist('empresas') if a in reg.empresas()]
            UsuarioCorporativo.objects.update_or_create(usuario_id=u.pk, defaults={
                'todas': bool(request.POST.get('todas')), 'empresas': elegidas,
                'puede_crear': bool(request.POST.get('puede_crear'))})
            messages.success(request, f'{u.username} es usuario corporativo.')
        return redirect('grupo_empresas')
    registradas = {e.alias: e for e in EmpresaGrupo.objects.using('default')}
    filas = []
    for alias in reg.empresas():
        e = registradas.get(alias)
        filas.append({'alias': alias, 'nombre': reg.razon_social(alias), 'registro': e,
                      'puede_entrar': alias in dict(grupo.empresas_de(request)) and (e is None or e.estado == 'LISTA'),
                      'actual': alias == reg.actual()})
    filas += [{'alias': e.alias, 'nombre': e.razon_social, 'registro': e, 'puede_entrar': False, 'actual': False}
              for e in registradas.values() if e.alias not in reg.empresas()]
    corporativos = []
    if principal.is_superuser:
        User = get_user_model()
        actuales = {c.usuario_id: c for c in UsuarioCorporativo.objects.all()}
        corporativos = [{'u': u, 'c': actuales.get(u.pk)} for u in User.objects.using('default').filter(
            is_active=True).order_by('-is_superuser', 'username')]
    return render(request, 'core/grupo_empresas.html', {
        'filas': filas, 'puede_crear': grupo.puede_crear(request), 'principal': principal,
        'corporativos': corporativos, 'aliases': list(reg.empresas()),
        'creando': any(f['registro'] and f['registro'].estado == 'CREANDO' for f in filas)})


def _resumen(alias):
    """Indicadores de una empresa para el panel (sin datos sensibles más allá de totales)."""
    from django.db.models import Max, Sum
    from django.utils import timezone

    from .models import CorreoConfig, Empresa, FacturacionConfig, Serie
    token = reg.activar(alias)
    try:
        from ventas.models import Venta
        hoy = timezone.localdate()
        e = Empresa.objects.first()
        ventas = Venta.objects.filter(periodo=hoy.strftime('%Y%m')).exclude(estado='ANULADO')
        pendientes = []
        if not Serie.objects.filter(activo=True).exists():
            pendientes.append('series')
        correo, facturacion = CorreoConfig.objects.first(), FacturacionConfig.objects.first()  # solo lectura
        if not (correo and correo.activa):
            pendientes.append('correo')
        if not (facturacion and facturacion.activa):
            pendientes.append('facturación electrónica')
        if e and not (e.direccion or '').strip():
            pendientes.append('dirección')
        return {'ruc': e.ruc if e else '', 'usuarios': get_user_model().objects.filter(is_active=True).count(),
                'ventas_mes': ventas.aggregate(t=Sum('total_pen'))['t'] or 0,
                'ultima_venta': Venta.objects.aggregate(f=Max('fecha_emision'))['f'], 'pendientes': pendientes,
                'error': ''}
    except Exception as exc:  # base no disponible o en creación
        return {'error': type(exc).__name__}
    finally:
        reg.restaurar(token)


@login_required
def panel(request):
    """Panel del administrador del grupo: todas las empresas, su estado y lo que les falta configurar."""
    _exigir_corporativo(request)
    principal = grupo.corporativo(request)
    if request.method == 'POST' and request.POST.get('accion') == 'nuevo_corporativo':
        if not principal.is_superuser:
            messages.error(request, 'Solo un administrador de la empresa principal crea usuarios corporativos.')
            return redirect('grupo_panel')
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        User = get_user_model()
        usuario = (request.POST.get('usuario') or '').strip()
        clave = request.POST.get('clave') or ''
        try:
            if not usuario or User.objects.using('default').filter(username__iexact=usuario).exists():
                raise ValidationError('Indique un usuario que no exista.')
            validate_password(clave)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect('grupo_panel')
        token = reg.activar('default')
        try:
            nombres = (request.POST.get('nombre') or '').strip().split(' ', 1)
            u = User.objects.create_user(usuario, (request.POST.get('email') or '').strip(), clave,
                                         first_name=nombres[0], last_name=nombres[1] if len(nombres) > 1 else '')
            UsuarioCorporativo.objects.create(usuario=u, todas=bool(request.POST.get('todas')),
                                              empresas=[a for a in request.POST.getlist('empresas')
                                                        if a in reg.empresas()],
                                              puede_crear=bool(request.POST.get('puede_crear')))
        finally:
            reg.restaurar(token)
        messages.success(request, f'Usuario corporativo {u.username} creado: ingresa en la empresa principal y '
                                  f'pasa a sus empresas desde el menú de usuario.')
        return redirect('grupo_panel')
    registradas = {e.alias: e for e in EmpresaGrupo.objects.using('default')}
    permitidas = dict(grupo.empresas_de(request))
    tarjetas = []
    for alias in reg.empresas():
        if alias not in permitidas:
            continue
        e = registradas.get(alias)
        listo = e is None or e.estado == 'LISTA'
        resumen = _resumen(alias) if listo else {}
        tarjetas.append({'alias': alias, 'nombre': reg.razon_social(alias), 'registro': e, 'actual': alias == reg.actual(),
                         'listo': listo and not resumen.get('error'),
                         **resumen, 'ruc': resumen.get('ruc') or (e.ruc if e else '')})
    tarjetas += [{'alias': e.alias, 'nombre': e.razon_social, 'registro': e, 'actual': False, 'listo': False,
                  'ruc': e.ruc} for e in registradas.values() if e.alias not in reg.empresas()]
    from . import estado_servicio as es
    from django.conf import settings
    return render(request, 'core/grupo_panel.html', {
        'tarjetas': tarjetas, 'principal': principal, 'puede_crear': grupo.puede_crear(request),
        'aliases': list(reg.empresas()), 'version': settings.ERP_VERSION, 'disp_30': es.disponibilidad(30),
        'creando': any(t['registro'] and t['registro'].estado == 'CREANDO' for t in tarjetas)})


@login_required
def entrar(request):
    if request.method != 'POST':
        return redirect('grupo_empresas')
    try:
        grupo.entrar(request, request.POST.get('empresa', ''))
        messages.success(request, f'Ahora trabaja en {reg.razon_social(request.session["empresa"])}.')
        return redirect('home')
    except grupo.ErrorGrupo as exc:
        messages.error(request, str(exc))
        return redirect(request.META.get('HTTP_REFERER') or 'home')


@login_required
def estado(request):
    _exigir_corporativo(request)
    return JsonResponse({e.alias: {'estado': e.estado, 'mensaje': e.mensaje}
                         for e in EmpresaGrupo.objects.using('default')})
