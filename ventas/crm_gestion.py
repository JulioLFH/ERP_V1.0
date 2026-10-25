"""CRM (v1.81): equipos y metas, campañas y segmentos, contactos, postventa y encuestas, contratos con renovación,
datos personales (Ley 29733), reasignación de cartera, vista para operaciones y configuración."""
import json
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.forms import BootstrapMixin, remoto
from core.models import Tercero
from core.utils import fmt_fecha

from . import crm_servicios as srv
from .crm import _usuarios
from .models import (Campana, CasoPostventa, ConfigCRM, ConsentimientoDatos, ContactoCliente, ContratoServicio,
                     EncuestaSatisfaccion, EquipoVenta, MetaVenta, Oportunidad, Reasignacion)

D0 = Decimal('0')


def _tabla(request, titulo, columnas, filas, nombre_excel=None, **extra):
    """Lista genérica del CRM (con Excel de lo que se ve)."""
    from .crm_tabla import responder
    return responder(request, titulo, [{'columnas': columnas, 'filas': filas}], **extra)


def _editar(request, instancia, form_class, titulo, volver, ayuda='', al_guardar=None):
    form = form_class(request.POST or None, instance=instancia)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if al_guardar:
            al_guardar(obj)
        obj.save()
        form.save_m2m()
        messages.success(request, f'{obj._meta.verbose_name.capitalize()} guardado.')
        return redirect(volver)
    return render(request, 'core/form.html', {'form': form, 'titulo': titulo, 'ayuda': ayuda})


def _usuario_txt(u):
    return (u.get_full_name() or u.username) if u else '—'


