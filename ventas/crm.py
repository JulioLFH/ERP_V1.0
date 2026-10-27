"""CRM (módulo propio desde v1.80, URL /crm/): embudo comercial (oportunidades por etapa, tablero arrastrable),
actividades de seguimiento con responsable y recordatorio, ficha 360 del cliente, conversión del prospecto, crédito del
cliente, cotizaciones unidas a la oportunidad, WhatsApp, proforma inmobiliaria y plan de visitas (v1.81)."""
import re
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.forms import BootstrapMixin, remoto
from core.models import Tercero
from core.utils import excel_response, fmt_fecha

from . import crm_servicios as srv
from .models import (ActividadCRM, CambioEtapa, Campana, ConsentimientoDatos, ContactoCliente, Cotizacion,
                     Oportunidad)

User = get_user_model()
D0 = Decimal('0')


def _usuarios():
    return User.objects.filter(is_active=True, acceso_proveedor__isnull=True).order_by('first_name', 'username')


class OportunidadForm(BootstrapMixin, forms.ModelForm):
    consentimiento = forms.BooleanField(
        label='El prospecto autorizó el uso de sus datos personales (Ley 29733)', required=False,
        help_text='Se registra con la finalidad de la Configuración del CRM')

    class Meta:
        model = Oportunidad
        fields = ['nombre', 'tercero', 'prospecto', 'contacto', 'telefono', 'email', 'etapa', 'monto',
                  'probabilidad', 'fecha_cierre', 'fecha_inicio', 'origen', 'campana', 'unidad', 'responsable',
                  'cotizacion', 'motivo_perdida_tipo', 'competidor', 'motivo_perdida', 'notas']
        widgets = {'notas': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['tercero'], 'clientes')
        self.fields['etapa'].choices = srv.etapas()
        self.fields['responsable'].queryset = _usuarios()
        self.fields['campana'].queryset = Campana.objects.filter(
            Q(activa=True) | Q(pk=self.instance.campana_id or 0))
        from obras.models import Unidad
        self.fields['unidad'].queryset = Unidad.objects.filter(
            Q(estado='DISPONIBLE') | Q(pk=self.instance.unidad_id or 0)).exclude(tipo='COMUN').select_related('obra')
        self.fields['unidad'].label_from_instance = lambda u: f'{u.obra.codigo} {u} · S/ {u.precio_lista:,.2f}'
        # CRM-03: solo cotizaciones (no pedidos) abiertas del cliente, más la ya elegida
        cot = Cotizacion.objects.filter(tipo='COT').filter(
            Q(estado__in=['PENDIENTE', 'APROBADO']) | Q(pk=self.instance.cotizacion_id or 0))
        if self.instance.tercero_id:
            cot = cot.filter(tercero_id=self.instance.tercero_id)
        self.fields['cotizacion'].queryset = Cotizacion.objects.filter(
            pk__in=list(cot.order_by('-fecha', '-id').values_list('pk', flat=True)[:200]))
        if self.instance.pk:
            self.fields.pop('consentimiento')

    def clean(self):
        d = super().clean()
        hoy = timezone.localdate()
        if not d.get('tercero') and not (d.get('prospecto') or '').strip():
            self.add_error('prospecto', 'Indique el cliente o el nombre del prospecto.')
        if d.get('monto') is not None and d['monto'] <= 0:  # CRM-07
            self.add_error('monto', 'El monto estimado debe ser mayor que cero.')
        abierta = d.get('etapa') in Oportunidad.ABIERTAS
        if abierta and d.get('fecha_cierre') and d['fecha_cierre'] < hoy and (
                not self.instance.pk or 'fecha_cierre' in self.changed_data or 'etapa' in self.changed_data):
            self.add_error('fecha_cierre', 'El cierre estimado de una oportunidad abierta no puede ser anterior a hoy.')
        if d.get('etapa') == 'PERDIDA':
            if not d.get('motivo_perdida_tipo'):
                self.add_error('motivo_perdida_tipo', 'Elija por qué se perdió.')
            if d.get('motivo_perdida_tipo') == 'COMPETIDOR' and not (d.get('competidor') or '').strip():
                self.add_error('competidor', 'Indique el competidor que ganó.')
            if d.get('motivo_perdida_tipo') == 'OTRO' and len((d.get('motivo_perdida') or '').strip()) < 5:
                self.add_error('motivo_perdida', 'Describa el motivo.')
        if d.get('probabilidad') is not None and d['probabilidad'] > 100:
            self.add_error('probabilidad', 'Máximo 100.')
        # CRM-23: las mismas reglas que Contactos y Ventas
        if d.get('fecha_inicio') and d.get('fecha_cierre') and d['fecha_cierre'] < d['fecha_inicio']:
            self.add_error('fecha_cierre', 'El cierre no puede ser anterior al inicio de la oportunidad.')
        from core.forms import error_telefono
        error = error_telefono(d.get('telefono'))
        if error and (not self.instance.pk or 'telefono' in self.changed_data):
            self.add_error('telefono', error)
        if d.get('etapa') == 'GANADA' and d.get('probabilidad') is not None and d['probabilidad'] != 100:
            d['probabilidad'] = 100  # ganada = 100 %, perdida = 0 %
        elif d.get('etapa') == 'PERDIDA' and d.get('probabilidad'):
            d['probabilidad'] = 0
        if not self.instance.pk and 'consentimiento' in self.fields and not d.get('consentimiento') \
                and _persona_natural(d):
            self.add_error('consentimiento', 'El prospecto es una persona natural: registre que autorizó el uso de '
                                             'sus datos personales (Ley 29733) antes de guardarlo.')
        if d.get('etapa') in ('GANADA', 'PERDIDA') and 'etapa' in self.changed_data and \
                _licitacion_en_curso(self.instance):
            self.add_error('etapa', 'Es una licitación de obra: registre la buena pro o la pérdida en Obras › '
                                    'Licitaciones (la oportunidad se actualiza sola).')
        return d


