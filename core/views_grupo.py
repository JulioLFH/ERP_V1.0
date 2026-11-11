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
                r = grupo.crear_empresa(request.POST.get('razon_social'), request.POST.get('ruc'), principal,
                                        direccion=request.POST.get('direccion', ''),
                                        ubigeo=request.POST.get('ubigeo', ''),
                                        modelo=request.POST.get('modelo', ''),
                                        copiar=request.POST.getlist('copiar') if request.POST.get('con_copiado')
                                        else None)
                messages.success(request, f'Creando {r.razon_social}: tarda un par de minutos. Esta página se '
                                          f'actualiza sola.')
            except grupo.ErrorGrupo as exc:
                messages.error(request, str(exc))
        return redirect('grupo_panel' if request.POST.get('volver') == 'panel' else 'grupo_empresas')
    if accion == 'reintentar' and grupo.puede_crear(request):
        r = get_object_or_404(EmpresaGrupo.objects.using('default'), pk=request.POST.get('id'),
                              estado__in=['ERROR', 'CREANDO'])
        if r.estado == 'CREANDO' and not grupo.colgada(r):
            messages.info(request, f'{r.razon_social} todavía se está creando.')
            return redirect(request.POST.get('volver') == 'panel' and 'grupo_panel' or 'grupo_empresas')
        grupo.reintentar(r, principal)
        messages.info(request, f'Reintentando la creación de {r.razon_social}.')
        return redirect('grupo_panel' if request.POST.get('volver') == 'panel' else 'grupo_empresas')
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
    for f in filas:  # creación cortada por un reinicio del servidor: se puede reintentar
        f['colgada'] = grupo.colgada(f['registro'])
    corporativos = []
    if principal.is_superuser:
        User = get_user_model()
        actuales = {c.usuario_id: c for c in UsuarioCorporativo.objects.all()}
        corporativos = [{'u': u, 'c': actuales.get(u.pk)} for u in User.objects.using('default').filter(
            is_active=True).order_by('-is_superuser', 'username')]
    return render(request, 'core/grupo_empresas.html', {
        'filas': filas, 'puede_crear': grupo.puede_crear(request), 'principal': principal,
        'corporativos': corporativos, 'aliases': list(reg.empresas()),
        'activas': [f for f in filas if f['alias'] in reg.empresas() and (
            f['registro'] is None or f['registro'].estado == 'LISTA')],
        'copiables': grupo.COPIABLES, 'copia_defecto': grupo.COPIA_POR_DEFECTO,
        'creando': any(f['registro'] and f['registro'].estado == 'CREANDO' for f in filas)})