# ---------------------------------------------------------------- equipos y metas (CRM-08, CRM-15)
class EquipoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = EquipoVenta
        fields = ['nombre', 'jefe', 'miembros', 'activo']
        widgets = {'miembros': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['jefe'].queryset = self.fields['miembros'].queryset = _usuarios()


@login_required
def equipos(request):
    filas = [[{'texto': e.nombre, 'url': reverse('crm:equipo', args=[e.pk])}, _usuario_txt(e.jefe),
              ', '.join(_usuario_txt(m) for m in e.miembros.all()), 'Sí' if e.activo else 'No']
             for e in EquipoVenta.objects.select_related('jefe').prefetch_related('miembros')]
    return _tabla(request, 'Equipos de ventas', ['Equipo', 'Jefe', 'Miembros', 'Activo'], filas, 'Equipos',
                  botones=[('Nuevo equipo', reverse('crm:equipo_nuevo'), 'bi-plus-lg')],
                  ayuda='El jefe ve y reasigna las oportunidades de su equipo; el vendedor ve las suyas; con el '
                        'permiso «ver todas» se ve todo el CRM.')


@login_required
def equipo(request, pk=None):
    e = get_object_or_404(EquipoVenta, pk=pk) if pk else EquipoVenta()
    return _editar(request, e, EquipoForm, 'Equipo de ventas', 'crm:equipos')


class MetaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = MetaVenta
        fields = ['periodo', 'vendedor', 'equipo', 'monto']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vendedor'].queryset = _usuarios()
        self.fields['periodo'].initial = timezone.localdate().strftime('%Y%m')

    def clean(self):
        d = super().clean()
        if bool(d.get('vendedor')) == bool(d.get('equipo')):
            raise forms.ValidationError('Indique el vendedor o el equipo (uno de los dos).')
        if not (d.get('periodo') or '').isdigit() or len(d['periodo']) != 6:
            self.add_error('periodo', 'Mes en formato AAAAMM (ej. 202611).')
        return d


@login_required
def meta(request, pk=None):
    m = get_object_or_404(MetaVenta, pk=pk) if pk else MetaVenta()
    return _editar(request, m, MetaForm, 'Meta de ventas', 'crm:metas')


# ---------------------------------------------------------------- campañas y segmentos (CRM-18)
class CampanaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Campana
        fields = ['nombre', 'canal', 'inicio', 'fin', 'costo', 'notas', 'activa']


@login_required
def campanas(request):
    filas = []
    for c in Campana.objects.all():
        ops = list(c.oportunidades.all())
        ganadas = [o for o in ops if o.etapa == 'GANADA']
        monto = sum((o.monto for o in ganadas), D0)
        filas.append([{'texto': c.nombre, 'url': reverse('crm:campana', args=[c.pk])}, c.get_canal_display(),
                      fmt_fecha(c.inicio), c.costo, len(ops), len(ganadas), monto,
                      (c.costo / len(ops)).quantize(Decimal('0.01')) if ops else '—',
                      (c.costo / len(ganadas)).quantize(Decimal('0.01')) if ganadas else '—',
                      f'{(monto / c.costo):.1f}x' if c.costo else '—'])
    return _tabla(request, 'Campañas', ['Campaña', 'Canal', 'Inicio', 'Costo S/', 'Oportunidades', 'Ganadas',
                                        'Ganado S/', 'Costo por oportunidad', 'Costo por venta ganada',
                                        'Retorno'], filas, 'Campanas',
                  botones=[('Nueva campaña', reverse('crm:campana_nueva'), 'bi-plus-lg'),
                           ('Segmentos de clientes', reverse('crm:segmentos'), 'bi-people')])


@login_required
def campana(request, pk=None):
    c = get_object_or_404(Campana, pk=pk) if pk else Campana()
    return _editar(request, c, CampanaForm, 'Campaña', 'crm:campanas',
                   'Cada oportunidad elige su campaña: así se mide el costo por oportunidad y por venta ganada.')


@login_required
def segmentos(request):
    """Clientes y contactos por segmento para comunicaciones: solo los que dieron su consentimiento y no se dieron de
    baja (Ley 29733)."""
    from django.db.models import Max
    from .models import Venta
    zona, rol = (request.GET.get('zona') or '').strip(), request.GET.get('rol') or ''
    dias = request.GET.get('sin_compra') or ''
    con_permiso = ConsentimientoDatos.objects.filter(estado='OTORGADO')
    contactos = ContactoCliente.objects.filter(activo=True, consentimientos__in=con_permiso).distinct().select_related(
        'tercero')
    if zona:
        contactos = contactos.filter(tercero__zona__icontains=zona)
    if rol:
        contactos = contactos.filter(rol=rol)
    if dias.isdigit():
        ultima = dict(Venta.objects.filter(estado='REGISTRADO').values('tercero').annotate(
            u=Max('fecha_emision')).values_list('tercero', 'u'))
        limite = timezone.localdate() - timedelta(days=int(dias))
        contactos = [c for c in contactos if ultima.get(c.tercero_id) and ultima[c.tercero_id] < limite]
    filas = [[c.tercero.nombre, c.nombre, c.get_rol_display(), c.cargo, c.email, c.telefono, c.tercero.zona]
             for c in contactos]
    return _tabla(request, 'Segmentos de clientes (con consentimiento)',
                  ['Cliente', 'Contacto', 'Rol', 'Cargo', 'Correo', 'Teléfono', 'Zona'], filas, 'Segmento',
                  selects=[('rol', 'Rol', [('', 'Todos')] + ContactoCliente.ROLES, rol)],
                  textos=[('zona', 'Zona', zona), ('sin_compra', 'Sin compras hace (días)', dias)],
                  ayuda='Solo aparecen los contactos con consentimiento vigente: los que se dieron de baja no se '
                        'exportan.')


# ---------------------------------------------------------------- contactos (CRM-06)
@login_required
def contactos(request):
    q = (request.GET.get('q') or '').strip()
    qs = ContactoCliente.objects.filter(activo=True).select_related('tercero')
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(tercero__nombre__icontains=q) | Q(email__icontains=q))
    filas = [[{'texto': c.tercero.nombre, 'url': reverse('crm:cliente', args=[c.tercero_id])}, c.nombre,
              c.cargo, c.get_rol_display(), c.telefono, c.email, 'Sí' if c.principal else '']
             for c in qs[:500]]
    return _tabla(request, 'Contactos de clientes', ['Cliente', 'Contacto', 'Cargo', 'Rol', 'Teléfono', 'Correo',
                                                     'Principal'], filas, 'Contactos', buscar=True,
                  ayuda='Los contactos se agregan en la ficha 360 de cada cliente.')