EMPRESA_RE = re.compile(r'\b(s\.?a\.?c?\.?|s\.?r\.?l\.?|e\.?i\.?r\.?l\.?|s\.?a\.?a\.?|sociedad|empresa|corporaci[oó]n|'
                        r'consorcio|asociaci[oó]n|cooperativa|municipalidad|gobierno|ministerio|hospital|cl[ií]nica|'
                        r'universidad|colegio|instituto|inversiones|constructora|comercial|servicios|grupo)\b', re.I)


def _persona_natural(d):
    """CRM-23: el cliente con DNI o carné, o el prospecto cuyo nombre no es el de una empresa o entidad."""
    tercero = d.get('tercero')
    if tercero is not None:
        if tercero.tipo_doc == '6':
            return False
        return not ConsentimientoDatos.objects.filter(tercero=tercero, estado='OTORGADO').exists()
    nombre = (d.get('prospecto') or '').strip()
    return bool(nombre) and not EMPRESA_RE.search(nombre) and not re.search(r'\b20\d{9}\b', nombre)


def _licitacion_en_curso(o):
    """Licitación de obra abierta de la oportunidad (su resultado se registra en Obras)."""
    if not o.pk:
        return None
    from obras.models import Licitacion
    return Licitacion.objects.filter(oportunidad=o, resultado='EN_CURSO').first()


class ActividadForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ActividadCRM
        fields = ['tipo', 'fecha', 'hora', 'descripcion', 'responsable', 'recordatorio']
        widgets = {'hora': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['responsable'].queryset = _usuarios()

    def clean_fecha(self):
        f = self.cleaned_data['fecha']
        if f and f < timezone.localdate() - timedelta(days=30):
            raise forms.ValidationError('La fecha es de hace más de un mes.')
        return f


class VisitaForm(ActividadForm):
    class Meta(ActividadForm.Meta):
        fields = ['tercero', 'fecha', 'hora', 'descripcion', 'responsable', 'recordatorio']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tercero'].required = True
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
        remoto(self.fields['tercero'], 'clientes')


def _filtrar(request):
    qs = srv.visibles(request.user, Oportunidad.objects.select_related('tercero', 'responsable', 'campana'))
    if request.GET.get('mias'):
        qs = qs.filter(responsable=request.user)
    from core.utils import elegidos
    for campo in ('responsable', 'campana'):  # PLAT-13: varios valores
        valores = [v for v in elegidos(request, campo) if v.isdigit()]
        if valores:
            qs = qs.filter(**{f'{campo}_id__in': valores})
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(nombre__icontains=q) | Q(prospecto__icontains=q) | Q(tercero__nombre__icontains=q) |
                       Q(tercero__numero_doc__icontains=q) | Q(telefono__icontains=q) | Q(email__icontains=q))
    return qs


def _estancadas(oportunidades):
    """CRM-04: abiertas sin movimiento hace más de CRM_DIAS_ESTANCADA días (se marcan en rojo)."""
    limite = timezone.now() - timedelta(days=int(srv.parametro('CRM_DIAS_ESTANCADA')))
    return {o.pk for o in oportunidades if o.etapa in Oportunidad.ABIERTAS and o.actualizado < limite}