@login_required
def correo_ceiba(request):
    """REQ-06: correo general de Ceiba (respaldo de las empresas sin correo propio). Solo el administrador de la
    empresa principal."""
    _exigir_corporativo(request)
    principal = grupo.corporativo(request)
    if not principal.is_superuser:
        return render(request, 'core/sin_acceso.html',
                      {'motivo': 'Solo un administrador de la empresa principal configura el correo de Ceiba.'},
                      status=403)
    from django.conf import settings

    from .correo import ErrorCorreo, enviar
    from .forms import CorreoCeibaForm
    from .models import CorreoCeiba
    cfg = CorreoCeiba.actual()
    if request.method == 'POST' and request.POST.get('accion') == 'probar':
        destino = request.POST.get('destino', '').strip() or principal.email
        try:
            enviar([destino], f'Prueba del correo de {settings.ERP_NOMBRE}', 'proveedores/correo_prueba.html',
                   {'usuario': principal}, solo_ceiba=True)
            messages.success(request, f'Correo de prueba enviado a {destino}.')
        except ErrorCorreo as exc:
            messages.error(request, str(exc) if cfg.activa else 'Guarde primero el servidor y el usuario del correo.')
        return redirect('correo_ceiba')
    form = CorreoCeibaForm(request.POST or None, instance=cfg)
    if request.method == 'POST' and form.is_valid():
        form.instance.save(using='default')
        messages.success(request, 'Correo de Ceiba guardado.')
        return redirect('correo_ceiba')
    sin_propio = []
    for alias in reg.empresas():
        token = reg.activar(alias)
        try:
            from .models import CorreoConfig
            propio = CorreoConfig.objects.first()
            if not (propio and propio.activa):
                sin_propio.append(reg.razon_social(alias))
        except Exception:  # base no disponible
            pass
        finally:
            reg.restaurar(token)
    return render(request, 'core/correo_ceiba.html', {'form': form, 'cfg': cfg, 'sin_propio': sin_propio})


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
            from .correo import correo_ceiba
            if correo_ceiba() is None:  # REQ-06: con el correo de Ceiba la empresa ya puede enviar
                pendientes.append('correo')
        if not (facturacion and facturacion.activa):
            pendientes.append('facturación electrónica')
        if e and not (e.direccion or '').strip():
            pendientes.append('dirección')
        from .estudio import resumen_contable
        return {'ruc': e.ruc if e else '', 'usuarios': get_user_model().objects.filter(is_active=True).count(),
                'ventas_mes': ventas.aggregate(t=Sum('total_pen'))['t'] or 0,
                'ultima_venta': Venta.objects.aggregate(f=Max('fecha_emision'))['f'], 'pendientes': pendientes,
                'contable': resumen_contable(e.ruc if e else '', bool(e and e.buen_contribuyente)), 'error': ''}
    except Exception as exc:  # base no disponible o en creación
        return {'error': type(exc).__name__}
    finally:
        reg.restaurar(token)


@login_required
def panel(request):
    """Panel del administrador del grupo: todas las empresas, su estado y lo que les falta configurar."""
    _exigir_corporativo(request)
    principal = grupo.corporativo(request)
    if request.method == 'POST' and request.POST.get('accion') in ('centralizar', 'cerrar', 'ple'):
        return _proceso_masivo(request)
    if request.method == 'POST' and request.POST.get('accion') == 'nuevo_corporativo':
        if not principal.is_superuser:
            messages.error(request, 'Solo un administrador de la empresa principal crea usuarios corporativos.')
            return redirect('grupo_panel')
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        from .modulos import GRUPOS
        from .plataforma import modulos_permitidos
        User = get_user_model()
        usuario = (request.POST.get('usuario') or '').strip()
        clave = request.POST.get('clave') or ''
        admin = bool(request.POST.get('admin'))
        en_plan = modulos_permitidos()
        modulos = [c for c in GRUPOS if c in en_plan] if admin else \
            [c for c in request.POST.getlist('modulos') if c in GRUPOS and c in en_plan]
        empresas_elegidas = [a for a in request.POST.getlist('empresas') if a in reg.empresas()]
        try:
            if not usuario or User.objects.using('default').filter(username__iexact=usuario).exists():
                raise ValidationError('Indique un usuario que no exista.')
            if not modulos:
                raise ValidationError('Elija los módulos que verá (o márquelo como administrador).')
            if not request.POST.get('todas') and not empresas_elegidas:
                raise ValidationError('Elija las empresas que verá (o marque todas).')
            validate_password(clave)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect('grupo_panel')
        token = reg.activar('default')
        try:
            nombres = (request.POST.get('nombre') or '').strip().split(' ', 1)
            u = User.objects.create_user(usuario, (request.POST.get('email') or '').strip(), clave,
                                         first_name=nombres[0], last_name=nombres[1] if len(nombres) > 1 else '')
            from django.contrib.auth.models import Group
            u.groups.set([Group.objects.get_or_create(name=GRUPOS[c])[0] for c in modulos])  # en la principal
            UsuarioCorporativo.objects.create(usuario=u, todas=bool(request.POST.get('todas')),
                                              empresas=empresas_elegidas,
                                              puede_crear=bool(request.POST.get('puede_crear')),
                                              modulos=None if admin else modulos)
        finally:
            reg.restaurar(token)
        messages.success(request, f'Usuario corporativo {u.username} creado: ingresa en la empresa principal y '
                                  f'pasa a sus empresas desde el menú de usuario. Sus módulos en cada empresa se '
                                  f'ajustan allí, en Ajustes › Usuarios.')
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
    for t in tarjetas:  # creación cortada por un reinicio del servidor: se puede reintentar
        t['colgada'] = grupo.colgada(t['registro'])
    from . import estado_servicio as es
    from django.conf import settings
    from django.utils import timezone

    from .cronograma_sunat import periodo_anterior
    activas = [t for t in tarjetas if t.get('listo')]
    return render(request, 'core/grupo_panel.html', {
        'tarjetas': tarjetas, 'principal': principal, 'puede_crear': grupo.puede_crear(request),
        'aliases': list(reg.empresas()), 'version': settings.ERP_VERSION,
        'empresas_nombres': [(a, reg.razon_social(a)) for a in reg.empresas() if a != 'default'],
        'modulos_elegibles': _modulos_elegibles(), 'disp_30': es.disponibilidad(30),
        'activas': activas, 'periodo_masivo': periodo_anterior(timezone.localdate()),
        'copiables': grupo.COPIABLES, 'copia_defecto': grupo.COPIA_POR_DEFECTO, **_plan_para_panel(),
        'creando': any(t['registro'] and t['registro'].estado == 'CREANDO' for t in tarjetas)})