# ---------------------------------------------------------------- postventa y encuestas (CRM-17)
class CasoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CasoPostventa
        fields = ['tercero', 'tipo', 'asunto', 'descripcion', 'oportunidad', 'obra', 'venta', 'responsable',
                  'fecha_limite', 'estado', 'solucion']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 3}), 'solucion': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['tercero'], 'clientes')
        remoto(self.fields['venta'], 'ventas', perezoso=True)
        self.fields['responsable'].queryset = _usuarios()
        self.fields['oportunidad'].queryset = Oportunidad.objects.filter(
            Q(etapa='GANADA') | Q(pk=self.instance.oportunidad_id or 0))

    def clean(self):
        d = super().clean()
        if d.get('estado') == 'CERRADO' and len((d.get('solucion') or '').strip()) < 5:
            self.add_error('solucion', 'Describa la solución para cerrar el caso.')
        return d


@login_required
def casos(request):
    estado = request.GET.get('estado', 'abiertos')
    qs = CasoPostventa.objects.select_related('tercero', 'responsable')
    if estado == 'abiertos':
        qs = qs.exclude(estado='CERRADO')
    filas = [[{'texto': c.numero, 'url': reverse('crm:caso', args=[c.pk])}, fmt_fecha(c.creado.date()),
              c.tercero.nombre, c.get_tipo_display(), c.asunto, _usuario_txt(c.responsable),
              {'texto': fmt_fecha(c.fecha_limite), 'clase': 'text-danger fw-semibold' if c.vencido else ''},
              c.get_estado_display()] for c in qs[:500]]
    return _tabla(request, 'Casos de postventa', ['N°', 'Fecha', 'Cliente', 'Tipo', 'Asunto', 'Responsable',
                                                  'Plazo', 'Estado'], filas, 'Postventa',
                  botones=[('Nuevo caso', reverse('crm:caso_nuevo'), 'bi-plus-lg'),
                           ('Encuestas', reverse('crm:encuestas'), 'bi-emoji-smile')],
                  selects=[('estado', 'Ver', [('abiertos', 'Abiertos'), ('todos', 'Todos')], estado)])


@login_required
def caso(request, pk=None):
    c = get_object_or_404(CasoPostventa, pk=pk) if pk else CasoPostventa(
        tercero_id=request.GET.get('tercero') or None, oportunidad_id=request.GET.get('oportunidad') or None,
        responsable=request.user, fecha_limite=timezone.localdate() + timedelta(days=7))

    def antes(obj):
        if not obj.pk:
            obj.creado_por = request.user
    return _editar(request, c, CasoForm, f'Caso de postventa {c.numero}' if c.pk else 'Nuevo caso de postventa',
                   'crm:casos', 'Al cerrarlo, registre la encuesta de satisfacción del cliente.', al_guardar=antes)


class EncuestaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = EncuestaSatisfaccion
        fields = ['tercero', 'motivo', 'obra', 'caso', 'fecha', 'satisfaccion', 'recomendaria', 'comentario']
        widgets = {'comentario': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['tercero'], 'clientes')

    def clean(self):
        d = super().clean()
        if d.get('satisfaccion') is not None and not 1 <= d['satisfaccion'] <= 5:
            self.add_error('satisfaccion', 'De 1 a 5.')
        if d.get('recomendaria') is not None and d['recomendaria'] > 10:
            self.add_error('recomendaria', 'De 0 a 10.')
        return d