def _actividades_visibles(user, qs):
    if srv.ve_todo(user):
        return qs
    ids = srv.equipo_de(user)
    return qs.filter(Q(responsable_id__in=ids) | Q(responsable__isnull=True, usuario_id__in=ids) |
                     Q(oportunidad__responsable_id__in=ids))


@login_required
def embudo(request):
    """Tablero por etapas (se arrastran las tarjetas para cambiar de etapa)."""
    qs = list(_filtrar(request).filter(Q(etapa__in=Oportunidad.ABIERTAS) | Q(
        actualizado__date__gte=timezone.localdate().replace(day=1))))
    estancadas = _estancadas(qs)
    columnas = []
    for etapa, nombre in srv.etapas():
        tarjetas = [o for o in qs if o.etapa == etapa]
        for o in tarjetas:
            o.estancada = o.pk in estancadas
        columnas.append({'etapa': etapa, 'nombre': nombre, 'tarjetas': tarjetas,
                         'monto': sum((o.monto for o in tarjetas), D0),
                         'ponderado': sum((o.ponderado for o in tarjetas), D0)})
    abiertas = [o for o in qs if o.etapa in Oportunidad.ABIERTAS]
    pendientes = _actividades_visibles(request.user, ActividadCRM.objects.filter(
        hecha=False, fecha__lte=timezone.localdate()).filter(
        Q(oportunidad__etapa__in=Oportunidad.ABIERTAS) | Q(oportunidad__isnull=True))).select_related(
        'oportunidad', 'tercero')
    if request.GET.get('mias'):
        pendientes = pendientes.filter(Q(responsable=request.user) | Q(responsable__isnull=True, usuario=request.user))
    return render(request, 'ventas/crm_embudo.html', {
        'columnas': columnas, 'pendientes': pendientes[:20], 'estancadas': len(estancadas),
        'dias_estancada': int(srv.parametro('CRM_DIAS_ESTANCADA')),
        'total_abierto': sum((o.monto for o in abiertas), D0),
        'total_ponderado': sum((o.ponderado for o in abiertas), D0)})


@login_required
def oportunidades(request):
    from core.utils import elegidos
    qs = _filtrar(request)
    if elegidos(request, 'etapa'):
        qs = qs.filter(etapa__in=elegidos(request, 'etapa'))
    etiquetas = dict(srv.etapas())
    if request.GET.get('formato') == 'excel':
        return excel_response('Oportunidades', 'Oportunidades comerciales', [
            'Oportunidad', 'Cliente / prospecto', 'Contacto', 'Teléfono', 'Etapa', 'Monto', 'Probabilidad %',
            'Ponderado', 'Cierre estimado', 'Inicio probable', 'Origen', 'Campaña', 'Responsable',
            'Motivo de pérdida', 'Competidor', 'Detalle'],
            [[o.nombre, o.cliente, o.contacto, o.telefono, etiquetas.get(o.etapa), o.monto, o.prob, o.ponderado,
              fmt_fecha(o.fecha_cierre), fmt_fecha(o.fecha_inicio), o.get_origen_display(), str(o.campana or ''),
              str(o.responsable or ''), o.get_motivo_perdida_tipo_display(), o.competidor, o.motivo_perdida]
             for o in qs])
    lista = list(qs[:300])
    estancadas = _estancadas(lista)
    for o in lista:
        o.estancada, o.nombre_etapa = o.pk in estancadas, etiquetas.get(o.etapa)
    return render(request, 'ventas/crm_lista.html', {
        'oportunidades': lista, 'etapas': srv.etapas(), 'campanas': Campana.objects.all()[:50],
        'responsables': _usuarios() if srv.ve_todo(request.user) or len(srv.equipo_de(request.user)) > 1 else [],
        'dias_estancada': int(srv.parametro('CRM_DIAS_ESTANCADA'))})


def _registrar_consentimiento(o, usuario, canal='VERBAL'):
    from .models import ConfigCRM
    return ConsentimientoDatos.objects.create(
        titular=(o.contacto or o.prospecto or o.cliente or o.nombre)[:150], email=o.email or '',
        telefono=o.telefono or '', tercero=o.tercero, oportunidad=o, canal=canal,
        finalidad=ConfigCRM.actual().finalidad_datos, registrado_por=usuario if usuario.is_authenticated else None)