def _modulos_elegibles():
    """[(clave, nombre)] de los módulos del plan que se pueden dar a un usuario corporativo nuevo."""
    from .modulos import GRUPOS, POR_CLAVE
    from .plataforma import modulos_permitidos
    en_plan = modulos_permitidos()
    return [(c, POR_CLAVE[c]['nombre']) for c in GRUPOS if c in en_plan]


def _plan_para_panel():
    """REQ-03: el plan del grupo (solo lectura para el cliente; lo cambia la plataforma Ceiba)."""
    from .modulos import MODULOS
    from .plataforma import FIJOS, modulos_permitidos, plan_actual
    plan, permitidos = plan_actual(), modulos_permitidos()
    return {'plan_grupo': plan.nombre if plan else 'Completo', 'plan_todos': plan is None or plan.todos,
            'plan_modulos': [m['nombre'] for m in MODULOS if m['clave'] in permitidos and m['clave'] not in FIJOS],
            'plan_fuera': [m['nombre'] for m in MODULOS if m['clave'] not in permitidos]}


def _proceso_masivo(request):
    """Centralizar, cerrar el periodo o descargar los PLE de varias empresas a la vez (las que el usuario puede
    abrir). Cerrar exige ser administrador en la empresa principal."""
    from django.http import HttpResponse

    from . import estudio
    principal = grupo.corporativo(request)
    accion = request.POST.get('accion')
    periodo = (request.POST.get('periodo') or '').replace('-', '')[:6]
    permitidas = dict(grupo.empresas_de(request))
    elegidas = [a for a in request.POST.getlist('empresas') if a in permitidas]
    if not (len(periodo) == 6 and periodo.isdigit()) or not elegidas:
        messages.error(request, 'Elija el periodo y al menos una empresa.')
        return redirect('grupo_panel')
    if accion == 'cerrar' and not principal.is_superuser:
        messages.error(request, 'Solo un administrador de la empresa principal cierra periodos de varias empresas.')
        return redirect('grupo_panel')
    if accion == 'ple':
        datos, _ = estudio.ple_zip(elegidas, periodo)
        resp = HttpResponse(datos, content_type='application/zip')
        resp['Content-Disposition'] = f'attachment; filename="PLE_{periodo}_{len(elegidas)}_empresas.zip"'
        return resp
    for alias, ok, texto in estudio.ejecutar(elegidas, accion, periodo):
        (messages.success if ok else messages.error)(request, f'{permitidas.get(alias, alias)}: {texto}.')
    return redirect('grupo_panel')


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