@login_required
def encuestas(request):
    qs = list(EncuestaSatisfaccion.objects.select_related('tercero', 'obra')[:500])
    promotores = sum(1 for e in qs if e.recomendaria >= 9)
    detractores = sum(1 for e in qs if e.recomendaria <= 6)
    nps = round((promotores - detractores) * 100 / len(qs)) if qs else None
    filas = [[fmt_fecha(e.fecha), e.tercero.nombre, e.motivo, e.satisfaccion, e.recomendaria, e.comentario]
             for e in qs]
    return _tabla(request, 'Encuestas de satisfacción', ['Fecha', 'Cliente', 'Motivo', 'Satisfacción (1-5)',
                                                         'Recomendaría (0-10)', 'Comentario'], filas, 'Encuestas',
                  botones=[('Nueva encuesta', reverse('crm:encuesta_nueva'), 'bi-plus-lg')],
                  ayuda=(f'Índice de recomendación (NPS): {nps} · promedio de satisfacción '
                         f'{sum(e.satisfaccion for e in qs) / len(qs):.1f} de 5.') if qs else '')


@login_required
def encuesta(request):
    e = EncuestaSatisfaccion(tercero_id=request.GET.get('tercero') or None, caso_id=request.GET.get('caso') or None,
                             registrado_por=request.user)
    return _editar(request, e, EncuestaForm, 'Encuesta de satisfacción', 'crm:encuestas',
                   'Al cerrar la obra, la entrega de la unidad o un caso de postventa.')


# ---------------------------------------------------------------- contratos y renovaciones (CRM-12)
class ContratoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ContratoServicio
        fields = ['tercero', 'descripcion', 'inicio', 'fin', 'valor', 'aviso_dias', 'responsable', 'estado']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['tercero'], 'clientes')
        self.fields['responsable'].queryset = _usuarios()

    def clean(self):
        d = super().clean()
        if d.get('inicio') and d.get('fin') and d['fin'] < d['inicio']:
            self.add_error('fin', 'Termina antes de empezar.')
        return d


@login_required
def contratos(request):
    if request.method == 'POST' and (request.POST.get('renovar') or '').isdigit():
        c = get_object_or_404(ContratoServicio, pk=request.POST['renovar'])
        if c.oportunidad_renovacion_id is None:
            o = Oportunidad.objects.create(
                nombre=f'Renovación: {c.descripcion}'[:150], tercero=c.tercero, etapa='CALIFICADO',
                monto=c.valor or Decimal('1'), origen='RENOVACION', responsable=c.responsable or request.user,
                fecha_cierre=max(c.fin, timezone.localdate()))
            ContratoServicio.objects.filter(pk=c.pk).update(oportunidad_renovacion=o)
            messages.success(request, f'Oportunidad de renovación creada para {c}.')
            return redirect('crm:oportunidad', o.pk)
    ver = request.GET.get('ver', 'vigentes')
    qs = ContratoServicio.objects.select_related('tercero', 'responsable', 'oportunidad_renovacion')
    if ver == 'vigentes':
        qs = qs.filter(estado='VIGENTE')
    filas = []
    for c in qs:
        accion = ({'texto': 'Oportunidad', 'url': reverse('crm:oportunidad', args=[c.oportunidad_renovacion_id])}
                  if c.oportunidad_renovacion_id else
                  {'texto': 'Crear oportunidad de renovación', 'post': reverse('crm:contratos'), 'campo': 'renovar',
                   'valor': c.pk} if c.estado == 'VIGENTE' else '')
        filas.append([{'texto': c.descripcion, 'url': reverse('crm:contrato', args=[c.pk])}, c.tercero.nombre,
                      fmt_fecha(c.inicio), {'texto': fmt_fecha(c.fin),
                                            'clase': 'text-danger fw-semibold' if c.por_renovar else ''},
                      c.valor, _usuario_txt(c.responsable), c.get_estado_display(), accion])
    return _tabla(request, 'Contratos y renovaciones', ['Servicio', 'Cliente', 'Inicio', 'Termina', 'Valor S/',
                                                        'Responsable', 'Estado', ''], filas, 'Contratos',
                  botones=[('Nuevo contrato', reverse('crm:contrato_nuevo'), 'bi-plus-lg')],
                  selects=[('ver', 'Ver', [('vigentes', 'Vigentes'), ('todos', 'Todos')], ver)],
                  ayuda='En rojo: vencen dentro de su plazo de aviso (también sale en Inicio).')