@login_required
def oportunidad_nueva(request):
    form = OportunidadForm(request.POST or None, initial={
        'responsable': request.user.pk, 'tercero': request.GET.get('tercero'), 'etapa': 'PROSPECTO',
        'origen': request.GET.get('origen', '')})
    if request.method == 'POST' and form.is_valid():
        o = form.save()
        CambioEtapa.objects.create(oportunidad=o, de='', a=o.etapa, usuario=request.user)
        if o.etapa in ('GANADA', 'PERDIDA'):
            Oportunidad.objects.filter(pk=o.pk).update(cerrada_en=timezone.localdate())
        if form.cleaned_data.get('consentimiento'):
            _registrar_consentimiento(o, request.user)
        srv.asignar_cartera(o.tercero, o.responsable)
        messages.success(request, f'Oportunidad «{o}» registrada. Agende su primera actividad.')
        for d in srv.duplicados(o)[:3]:  # CRM-02: aviso de posible duplicado
            messages.warning(request, f'Posible duplicado: «{d.nombre}» de {d.cliente} '
                                      f'({d.responsable or "sin responsable"}, {srv.nombre_etapa(d.etapa)}).')
        return redirect('crm:oportunidad', o.pk)
    return render(request, 'core/form.html', {
        'form': form, 'titulo': 'Nueva oportunidad',
        'ayuda': 'Si es un prospecto (aún no es cliente), escriba su nombre; luego use «Convertir en cliente» con su '
                 'RUC o DNI. Ceiba avisa si la oportunidad parece repetida.'})


def _siguiente_actividad(request, o):
    """CRM-04: al marcar hecha la última actividad pendiente de una oportunidad abierta se exige la siguiente."""
    tipo, fecha, texto = (request.POST.get('sig_tipo') or 'LLAMADA', request.POST.get('sig_fecha'),
                          (request.POST.get('sig_descripcion') or '').strip())
    if not fecha or not texto:
        return None
    from datetime import date
    try:
        f = date.fromisoformat(fecha)
    except ValueError:
        return None
    return ActividadCRM.objects.create(oportunidad=o, tipo=tipo if tipo in dict(ActividadCRM.TIPOS) else 'LLAMADA',
                                       fecha=f, descripcion=texto[:250], usuario=request.user,
                                       responsable=o.responsable or request.user)


