"""Pantallas de escritorio del operador logístico: solicitudes, guías de los clientes, tablero por destino,
consolidados, bitácora, costo por guía y configuración (clientes autorizados, maestro de estados)."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Tercero
from core.permisos import puede

from . import servicios as srv
from .forms import (ClienteLogisticoForm, ConfigForm, EmitirForm, EstadoForm, SolicitudForm, TransportistaForm)
from .xml_guias import ErrorXML
from .models import (BitacoraEstado, ClienteLogistico, ConfigOperador, Consolidado, EstadoLogistico, GuiaCliente,
                     SolicitudRecojo)


def _error(request, exc):
    messages.error(request, str(exc))


@login_required
def tablero(request):
    EstadoLogistico.cargar_base()
    conteo = dict(GuiaCliente.objects.values_list('estado').annotate(n=Count('id')))
    estados = [(e, conteo.get(e.pk, 0)) for e in EstadoLogistico.objects.filter(activo=True)]
    hoy = timezone.localdate()
    return render(request, 'operador_logistico/tablero.html', {
        'estados': estados,
        'solicitudes': SolicitudRecojo.objects.filter(estado='ASIGNADO').select_related('cliente', 'operador')[:20],
        'grupos': Consolidado.objects.filter(estado__in=('ARMANDO', 'CONFIRMADO')).annotate(n=Count('guias'))[:20],
        'en_trayecto': Consolidado.objects.filter(estado='EN_TRAYECTO').select_related('guia', 'transportista')[:20],
        'entregadas_hoy': GuiaCliente.objects.filter(entregado_en__date=hoy).count(),
        'pendientes_datos': GuiaCliente.objects.filter(datos_completos=False).exclude(estado__codigo='ENTREGADO').count(),
    })


# ---------------------------------------------------------------- solicitudes
@login_required
def solicitudes(request):
    qs = SolicitudRecojo.objects.select_related('cliente', 'operador').annotate(n_guias=Count('guias'))
    estado = request.GET.get('estado', 'ASIGNADO')
    if estado in dict(SolicitudRecojo.ESTADOS):
        qs = qs.filter(estado=estado)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(cliente__nombre__icontains=q) | Q(remitente__icontains=q))
    return render(request, 'operador_logistico/solicitudes.html', {
        'page_obj': Paginator(qs.order_by('-creado', '-id'), 50).get_page(request.GET.get('page')), 'estado': estado, 'q': q,
        'estados': SolicitudRecojo.ESTADOS})


@login_required
def solicitud_nueva(request):
    form = SolicitudForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        sol = srv.crear_solicitud(d['cliente'], 'MANUAL', '', '', d.get('mensaje') or '', request.user, unir=False,
                                  operador=d.get('operador'), almacen=d.get('almacen'), bultos=d.get('bultos'),
                                  contacto=d.get('contacto'), fecha=d.get('fecha_recojo'))
        messages.success(request, f'Solicitud {sol.numero} registrada.')
        return redirect('operador_logistico:solicitud', sol.pk)
    return render(request, 'operador_logistico/solicitud_nueva.html', {'form': form})


@login_required
def solicitud(request, pk):
    sol = get_object_or_404(SolicitudRecojo.objects.select_related('cliente', 'operador', 'atendida_por'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'asignar':
                from django.contrib.auth.models import User
                sol.operador = User.objects.filter(pk=request.POST.get('operador') or 0, is_active=True).first()
                sol.save(update_fields=['operador'])
                messages.success(request, 'Operador asignado.' if sol.operador else 'Operador quitado.')
            elif accion == 'xml':
                archivo = request.FILES.get('xml')
                if archivo is None:
                    raise srv.ErrorOperador('Adjunte el XML de la guía.')
                if archivo.size > 5 * 1024 * 1024:
                    raise srv.ErrorOperador('El XML supera los 5 MB.')
                guia, nueva = srv.registrar_guia_xml(archivo.read(), sol.cliente, sol, request.user)
                messages.success(request, f'Guía {guia.numero_completo} {"cargada" if nueva else "ya estaba"}.')
            elif accion == 'anular':
                if sol.estado != 'ASIGNADO':
                    raise srv.ErrorOperador('Solo se anula una solicitud que aún no se atendió.')
                srv._estado_solicitud(sol, 'ANULADA', request.user, 'PANTALLA',
                                      request.POST.get('motivo', '').strip()[:200] or 'Anulada')
                sol.save(update_fields=['estado'])
                messages.success(request, f'Solicitud {sol.numero} anulada.')
        except (srv.ErrorOperador, ErrorXML) as exc:
            _error(request, exc)
        return redirect('operador_logistico:solicitud', pk)
    from django.contrib.auth.models import User
    return render(request, 'operador_logistico/solicitud.html', {
        'sol': sol, 'guias': sol.guias.select_related('estado', 'consolidado'),
        'bitacora': sol.bitacora.select_related('usuario'),
        'operadores': User.objects.filter(is_active=True).order_by('username'),
        'planificar': puede(request.user, 'operador_logistico.planificar')})


# ---------------------------------------------------------------- guías de los clientes
@login_required
def guias(request):
    qs = GuiaCliente.objects.select_related('estado', 'cliente', 'consolidado', 'ultimo_tramo')
    estado = request.GET.get('estado', '')
    if estado:
        qs = qs.filter(estado__codigo=estado)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(serie__icontains=q) | Q(cliente__nombre__icontains=q) |
                       Q(destinatario_nombre__icontains=q) | Q(emisor_ruc__icontains=q) |
                       Q(departamento__icontains=q) | Q(provincia__icontains=q))
    if request.GET.get('pendientes'):
        qs = qs.filter(datos_completos=False)
    return render(request, 'operador_logistico/guias.html', {
        'page_obj': Paginator(qs.order_by('-creado', '-id'), 50).get_page(request.GET.get('page')), 'estado': estado, 'q': q,
        'estados': EstadoLogistico.objects.all()})


@login_required
def guia(request, pk):
    g = get_object_or_404(GuiaCliente.objects.select_related('estado', 'cliente', 'solicitud', 'consolidado__guia',
                                                             'ultimo_tramo'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'estado':  # cambio manual (ej. «Actualizar»): queda en la bitácora
                nuevo = get_object_or_404(EstadoLogistico, pk=request.POST.get('estado') or 0, activo=True)
                srv.cambiar_estado(g, nuevo.codigo, request.user, 'PANTALLA',
                                   request.POST.get('detalle', '').strip()[:200] or 'Cambio manual')
                messages.success(request, f'Estado: {nuevo.nombre}.')
            elif accion == 'xml':
                archivo = request.FILES.get('xml')
                if archivo is None:
                    raise srv.ErrorOperador('Adjunte el XML de la guía.')
                nueva, _ = srv.registrar_guia_xml(archivo.read(), g.cliente, g.solicitud, request.user)
                if nueva.pk != g.pk:
                    raise srv.ErrorOperador(f'El XML es de otra guía ({nueva.numero_completo}): se cargó aparte.')
                messages.success(request, 'Datos de la guía completados con su XML.')
            elif accion == 'mover':
                grupo = Consolidado.objects.filter(pk=request.POST.get('grupo') or 0).first()
                srv.mover_guia(g, grupo, request.user)
                messages.success(request, 'Guía movida.')
        except (srv.ErrorOperador, ErrorXML) as exc:
            _error(request, exc)
        return redirect('operador_logistico:guia', pk)
    return render(request, 'operador_logistico/guia.html', {
        'g': g, 'bitacora': g.bitacora.select_related('usuario', 'estado'),
        'estados': EstadoLogistico.objects.filter(activo=True),
        'planificar': puede(request.user, 'operador_logistico.planificar')})


# ---------------------------------------------------------------- planificación
@login_required
def planificacion(request):
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'mover':
                g = get_object_or_404(GuiaCliente, pk=request.POST.get('guia') or 0)
                destino = request.POST.get('grupo', '')
                grupo = None if destino == 'nuevo' else get_object_or_404(Consolidado, pk=destino or 0)
                grupo = srv.mover_guia(g, grupo, request.user)
                messages.success(request, f'Guía {g.numero_completo} movida a {grupo.numero}.')
            elif accion == 'confirmar':
                grupo = srv.confirmar_grupo(get_object_or_404(Consolidado, pk=request.POST.get('grupo') or 0),
                                            request.user)
                messages.success(request, f'Grupo {grupo.numero} confirmado: ya puede emitir su guía consolidada.')
                return redirect('operador_logistico:consolidado', grupo.pk)
            elif accion == 'custodia':
                n = 0
                for g in GuiaCliente.objects.filter(pk__in=request.POST.getlist('guias'), estado__codigo='CONFIRMADO'):
                    srv.cambiar_estado(g, 'EN_CUSTODIA', request.user, 'PANTALLA', 'Ingreso al almacén (logística)')
                    n += 1
                messages.success(request, f'{n} guía(s) en custodia.')
        except srv.ErrorOperador as exc:
            _error(request, exc)
        return redirect('operador_logistico:planificacion')
    grupos, sin_destino = srv.tablero()
    return render(request, 'operador_logistico/planificacion.html', {
        'grupos': grupos, 'sin_destino': sin_destino, 'abiertos': [g for g, _ in grupos],
        'planificar': puede(request.user, 'operador_logistico.planificar')})


@login_required
def consolidados(request):
    qs = Consolidado.objects.select_related('guia', 'transportista').annotate(n=Count('guias'))
    estado = request.GET.get('estado', '')
    if estado in dict(Consolidado.ESTADOS):
        qs = qs.filter(estado=estado)
    else:
        qs = qs.exclude(estado='ANULADO')
    return render(request, 'operador_logistico/consolidados.html', {
        'page_obj': Paginator(qs.order_by('-creado', '-id'), 50).get_page(request.GET.get('page')), 'estado': estado,
        'estados': Consolidado.ESTADOS})


@login_required
def consolidado(request, pk):
    c = get_object_or_404(Consolidado.objects.select_related('guia', 'transportista', 'agencia_destino',
                                                             'hoja_reparto'), pk=pk)
    emitir = EmitirForm(request.POST if request.POST.get('accion') == 'emitir' else None,
                        initial={'fecha': timezone.localdate(), 'transportista': c.transportista_id})
    transporte = TransportistaForm(request.POST if request.POST.get('accion') == 'transportista' else None,
                                   request.FILES or None,
                                   initial={'transportista': c.transportista_id, 'tipo': c.doc_transportista_tipo or '31',
                                            'numero': c.doc_transportista_numero, 'fecha': c.doc_transportista_fecha,
                                            'peso': c.doc_transportista_peso, 'bultos': c.doc_transportista_bultos})
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'confirmar':
                srv.confirmar_grupo(c, request.user)
                messages.success(request, 'Grupo confirmado.')
            elif accion == 'emitir':
                if not emitir.is_valid():
                    raise srv.ErrorOperador('Revise los datos de la guía consolidada.')
                d = emitir.cleaned_data
                gr = srv.emitir_consolidada(c, request.user, d['agencia'], d['llegada_ubigeo'], d['llegada_direccion'],
                                            d['transportista'], d['fecha'])
                messages.success(request, f'Guía consolidada {gr.numero_completo} emitida con {c.guias.count()} '
                                          f'guía(s) relacionadas. Las guías están En trayecto.')
            elif accion == 'transportista':
                if not transporte.is_valid():
                    raise srv.ErrorOperador('Revise los datos del transportista.')
                d = transporte.cleaned_data
                srv.registrar_transportista(c, request.user, d['transportista'], d['tipo'], d['numero'], d['fecha'],
                                            d['peso'], d['bultos'], d['qr'], d['archivo'])
                messages.success(request, 'Documento del transportista ligado a la guía consolidada.')
            elif accion == 'hoja':
                hoja = srv.crear_hoja_reparto(c, request.user)
                messages.success(request, f'Hoja de reparto {hoja.numero} creada con las guías del último tramo.')
                return redirect('distribucion:hoja', hoja.pk)
            return redirect('operador_logistico:consolidado', pk)
        except srv.ErrorOperador as exc:
            _error(request, exc)
            if accion not in ('emitir', 'transportista'):
                return redirect('operador_logistico:consolidado', pk)
    total, costos = srv.costos(c)
    from logistica.models import GuiaRemision
    return render(request, 'operador_logistico/consolidado.html', {
        'c': c, 'guias': c.guias.select_related('estado', 'cliente', 'ultimo_tramo'), 'emitir': emitir,
        'transporte': transporte, 'total_costo': total, 'costos': dict((g.pk, v) for g, v in costos),
        'ultimos': GuiaRemision.objects.filter(guias_cliente__consolidado=c).distinct().prefetch_related('relacionados'),
        'planificar': puede(request.user, 'operador_logistico.planificar')})


@login_required
def hoja_despacho(request, pk):
    """Hoja que viaja con el despacho: su QR lo escanea el operador de destino."""
    c = get_object_or_404(Consolidado.objects.select_related('guia', 'transportista'), pk=pk)
    from core.models import Empresa
    return render(request, 'operador_logistico/hoja_despacho.html', {
        'c': c, 'guias': c.guias.select_related('cliente'), 'empresa': Empresa.actual()})


# ---------------------------------------------------------------- reportes
@login_required
def bitacora(request):
    qs = BitacoraEstado.objects.select_related('guia', 'solicitud', 'usuario', 'estado')
    hoy = timezone.localdate()
    try:
        from datetime import date
        desde = date.fromisoformat(request.GET.get('desde', ''))
    except ValueError:
        desde = hoy - timedelta(days=7)
    qs = qs.filter(fecha__date__gte=desde)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(guia__numero__icontains=q) | Q(solicitud__numero__icontains=q) | Q(detalle__icontains=q) |
                       Q(usuario__username__icontains=q))
    return render(request, 'operador_logistico/bitacora.html', {
        'page_obj': Paginator(qs.order_by('-fecha', '-id'), 100).get_page(request.GET.get('page')), 'desde': desde,
        'q': q})


@login_required
def recibido_ceibago(request):
    """Lo que Ceiba GO mandó a su cola de sincronización (se guarda tal cual; aún no genera asientos)."""
    from .models import RegistroCeibaGO
    qs = RegistroCeibaGO.objects.select_related('usuario')
    tipo = request.GET.get('tipo', '')
    if tipo:
        qs = qs.filter(tipo=tipo)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(pedido__icontains=q)
    return render(request, 'operador_logistico/recibido_ceibago.html', {
        'page_obj': Paginator(qs, 100).get_page(request.GET.get('page')), 'tipo': tipo, 'q': q,
        'tipos': RegistroCeibaGO.TIPOS})


@login_required
def costos(request):
    filas = []
    for c in Consolidado.objects.filter(guia__isnull=False).select_related('guia').order_by('-creado')[:100]:
        total, por_guia = srv.costos(c)
        filas.append((c, total, por_guia))
    return render(request, 'operador_logistico/costos.html', {'filas': filas})


# ---------------------------------------------------------------- configuración
@login_required
def clientes(request):
    qs = Tercero.objects.filter(activo=True, tipo__in=('CLIENTE', 'AMBOS'))
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(numero_doc__icontains=q))
    elif not request.GET.get('todos'):  # por defecto, los que ya tienen ficha logística (con o sin contrato)
        qs = qs.filter(servicio_logistico__isnull=False)
    qs = qs.select_related('servicio_logistico').annotate(n_autorizados=Count('servicio_logistico__autorizados'))
    return render(request, 'operador_logistico/clientes.html', {
        'page_obj': Paginator(qs.order_by('nombre'), 50).get_page(request.GET.get('page')), 'q': q})


@login_required
def cliente(request, pk):
    t = get_object_or_404(Tercero, pk=pk)
    perfil = ClienteLogistico.objects.filter(tercero=t).first() or ClienteLogistico(tercero=t)
    form = ClienteLogisticoForm(request.POST or None, instance=perfil)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Servicio logístico de {t.nombre} guardado.')
        return redirect('operador_logistico:cliente', pk)
    return render(request, 'operador_logistico/cliente.html', {
        'form': form, 't': t, 'perfil': perfil,
        'configurar': puede(request.user, 'operador_logistico.configurar')})


@login_required
def estados(request):
    EstadoLogistico.cargar_base()
    if request.method == 'POST':
        e = get_object_or_404(EstadoLogistico, pk=request.POST.get('estado') or 0)
        form = EstadoForm(request.POST, instance=e, prefix=f'e{e.pk}')
        if form.is_valid():
            form.save()
            if e.inicial:  # un solo estado inicial
                EstadoLogistico.objects.exclude(pk=e.pk).update(inicial=False)
            messages.success(request, f'Estado «{e.nombre}» guardado.')
        else:
            messages.error(request, 'Revise los datos del estado.')
        return redirect('operador_logistico:estados')
    return render(request, 'operador_logistico/estados.html', {
        'filas': [(e, EstadoForm(instance=e, prefix=f'e{e.pk}')) for e in EstadoLogistico.objects.all()],
        'configurar': puede(request.user, 'operador_logistico.configurar')})


@login_required
def configuracion(request):
    form = ConfigForm(request.POST or None, instance=ConfigOperador.actual())
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Configuración guardada.')
        return redirect('operador_logistico:configuracion')
    return render(request, 'operador_logistico/configuracion.html', {
        'form': form, 'configurar': puede(request.user, 'operador_logistico.configurar')})


# ---------------------------------------------------------------- rutas de recojo (v1.117.15)
def _fecha_get(valor):
    from datetime import date
    try:
        return date.fromisoformat(valor or '')
    except ValueError:
        return timezone.localdate()


@login_required
def rutas(request):
    """Ruta de recojo del día: sugerencia por zona y cercanía, ajuste a mano, operador y placa."""
    from django.contrib.auth.models import User
    from . import rutas as rr
    from .models import RutaRecojo
    fecha = _fecha_get(request.POST.get('fecha') or request.GET.get('fecha'))
    if request.method == 'POST':
        P, accion = request.POST, request.POST.get('accion', '')
        ruta = RutaRecojo.objects.filter(pk=P.get('ruta') or 0).first()
        sol = SolicitudRecojo.objects.filter(pk=P.get('solicitud') or 0).first()
        try:
            if accion in ('sugerir', 'rehacer'):
                creadas = rr.sugerir(fecha, request.user, rehacer=accion == 'rehacer')
                messages.success(request, f'{len(creadas)} ruta(s) sugerida(s).' if creadas else
                                 'No hay solicitudes por recoger sin ruta para ese día.')
            elif accion == 'nueva':
                ruta = rr.nueva(fecha, request.user, (P.get('zona') or 'Manual').strip()[:60])
                messages.success(request, f'Ruta {ruta.numero} creada: agréguele paradas.')
            elif accion == 'agregar' and sol is not None:
                destino = RutaRecojo.objects.filter(pk=P.get('destino') or 0).first()
                if destino is None:
                    destino = rr.nueva(fecha, request.user)
                rr.agregar(sol, destino)
            elif accion == 'quitar' and sol is not None:
                rr.quitar(sol)
            elif accion in ('subir', 'bajar') and sol is not None:
                rr.mover(sol, -1 if accion == 'subir' else 1)
            elif ruta is None:
                raise rr.ErrorRuta('Elija la ruta.')
            elif accion == 'ordenar':
                rr.ordenar_por_cercania(ruta)
                messages.success(request, f'{ruta.numero}: paradas ordenadas por cercanía.')
            elif accion == 'asignar':
                operador = User.objects.filter(pk=P.get('operador') or 0, is_active=True).first()
                rr.asignar(ruta, operador, P.get('placa', ''))
                messages.success(request, f'{ruta.numero}: asignada a '
                                          f'{(operador.get_full_name() or operador.username) if operador else "nadie"}.')
            elif accion == 'iniciar':
                rr.iniciar(ruta)
                messages.success(request, f'{ruta.numero} en curso: el operador la ve en el celular.')
            elif accion == 'eliminar':
                numero = ruta.numero
                rr.eliminar(ruta)
                messages.success(request, f'Ruta {numero} deshecha: sus solicitudes quedan sin ruta.')
        except rr.ErrorRuta as exc:
            _error(request, exc)
        return redirect(f"{request.path}?fecha={fecha.isoformat()}")
    perfiles = {p.tercero_id: p for p in ClienteLogistico.objects.all()}
    lista = []
    for r in RutaRecojo.objects.filter(fecha=fecha).select_related('operador'):
        paradas = [rr.detalle(s, perfiles.get(s.cliente_id)) for s in r.paradas_en_orden()]
        lista.append({'r': r, 'paradas': paradas, 'kg': sum((d['kg'] for d in paradas), 0),
                      'bultos': sum(d['bultos'] for d in paradas),
                      'hechas': sum(1 for d in paradas if d['sol'].estado != 'ASIGNADO'),
                      'google': rr.enlace_google(r, paradas), 'origen': rr.origen(r)})
    sin_ruta = [rr.detalle(s, perfiles.get(s.cliente_id)) for s in rr.pendientes(fecha)]
    operadores = rr.operadores()
    return render(request, 'operador_logistico/rutas.html', {
        'fecha': fecha, 'rutas': lista, 'sin_ruta': sin_ruta, 'operadores': operadores, 'cfg': ConfigOperador.actual(),
        'base': rr.base(), 'planificar': puede(request.user, 'operador_logistico.planificar'),
        'con_salida': len(rr.operadores_libres(fecha)),
        'abiertas': [x['r'] for x in lista if x['r'].estado != 'CERRADA']})


@login_required
def ruta_hoja(request, pk):
    """Hoja de ruta impresa: paradas en orden con contacto, bultos y espacio para la firma."""
    from core.models import Empresa
    from . import rutas as rr
    from .models import RutaRecojo
    r = get_object_or_404(RutaRecojo.objects.select_related('operador'), pk=pk)
    perfiles = {p.tercero_id: p for p in ClienteLogistico.objects.all()}
    paradas = [rr.detalle(s, perfiles.get(s.cliente_id)) for s in r.paradas_en_orden()]
    return render(request, 'operador_logistico/ruta_hoja.html', {
        'r': r, 'paradas': paradas, 'empresa': Empresa.actual(), 'cfg': ConfigOperador.actual(),
        'kg': sum((d['kg'] for d in paradas), 0), 'bultos': sum(d['bultos'] for d in paradas),
        'google': rr.enlace_google(r, paradas), 'origen': rr.origen(r)})


@login_required
def operadores_recojo(request):
    """v1.117.16: de dónde sale cada operador de recojo (su casa o punto de partida); la ruta se programa desde ahí."""
    from . import rutas as rr
    from .forms import OperadorRecojoForm
    from .models import OperadorRecojo
    usuarios = rr.operadores()
    fichas = {f.usuario_id: f for f in OperadorRecojo.objects.filter(usuario__in=usuarios)}

    def ficha_de(u):
        f = fichas.get(u.pk)
        if f is None:  # nueva: toma la dirección del trabajador de Planillas, si el usuario es uno
            trabajador = getattr(u, 'trabajador_planilla', None)
            f = OperadorRecojo(usuario=u, direccion=getattr(trabajador, 'direccion', '') or '')
        return f

    filas = []
    for u in usuarios:
        enviado = request.method == 'POST' and request.POST.get('usuario') == str(u.pk)
        form = OperadorRecojoForm(request.POST if enviado else None, instance=ficha_de(u), prefix=f'op{u.pk}')
        if enviado:
            if form.is_valid():
                form.save()
                messages.success(request, f'{u.get_full_name() or u.username}: punto de partida guardado.')
                return redirect('operador_logistico:operadores_recojo')
            messages.error(request, 'Revise los datos del operador.')
        f = form.instance
        filas.append({'u': u, 'form': form, 'salida': rr.salida(f)})
    return render(request, 'operador_logistico/operadores_recojo.html', {
        'filas': filas, 'planificar': puede(request.user, 'operador_logistico.planificar')})