@login_required
def contrato(request, pk=None):
    c = get_object_or_404(ContratoServicio, pk=pk) if pk else ContratoServicio(
        tercero_id=request.GET.get('tercero') or None, responsable=request.user)
    return _editar(request, c, ContratoForm, 'Contrato de servicio', 'crm:contratos')


# ---------------------------------------------------------------- datos personales (CRM-20)
def _datos_persona(c):
    """Todo lo que el CRM guarda de una persona (para entregárselo si lo pide)."""
    filtro = Q()
    for campo, valor in (('email__iexact', c.email), ('telefono', c.telefono)):
        if valor:
            filtro |= Q(**{campo: valor})
    ops = Oportunidad.objects.filter(filtro | Q(pk=c.oportunidad_id or 0)) if filtro or c.oportunidad_id else []
    contactos = ContactoCliente.objects.filter(filtro) if filtro else []
    return {
        'titular': c.titular, 'documento': c.documento, 'email': c.email, 'telefono': c.telefono,
        'consentimientos': [{'fecha': f'{x.fecha:%d/%m/%Y %H:%M}', 'canal': x.get_canal_display(),
                             'finalidad': x.finalidad, 'estado': x.get_estado_display()}
                            for x in ConsentimientoDatos.objects.filter(
                                Q(pk=c.pk) | (filtro if filtro else Q(pk=c.pk)))],
        'oportunidades': [{'nombre': o.nombre, 'prospecto': o.prospecto, 'contacto': o.contacto,
                           'telefono': o.telefono, 'email': o.email, 'creado': f'{o.creado:%d/%m/%Y}'} for o in ops],
        'contactos': [{'cliente': x.tercero.nombre, 'nombre': x.nombre, 'cargo': x.cargo, 'telefono': x.telefono,
                       'email': x.email} for x in contactos]}


@login_required
def datos_personales(request):
    if request.method == 'POST':
        c = get_object_or_404(ConsentimientoDatos, pk=request.POST.get('id') or 0)
        accion = request.POST.get('accion')
        if accion == 'revocar' and c.estado == 'OTORGADO':
            ConsentimientoDatos.objects.filter(pk=c.pk).update(estado='REVOCADO', fecha_baja=timezone.now())
            messages.success(request, f'{c.titular}: baja de comunicaciones registrada.')
        elif accion == 'exportar':
            datos = json.dumps(_datos_persona(c), ensure_ascii=False, indent=2)
            resp = HttpResponse(datos, content_type='application/json; charset=utf-8')
            resp['Content-Disposition'] = f'attachment; filename="datos_personales_{c.pk}.json"'
            return resp
        elif accion == 'suprimir':
            _suprimir(c)
            messages.success(request, f'Datos de {c.titular} suprimidos (anonimizados) en el CRM.')
        return redirect('crm:datos_personales')
    q = (request.GET.get('q') or '').strip()
    qs = ConsentimientoDatos.objects.all()
    if q:
        qs = qs.filter(Q(titular__icontains=q) | Q(email__icontains=q) | Q(telefono__icontains=q) |
                       Q(documento__icontains=q))
    url = reverse('crm:datos_personales')
    filas = [[fmt_fecha(c.fecha.date()), c.titular, c.email, c.telefono, c.get_canal_display(), c.finalidad,
              c.get_estado_display(),
              [{'texto': 'Baja', 'post': url, 'campo': 'id', 'valor': c.pk, 'extra': {'accion': 'revocar'}}
               if c.estado == 'OTORGADO' else '',
               {'texto': 'Exportar', 'post': url, 'campo': 'id', 'valor': c.pk, 'extra': {'accion': 'exportar'}},
               {'texto': 'Suprimir', 'post': url, 'campo': 'id', 'valor': c.pk, 'extra': {'accion': 'suprimir'},
                'confirmar': f'¿Suprimir los datos de {c.titular}? No se puede deshacer.'}
               if c.estado != 'SUPRIMIDO' else '']] for c in qs[:500]]
    return _tabla(request, 'Datos personales (Ley 29733)', ['Fecha', 'Titular', 'Correo', 'Teléfono', 'Canal',
                                                             'Finalidad', 'Estado', 'Acciones'], filas,
                  'Consentimientos', buscar=True,
                  ayuda='Consentimiento de cada prospecto o contacto. «Baja» deja de enviarle comunicaciones; '
                        '«Exportar» entrega sus datos; «Suprimir» los anonimiza en el CRM (los comprobantes '
                        'emitidos se conservan por obligación tributaria).')