@login_required
def oportunidad(request, pk):
    o = get_object_or_404(Oportunidad.objects.select_related('tercero', 'responsable', 'cotizacion', 'campana',
                                                            'unidad__obra'), pk=pk)
    if not srv.puede_ver(request.user, o):
        return HttpResponseForbidden('Esta oportunidad es de otro vendedor.')
    form = OportunidadForm(request.POST if request.POST.get('accion') == 'datos' else None, instance=o)
    actividad = ActividadForm(request.POST if request.POST.get('accion') == 'actividad' else None,
                              initial={'fecha': timezone.localdate(), 'responsable': o.responsable_id})
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'datos':
            anterior = Oportunidad.objects.filter(pk=o.pk).values_list('etapa', flat=True).first()
            if form.is_valid():
                nueva = form.cleaned_data['etapa']
                o = form.save(commit=False)
                o.etapa = anterior
                o.save()
                srv.cambiar_etapa(o, nueva, request.user)
                if o.cotizacion_id and o.cotizacion.oportunidad_id != o.pk:
                    srv.vincular_cotizacion(o.cotizacion, o)
                messages.success(request, 'Oportunidad actualizada.')
                return redirect('crm:oportunidad', pk)
        elif accion == 'actividad' and actividad.is_valid():
            a = actividad.save(commit=False)
            a.oportunidad, a.usuario = o, request.user
            a.save()
            o.save(update_fields=['actualizado'])
            return redirect('crm:oportunidad', pk)
        elif accion == 'hecha':
            a = o.actividades.filter(pk=request.POST.get('actividad'), hecha=False).first()
            if a is not None:
                otras = o.actividades.filter(hecha=False).exclude(pk=a.pk).exists()
                siguiente = _siguiente_actividad(request, o)
                if o.abierta and not otras and siguiente is None:
                    messages.error(request, 'Toda oportunidad abierta necesita su siguiente paso: registre la '
                                            'próxima actividad al marcar esta como realizada.')
                    return redirect('crm:oportunidad', pk)
                a.hecha, a.resultado = True, (request.POST.get('resultado') or '')[:250]
                a.save(update_fields=['hecha', 'resultado'])
                o.save(update_fields=['actualizado'])
                messages.success(request, 'Actividad realizada.' + (' Siguiente agendada.' if siguiente else ''))
            return redirect('crm:oportunidad', pk)
        elif accion == 'reasignar' and srv.puede_reasignar(request.user, o):
            nuevo = _usuarios().filter(pk=request.POST.get('responsable') or 0).first()
            if nuevo is not None and nuevo.pk != o.responsable_id:
                if o.responsable_id:
                    srv.reasignar(o.responsable, nuevo, request.user, request.POST.get('motivo', ''), [o])
                else:
                    Oportunidad.objects.filter(pk=o.pk).update(responsable=nuevo)
                messages.success(request, f'Oportunidad reasignada a {nuevo.get_full_name() or nuevo.username}.')
            return redirect('crm:oportunidad', pk)
        elif accion == 'consentimiento':
            _registrar_consentimiento(o, request.user, request.POST.get('canal') or 'VERBAL')
            messages.success(request, 'Consentimiento registrado.')
            return redirect('crm:oportunidad', pk)
    from core.modulos import modulos_del_usuario
    from obras.models import Licitacion
    mods = modulos_del_usuario(request.user)
    pendiente = o.actividades.filter(hecha=False).order_by('fecha', 'hora').first()
    entrega = None
    if o.cotizacion_id:  # CRM-12: fecha de entrega factible de lo cotizado (stock, receta e insumos)
        entrega = srv.fecha_entrega([(i.producto, i.cantidad) for i in o.cotizacion.items.select_related('producto')
                                     if i.producto_id])
    return render(request, 'ventas/crm_oportunidad.html', {
        'o': o, 'form': form, 'actividad': actividad, 'nombre_etapa': srv.nombre_etapa(o.etapa),
        'actividades': o.actividades.select_related('usuario', 'responsable'),
        'licitacion': Licitacion.objects.filter(oportunidad=o).select_related('obra').first(),
        'con_obras': 'obras' in mods, 'con_ventas': 'ventas' in mods,
        'credito': srv.credito(o.tercero), 'duplicados': srv.duplicados(o) if o.abierta else [],
        'cotizaciones': o.cotizaciones.order_by('-fecha', '-id'), 'entrega': entrega,
        'contactos': o.tercero.contactos_crm.filter(activo=True) if o.tercero_id else [],
        'consentimientos': o.consentimientos.all(), 'canales': ConsentimientoDatos.CANALES,
        'casos': o.casos.all(), 'sin_siguiente': o.abierta and pendiente is None,
        'estancada': o.pk in _estancadas([o]), 'puede_reasignar': srv.puede_reasignar(request.user, o),
        'usuarios': _usuarios(), 'tipos_actividad': ActividadCRM.TIPOS,
        'whatsapp': _numero_whatsapp(o.telefono or (o.tercero.telefono if o.tercero_id else '')),
        'historial': o.cambios.select_related('usuario'), 'etapas_nombres': dict(srv.etapas())})


@login_required
def cambiar_etapa(request, pk):
    """Desde el tablero (arrastrar y soltar). Perdida exige motivo: se pide en la ficha."""
    o = get_object_or_404(Oportunidad, pk=pk)
    etapa = request.POST.get('etapa')
    if request.method != 'POST' or etapa not in dict(Oportunidad.ETAPAS):
        return JsonResponse({'ok': False, 'error': 'Etapa no válida.'}, status=400)
    if not srv.puede_ver(request.user, o):
        return JsonResponse({'ok': False, 'error': 'Esta oportunidad es de otro vendedor.'}, status=403)
    if etapa == 'PERDIDA' and not o.motivo_perdida_tipo:
        return JsonResponse({'ok': False, 'error': 'Indique el motivo de pérdida en la ficha de la oportunidad.',
                             'url': reverse('crm:oportunidad', args=[o.pk])}, status=422)
    lic = _licitacion_en_curso(o) if etapa in ('GANADA', 'PERDIDA') else None
    if lic is not None:
        return JsonResponse({'ok': False, 'error': 'Es una licitación de obra: registre la buena pro o la pérdida en '
                                                   'la licitación.',
                             'url': reverse('obras:licitacion', args=[lic.obra_id])}, status=422)
    srv.cambiar_etapa(o, etapa, request.user)
    return JsonResponse({'ok': True, 'probabilidad': o.prob})