def _suprimir(c):
    filtro = Q()
    for campo, valor in (('email__iexact', c.email), ('telefono', c.telefono)):
        if valor:
            filtro |= Q(**{campo: valor})
    if c.oportunidad_id:
        filtro |= Q(pk=c.oportunidad_id)
    if filtro:
        Oportunidad.objects.filter(filtro).filter(tercero__isnull=True).update(
            prospecto='(datos suprimidos)', contacto='', telefono='', email='')
        Oportunidad.objects.filter(filtro).exclude(tercero__isnull=True).update(contacto='', telefono='', email='')
    if c.contacto_id:
        ContactoCliente.objects.filter(pk=c.contacto_id).update(nombre='(datos suprimidos)', telefono='', email='',
                                                               notas='', activo=False)
    ConsentimientoDatos.objects.filter(pk=c.pk).update(
        estado='SUPRIMIDO', titular='(datos suprimidos)', documento='', email='', telefono='',
        fecha_baja=c.fecha_baja or timezone.now())


# ---------------------------------------------------------------- reasignación de cartera (CRM-15)
class ReasignarForm(BootstrapMixin, forms.Form):
    de = forms.ModelChoiceField(label='Cartera de', queryset=None)
    a = forms.ModelChoiceField(label='Pasar a', queryset=None)
    motivo = forms.CharField(max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        self.fields['de'].queryset = get_user_model().objects.order_by('username')
        self.fields['a'].queryset = _usuarios()


@login_required
def reasignaciones(request):
    form = ReasignarForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            n = srv.reasignar(d['de'], d['a'], request.user, d['motivo'])
            messages.success(request, f'Pasaron {n[0]} oportunidad(es), {n[1]} actividad(es) y {n[2]} cliente(s).')
            return redirect('crm:reasignaciones')
        except srv.ErrorCRM as exc:
            form.add_error('a', str(exc))
    filas = [[f'{r.fecha:%d/%m/%Y %H:%M}', _usuario_txt(r.de), _usuario_txt(r.a), r.oportunidades, r.actividades,
              r.clientes, r.motivo, _usuario_txt(r.usuario)]
             for r in Reasignacion.objects.select_related('de', 'a', 'usuario')[:300]]
    from .crm_tabla import responder
    return responder(request, 'Reasignación de cartera', [{
        'titulo': 'Historial', 'columnas': ['Fecha', 'De', 'A', 'Oportunidades', 'Actividades', 'Clientes', 'Motivo',
                                            'Por'], 'filas': filas}],
        form=form, form_titulo='Pasar la cartera de un vendedor',
        ayuda='Al desactivar un usuario con cartera abierta, Ajustes › Usuarios pide a quién pasarla.')


# ---------------------------------------------------------------- operaciones: lo que viene (CRM-21)
@login_required
def operaciones(request):
    """Oportunidades en negociación (y ganadas recientes) para que obras, producción y logística planifiquen."""
    hoy = timezone.localdate()
    qs = Oportunidad.objects.filter(Q(etapa='NEGOCIACION') | Q(etapa='GANADA', cerrada_en__gte=hoy - timedelta(
        days=30))).select_related('tercero', 'responsable').order_by('fecha_inicio', 'fecha_cierre')
    nombres = dict(srv.etapas())
    filas = [[nombres.get(o.etapa), o.nombre, o.cliente, o.monto, f'{o.prob}%', fmt_fecha(o.fecha_cierre),
              {'texto': fmt_fecha(o.fecha_inicio) or 'Sin fecha', 'clase': '' if o.fecha_inicio else 'text-muted'},
              _usuario_txt(o.responsable)] for o in qs]
    return _tabla(request, 'Lo que viene: oportunidades para operaciones', [
        'Etapa', 'Oportunidad', 'Cliente', 'Monto S/', 'Probabilidad', 'Cierre estimado', 'Inicio probable',
        'Vendedor'], filas, 'Oportunidades_operaciones',
        ayuda='En negociación y ganadas en los últimos 30 días, con su inicio probable: planifique personal, equipos '
              'y compras. Al ganarse una oportunidad se avisa por correo a los responsables de la Configuración del '
              'CRM (y en Inicio).')


# ---------------------------------------------------------------- configuración y formulario web (CRM-09)
class ConfigForm(BootstrapMixin, forms.ModelForm):
    nombres = forms.CharField(label='Nombres de las etapas (opcional)', required=False,
                              widget=forms.Textarea(attrs={'rows': 4}),
                              help_text='Una por línea: CÓDIGO = nombre. Códigos: PROSPECTO, CALIFICADO, PROPUESTA, '
                                        'NEGOCIACION, GANADA, PERDIDA')

    class Meta:
        model = ConfigCRM
        fields = ['web_activo', 'asignacion_web', 'responsable_web', 'equipo_web', 'avisar_ganadas',
                  'finalidad_datos', 'correo_activo', 'correo_servidor', 'correo_puerto', 'correo_usuario',
                  'correo_clave']
        widgets = {'avisar_ganadas': forms.CheckboxSelectMultiple,
                   'correo_clave': forms.PasswordInput(render_value=False)}

    def clean_correo_clave(self):  # vacía = la que ya estaba (no se muestra)
        return self.cleaned_data.get('correo_clave') or self.instance.correo_clave

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['responsable_web'].queryset = self.fields['avisar_ganadas'].queryset = _usuarios()
        self.fields['nombres'].initial = '\n'.join(f'{k} = {v}' for k, v in (self.instance.nombres_etapas or {}).items())

    def clean_nombres(self):
        validos = dict(Oportunidad.ETAPAS)
        salida = {}
        for linea in (self.cleaned_data.get('nombres') or '').splitlines():
            if not linea.strip():
                continue
            codigo, _, nombre = linea.partition('=')
            codigo = codigo.strip().upper()
            if codigo not in validos or not nombre.strip():
                raise forms.ValidationError(f'Línea no válida: «{linea.strip()}».')
            salida[codigo] = nombre.strip()[:40]
        return salida

    def save(self, commit=True):
        self.instance.nombres_etapas = self.cleaned_data.get('nombres') or {}
        return super().save(commit)


@login_required
def configuracion(request):
    cfg = ConfigCRM.actual()
    if request.method == 'POST' and request.POST.get('accion') == 'nueva_clave':
        ConfigCRM.objects.filter(pk=cfg.pk).update(token_web=__import__('secrets').token_urlsafe(18))
        messages.success(request, 'Clave del formulario web cambiada: actualice el formulario de su página.')
        return redirect('crm:configuracion')
    form = ConfigForm(request.POST or None, instance=cfg)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Configuración del CRM guardada.')
        return redirect('crm:configuracion')
    from erp.empresas import actual
    url_web = request.build_absolute_uri(reverse('crm_web', args=[actual(), cfg.token_web]))
    return render(request, 'ventas/crm_configuracion.html', {
        'form': form, 'cfg': cfg, 'url_web': url_web,
        'parametros': reverse('parametros') + '?grupo=CRM'})