# ---------------------------------------------------------------- prospecto -> cliente (CRM-02)
class ConvertirForm(BootstrapMixin, forms.Form):
    tipo_doc = forms.ChoiceField(label='Tipo de documento', choices=[('6', 'RUC'), ('1', 'DNI'),
                                                                    ('4', 'Carné de extranjería')])
    numero_doc = forms.CharField(label='Número', max_length=15)
    nombre = forms.CharField(label='Nombre o razón social', max_length=200, required=False,
                             help_text='Con RUC se trae del padrón de SUNAT; vacío = el del prospecto')
    direccion = forms.CharField(label='Dirección', max_length=250, required=False)


@login_required
def convertir_cliente(request, pk):
    o = get_object_or_404(Oportunidad, pk=pk)
    if not srv.puede_ver(request.user, o):
        return HttpResponseForbidden()
    form = ConvertirForm(request.POST or None, initial={'nombre': o.prospecto})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            t = srv.convertir_en_cliente(o, d['tipo_doc'], d['numero_doc'], request.user, d['nombre'], d['direccion'])
            messages.success(request, f'{t.nombre} ahora es cliente; la oportunidad quedó a su nombre.')
            return redirect('crm:oportunidad', pk)
        except srv.ErrorCRM as exc:
            form.add_error('numero_doc', str(exc))
    return render(request, 'core/form.html', {'form': form, 'titulo': f'Convertir en cliente: {o.prospecto or o}',
                                              'ayuda': 'Si el documento ya es un cliente, se usa el existente.'})


# ---------------------------------------------------------------- WhatsApp (CRM-09)
def _numero_whatsapp(texto):
    digitos = re.sub(r'\D', '', texto or '')
    if len(digitos) == 9 and digitos.startswith('9'):
        digitos = '51' + digitos
    return digitos if len(digitos) >= 10 else ''


@login_required
def whatsapp(request, pk):
    """Abre el chat de WhatsApp con el contacto y deja la actividad registrada en la oportunidad."""
    from urllib.parse import quote
    o = get_object_or_404(Oportunidad, pk=pk)
    numero = _numero_whatsapp(request.GET.get('numero') or o.telefono or (o.tercero.telefono if o.tercero_id else ''))
    if not numero:
        messages.error(request, 'La oportunidad no tiene un celular válido.')
        return redirect('crm:oportunidad', pk)
    ActividadCRM.objects.create(oportunidad=o, tipo='CORREO', fecha=timezone.localdate(), hecha=True,
                                descripcion=f'WhatsApp a {numero}', usuario=request.user, recordatorio=False)
    o.save(update_fields=['actualizado'])
    texto = quote(f'Hola {o.contacto or ""}, le escribe {request.user.get_full_name() or request.user.username} '
                  f'sobre «{o.nombre}».'.replace('  ', ' '))
    return redirect(f'https://wa.me/{numero}?text={texto}')


# ---------------------------------------------------------------- proforma inmobiliaria (CRM-11)
@login_required
def proforma(request, pk):
    """Proforma de la unidad de interés con un cronograma de pagos simulado (separación, inicial y cuotas)."""
    o = get_object_or_404(Oportunidad.objects.select_related('unidad__obra', 'tercero'), pk=pk)
    if o.unidad_id is None:
        messages.error(request, 'Elija la unidad inmobiliaria de interés en la oportunidad.')
        return redirect('crm:oportunidad', pk)

    def numero(nombre, defecto, minimo=0, maximo=10 ** 9):
        try:
            return max(minimo, min(maximo, Decimal(request.GET.get(nombre) or defecto)))
        except ArithmeticError:
            return Decimal(defecto)
    precio = numero('precio', o.unidad.precio_lista, 0)
    separacion = numero('separacion', '2000', 0, precio)
    inicial_pct = numero('inicial', '10', 0, 100)
    cuotas = int(numero('cuotas', '12', 0, 360))
    hoy = timezone.localdate()
    inicial = (precio * inicial_pct / 100).quantize(Decimal('0.01'))
    filas = [('Separación', hoy, separacion), ('Cuota inicial (saldo)', hoy + timedelta(days=30),
                                                max(inicial - separacion, D0))]
    saldo = precio - max(inicial, separacion)
    if cuotas:
        cuota = (saldo / cuotas).quantize(Decimal('0.01'))
        for n in range(1, cuotas + 1):
            monto = cuota if n < cuotas else saldo - cuota * (cuotas - 1)
            filas.append((f'Cuota {n}', hoy + timedelta(days=30 * (n + 1)), monto))
    else:
        filas.append(('Saldo (crédito hipotecario o contado)', hoy + timedelta(days=60), saldo))
    return render(request, 'ventas/crm_proforma.html', {
        'o': o, 'u': o.unidad, 'precio': precio, 'filas': filas, 'separacion': separacion, 'inicial_pct': inicial_pct,
        'cuotas': cuotas, 'validez': hoy + timedelta(days=15), 'hoy': hoy})


# ---------------------------------------------------------------- actividades y visitas (CRM-04, CRM-12)
@login_required
def mis_actividades(request):
    hoy = timezone.localdate()
    qs = ActividadCRM.objects.filter(hecha=False).select_related('oportunidad', 'tercero', 'responsable', 'usuario')
    if request.GET.get('ver') == 'equipo':
        qs = _actividades_visibles(request.user, qs)
    else:
        qs = qs.filter(Q(responsable=request.user) | Q(responsable__isnull=True, usuario=request.user))
    qs = qs.order_by('fecha', 'hora')
    grupos = [('Vencidas', 'danger', [a for a in qs if a.fecha < hoy]),
              ('Hoy', 'warning', [a for a in qs if a.fecha == hoy]),
              ('Próximas', 'secondary', [a for a in qs if a.fecha > hoy][:100])]
    sin_paso = [o for o in srv.visibles(request.user, Oportunidad.objects.filter(
        etapa__in=Oportunidad.ABIERTAS)).exclude(actividades__hecha=False).select_related('tercero')[:50]]
    return render(request, 'ventas/crm_actividades.html', {'grupos': grupos, 'sin_paso': sin_paso,
                                                           'ver': request.GET.get('ver', '')})


@login_required
def actividad_hecha(request, pk):
    """Marcar realizada desde la bandeja (las de una oportunidad abierta piden la siguiente en su ficha)."""
    a = get_object_or_404(ActividadCRM, pk=pk, hecha=False)
    if request.method == 'POST':
        if a.oportunidad_id and a.oportunidad.abierta and not a.oportunidad.actividades.filter(hecha=False).exclude(
                pk=a.pk).exists():
            messages.info(request, 'Es la última actividad de la oportunidad: márquela en su ficha con el siguiente '
                                   'paso.')
            return redirect('crm:oportunidad', a.oportunidad_id)
        a.hecha, a.resultado = True, (request.POST.get('resultado') or '')[:250]
        a.save(update_fields=['hecha', 'resultado'])
    return redirect(request.POST.get('volver') or 'crm:actividades')


@login_required
def visitas(request):
    """Plan de visitas a clientes por semana (comercializadoras: CRM-12)."""
    from datetime import date
    try:
        desde = date.fromisoformat(request.GET.get('desde') or '')
    except ValueError:
        desde = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
    hasta = desde + timedelta(days=6)
    qs = _actividades_visibles(request.user, ActividadCRM.objects.filter(tipo='VISITA', fecha__range=[desde, hasta]))
    qs = qs.select_related('tercero', 'oportunidad', 'responsable', 'usuario').order_by('fecha', 'hora')
    form = VisitaForm(request.POST or None, initial={'fecha': timezone.localdate(), 'responsable': request.user.pk,
                                                     'tercero': request.GET.get('tercero'),
                                                     'descripcion': 'Visita comercial'})
    if request.method == 'POST' and form.is_valid():
        a = form.save(commit=False)
        a.tipo, a.usuario = 'VISITA', request.user
        a.save()
        messages.success(request, f'Visita a {a.tercero.nombre} agendada.')
        return redirect(f"{reverse('crm:visitas')}?desde={desde:%Y-%m-%d}")
    dias = [(desde + timedelta(days=i), [a for a in qs if a.fecha == desde + timedelta(days=i)]) for i in range(7)]
    return render(request, 'ventas/crm_visitas.html', {
        'dias': dias, 'desde': desde, 'hasta': hasta, 'form': form, 'anterior': desde - timedelta(days=7),
        'siguiente': desde + timedelta(days=7)})


# ---------------------------------------------------------------- ficha 360 del cliente (CRM-06, CRM-16)
class ContactoForm(BootstrapMixin, forms.ModelForm):
    consentimiento = forms.BooleanField(label='Autorizó el uso de sus datos personales (Ley 29733)', required=False)

    class Meta:
        model = ContactoCliente
        fields = ['nombre', 'cargo', 'rol', 'telefono', 'email', 'principal', 'notas']


@login_required
def cliente(request, pk):
    from .models import CarteraCliente, CasoPostventa, ContratoServicio, EncuestaSatisfaccion, Venta
    t = get_object_or_404(Tercero, pk=pk)
    form = ContactoForm(request.POST or None)
    if request.method == 'POST' and request.POST.get('accion') == 'contacto' and form.is_valid():
        c = form.save(commit=False)
        c.tercero = t
        c.save()
        if c.principal:
            t.contactos_crm.exclude(pk=c.pk).update(principal=False)
        if form.cleaned_data.get('consentimiento'):
            from .models import ConfigCRM
            ConsentimientoDatos.objects.create(titular=c.nombre, email=c.email, telefono=c.telefono, tercero=t,
                                               contacto=c, canal='VERBAL', finalidad=ConfigCRM.actual().finalidad_datos,
                                               registrado_por=request.user)
        messages.success(request, f'Contacto {c.nombre} agregado.')
        return redirect('crm:cliente', pk)
    if request.method == 'POST' and request.POST.get('accion') == 'cartera':
        nuevo = _usuarios().filter(pk=request.POST.get('responsable') or 0).first()
        CarteraCliente.objects.update_or_create(tercero=t, defaults={'responsable': nuevo})
        messages.success(request, 'Vendedor de la cartera actualizado.')
        return redirect('crm:cliente', pk)
    oportunidades = srv.visibles(request.user, Oportunidad.objects.filter(tercero=t)).select_related('responsable')
    ventas = Venta.objects.filter(tercero=t).exclude(estado='ANULADO').order_by('-fecha_emision', '-id')[:15]
    cartera = CarteraCliente.objects.filter(tercero=t).select_related('responsable').first()
    return render(request, 'ventas/crm_cliente.html', {
        't': t, 'form': form, 'credito': srv.credito(t), 'oportunidades': oportunidades,
        'ganado': sum((o.monto for o in oportunidades if o.etapa == 'GANADA'), D0),
        'cotizaciones': Cotizacion.objects.filter(tercero=t).order_by('-fecha', '-id')[:15], 'ventas': ventas,
        'actividades': ActividadCRM.objects.filter(Q(tercero=t) | Q(oportunidad__tercero=t)).select_related(
            'oportunidad', 'responsable', 'usuario').order_by('-fecha', '-id')[:30],
        'contactos': t.contactos_crm.all(), 'casos': CasoPostventa.objects.filter(tercero=t)[:20],
        'encuestas': EncuestaSatisfaccion.objects.filter(tercero=t)[:10],
        'contratos': ContratoServicio.objects.filter(tercero=t), 'cartera': cartera, 'usuarios': _usuarios(),
        'consentimientos': ConsentimientoDatos.objects.filter(tercero=t),
        'etapas_nombres': dict(srv.etapas()),
        'whatsapp': _numero_whatsapp(t.telefono)})


@login_required
def clientes(request):
    """Clientes con su vendedor, oportunidades abiertas y última compra (entrada a la ficha 360)."""
    from django.db.models import Count, Max
    from .models import CarteraCliente, Venta
    q = (request.GET.get('q') or '').strip()
    qs = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(numero_doc__icontains=q))
    cartera = dict(CarteraCliente.objects.values_list('tercero_id', 'responsable__username'))
    if request.GET.get('mios'):
        mios = set(CarteraCliente.objects.filter(responsable=request.user).values_list('tercero_id', flat=True))
        qs = qs.filter(pk__in=mios)
    terceros = list(qs.order_by('nombre')[:500])
    ids = [t.pk for t in terceros]
    ultima = dict(Venta.objects.filter(tercero_id__in=ids, estado='REGISTRADO').values('tercero').annotate(
        u=Max('fecha_emision')).values_list('tercero', 'u'))
    abiertas = dict(Oportunidad.objects.filter(tercero_id__in=ids, etapa__in=Oportunidad.ABIERTAS).values(
        'tercero').annotate(n=Count('id')).values_list('tercero', 'n'))
    filas = [[{'texto': t.nombre, 'url': reverse('crm:cliente', args=[t.pk])}, t.numero_doc,
              cartera.get(t.pk) or '—', abiertas.get(t.pk, 0), fmt_fecha(ultima.get(t.pk)), t.telefono or t.email]
             for t in terceros]
    columnas = ['Cliente', 'RUC / DNI', 'Vendedor', 'Oportunidades abiertas', 'Última compra', 'Contacto']
    from .crm_tabla import responder
    return responder(request, 'Clientes', [{'columnas': columnas, 'filas': filas}], buscar=True,
                     filtros=[('mios', 'Solo mi cartera', bool(request.GET.get('mios')))],
                     botones=[('Nuevo cliente', reverse('tercero_nuevo') + '?tipo=CLIENTE', 'bi-plus-lg')])
