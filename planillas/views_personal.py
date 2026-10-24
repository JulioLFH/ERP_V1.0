"""Pantallas de personal: estructura organizativa (organigrama, unidades y puestos), movimientos de personal,
legajo y contratos, solicitudes de vacaciones y permisos, exportación para el T-Registro y el portal del
trabajador (autoservicio)."""
from datetime import date

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.forms import BootstrapMixin
from core.models import Empresa
from core.permisos import puede
from core.utils import excel_response

from . import organizacion as org
from .models import (Contrato, DocumentoLegajo, MovimientoPersonal, Puesto, Trabajador, UnidadOrganizativa,
                     Vacacion)


def _fecha(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


# ---------------------------------------------------------------- estructura organizativa
class UnidadForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = UnidadOrganizativa
        fields = ['codigo', 'nombre', 'tipo', 'padre', 'centro_costo', 'activo']


class PuestoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Puesto
        fields = ['codigo', 'nombre', 'unidad', 'superior', 'centro_costo', 'activo']

    def clean(self):
        d = super().clean()
        p, n = d.get('superior'), 0
        while p is not None and n < 50:  # el puesto no puede reportar a sí mismo ni a quien le reporta
            if self.instance.pk and p.pk == self.instance.pk:
                self.add_error('superior', 'Crearía un ciclo en la jerarquía.')
                break
            p, n = p.superior, n + 1
        return d


@login_required
def organigrama(request):
    unidad = get_object_or_404(UnidadOrganizativa, pk=request.GET['unidad']) if request.GET.get('unidad') else None
    puesto = get_object_or_404(Puesto, pk=request.GET['puesto']) if request.GET.get('puesto') else None
    fu = UnidadForm(request.POST if request.POST.get('que') == 'unidad' else None, instance=unidad, prefix='u')
    fp = PuestoForm(request.POST if request.POST.get('que') == 'puesto' else None, instance=puesto, prefix='p')
    if request.method == 'POST':
        if not puede(request.user, 'planillas.configurar'):
            messages.error(request, 'Su usuario no configura la estructura organizativa.')
            return redirect('planillas:organigrama')
        form = fu if request.POST.get('que') == 'unidad' else fp
        if form.is_valid():
            form.save()
            messages.success(request, 'Estructura organizativa actualizada.')
            return redirect('planillas:organigrama')
    return render(request, 'planillas/organigrama.html', {
        'arbol': org.arbol(), 'fu': fu, 'fp': fp, 'unidad': unidad, 'puesto': puesto,
        'sin_puesto': Trabajador.objects.filter(puesto__isnull=True, fecha_ingreso__isnull=False).exclude(
            fecha_cese__lt=timezone.localdate()).count()})


# ---------------------------------------------------------------- movimientos de personal
class MovimientoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = MovimientoPersonal
        fields = ['trabajador', 'tipo', 'fecha_efectiva', 'motivo', 'sueldo', 'puesto', 'cargo', 'centro_costo',
                  'motivo_cese']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from contabilidad.models import CentroCosto
        self.fields['puesto'].queryset = Puesto.objects.filter(activo=True)
        self.fields['centro_costo'].queryset = CentroCosto.objects.filter(activo=True)

    def clean(self):
        d = super().clean()
        if d.get('trabajador') and d.get('tipo'):
            m = MovimientoPersonal(**{k: v for k, v in d.items() if k in self.Meta.fields})
            for e in org.validar(m):
                self.add_error(None, e)
        return d


@login_required
def movimientos(request):
    org.aplicar_vigentes()
    qs = MovimientoPersonal.objects.select_related('trabajador', 'puesto', 'centro_costo')
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'])
    if request.GET.get('trabajador'):
        qs = qs.filter(trabajador_id=request.GET['trabajador'])
    return render(request, 'planillas/movimientos.html', {
        'movimientos': qs[:300], 'estados': MovimientoPersonal.ESTADOS,
        'por_aprobar': [m for m in MovimientoPersonal.objects.filter(estado='ENVIADO') if org.puede_aprobar(
            request.user, m)]})


@login_required
def movimiento_nuevo(request):
    inicial = {'fecha_efectiva': timezone.localdate()}
    if request.GET.get('trabajador'):
        inicial['trabajador'] = request.GET['trabajador']
    form = MovimientoForm(request.POST or None, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        m = form.save(commit=False)
        m.solicitante = request.user
        m.save()
        try:
            if request.POST.get('accion') == 'enviar':
                org.enviar(m, request.user)
                messages.success(request, f'{m} enviado a aprobación.')
            else:
                messages.success(request, 'Movimiento guardado en borrador.')
        except org.ErrorPersonal as exc:
            messages.error(request, str(exc))
        return redirect('planillas:movimiento', m.pk)
    return render(request, 'planillas/movimiento_form.html', {'form': form})


@login_required
def movimiento(request, pk):
    m = get_object_or_404(MovimientoPersonal.objects.select_related('trabajador', 'puesto', 'centro_costo'), pk=pk)
    if request.method == 'POST':
        accion, comentario = request.POST.get('accion'), request.POST.get('comentario', '')
        try:
            if accion == 'enviar':
                org.enviar(m, request.user)
            elif accion == 'aprobar':
                org.aprobar(m, request.user, comentario)
            elif accion == 'rechazar':
                org.rechazar(m, request.user, comentario)
            elif accion == 'anular' and m.estado in ('BORRADOR', 'ENVIADO', 'APROBADO'):
                m.estado = 'ANULADO'
                m.save(update_fields=['estado'])
            messages.success(request, f'{m}: {m.get_estado_display().lower()}.')
        except org.ErrorPersonal as exc:
            messages.error(request, str(exc))
        return redirect('planillas:movimiento', pk)
    from core.aprobaciones import historial, pendiente
    return render(request, 'planillas/movimiento.html', {
        'm': m, 'puede_aprobar': org.puede_aprobar(request.user, m), 'historial': historial('MOVIMIENTO', m),
        'quien_aprueba': org.quien_aprueba(m) if m.estado == 'ENVIADO' else [],
        'pendiente': pendiente(m.niveles, m.nivel_aprobado) if m.estado == 'ENVIADO' else None})


# ---------------------------------------------------------------- legajo y contratos
class ContratoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Contrato
        fields = ['tipo', 'inicio', 'fin', 'renueva', 'observaciones']

    def clean(self):
        d = super().clean()
        if d.get('tipo') != 'INDETERMINADO' and not d.get('fin'):
            self.add_error('fin', 'Un contrato sujeto a modalidad tiene fecha de vencimiento.')
        if d.get('fin') and d.get('inicio') and d['fin'] < d['inicio']:
            self.add_error('fin', 'Debe ser posterior al inicio.')
        return d


class DocumentoForm(BootstrapMixin, forms.ModelForm):
    from core.sustentos import SustentoField
    archivo = SustentoField()

    class Meta:
        model = DocumentoLegajo
        fields = ['tipo', 'descripcion', 'vence']


@login_required
def legajo(request, pk):
    t = get_object_or_404(Trabajador, pk=pk)
    fc = ContratoForm(request.POST if request.POST.get('que') == 'contrato' else None, prefix='c')
    fc.fields['renueva'].queryset = t.contratos.all()
    fd = DocumentoForm(request.POST if request.POST.get('que') == 'documento' else None,
                       request.FILES if request.POST.get('que') == 'documento' else None, prefix='d')
    if request.method == 'POST':
        if not puede(request.user, 'planillas.calcular'):
            messages.error(request, 'Su usuario no edita el legajo.')
            return redirect('planillas:legajo', pk)
        if request.POST.get('que') == 'contrato' and fc.is_valid():
            c = fc.save(commit=False)
            c.trabajador = t
            c.save()
            messages.success(request, f'Contrato registrado ({c}).')
            return redirect('planillas:legajo', pk)
        if request.POST.get('que') == 'documento' and fd.is_valid():
            from core.sustentos import adjuntar
            doc = fd.save(commit=False)
            doc.trabajador = t
            doc.save()
            adjuntar(doc, fd.cleaned_data['archivo'], request.user, doc.get_tipo_display())
            messages.success(request, f'{doc.get_tipo_display()} agregado al legajo.')
            return redirect('planillas:legajo', pk)
    from core.sustentos import de
    documentos = list(t.legajo.all())
    for d in documentos:
        d.archivos = list(de(d))
    return render(request, 'planillas/legajo.html', {
        't': t, 'fc': fc, 'fd': fd, 'contratos': t.contratos.all(), 'documentos': documentos,
        'movimientos': t.movimientos.all()[:30], 'hoy': timezone.localdate(),
        'jefe': org.jefe_de_trabajador(t)})


@login_required
def contratos(request):
    """Contratos y legajo de todos los trabajadores: vigentes, por vencer (30 días) y vencidos, y los documentos del
    legajo por vencer o faltantes (DNI, contrato firmado, EMO). Cada fila abre el legajo del trabajador."""
    from datetime import timedelta
    hoy = timezone.localdate()
    filtro = request.GET.get('filtro', 'por_vencer')
    vigentes = (Trabajador.objects.filter(fecha_ingreso__isnull=False).exclude(fecha_cese__lt=hoy)
                .order_by('apellido_paterno', 'nombres'))
    ultimo = {}
    for c in Contrato.objects.filter(trabajador__in=vigentes).order_by('trabajador_id', '-inicio', '-id'):
        ultimo.setdefault(c.trabajador_id, c)
    filas = []
    for t in vigentes:
        c = ultimo.get(t.pk)
        dias = c.dias_para_vencer if c else None
        estado = ('sin contrato' if c is None else 'indeterminado' if c.fin is None else
                  'vencido' if dias < 0 else 'por vencer' if dias <= 30 else 'vigente')
        if filtro == 'por_vencer' and estado not in ('por vencer', 'vencido', 'sin contrato'):
            continue
        filas.append({'t': t, 'c': c, 'dias': dias, 'estado': estado})
    docs = (DocumentoLegajo.objects.filter(trabajador__in=vigentes, vence__isnull=False,
                                           vence__lte=hoy + timedelta(days=30))
            .select_related('trabajador').order_by('vence'))
    con_tipo = {(d['trabajador_id'], d['tipo']) for d in DocumentoLegajo.objects.filter(trabajador__in=vigentes)
                .values('trabajador_id', 'tipo')}
    faltan = [{'t': t, 'tipos': [n for k, n in DocumentoLegajo.TIPOS if k in ('DNI', 'CONTRATO', 'EMO')
                                 and (t.pk, k) not in con_tipo]} for t in vigentes]
    faltan = [f for f in faltan if f['tipos']]
    if request.GET.get('formato') == 'excel':
        return excel_response('Contratos', f'CONTRATOS · {Empresa.actual().razon_social}', [
            'Trabajador', 'Documento', 'Modalidad', 'Inicio', 'Vence', 'Días', 'Estado'], [
            [f['t'].nombre_completo, f['t'].numero_doc, f['c'].get_tipo_display() if f['c'] else '',
             f['c'].inicio if f['c'] else None, f['c'].fin if f['c'] else None, f['dias'], f['estado']]
            for f in filas])
    return render(request, 'planillas/contratos.html', {'filas': filas, 'filtro': filtro, 'docs': docs,
                                                        'faltan': faltan, 'hoy': hoy})


@login_required
def t_registro(request):
    """Datos para el T-Registro de SUNAT (altas y bajas): datos personales, laborales, de seguridad social y
    del contrato de los trabajadores vigentes o con movimientos en el rango."""
    hoy = timezone.localdate()
    desde = _fecha(request.GET.get('desde')) or hoy.replace(day=1)
    hasta = _fecha(request.GET.get('hasta')) or hoy
    filas = []
    for t in Trabajador.objects.filter(fecha_ingreso__isnull=False).select_related('afp', 'puesto').order_by(
            'apellido_paterno', 'nombres'):
        if not t.activo_en(desde, hasta):
            continue
        c = t.contratos.order_by('-inicio').first()
        evento = ('ALTA' if desde <= t.fecha_ingreso <= hasta else
                  'BAJA' if t.fecha_cese and desde <= t.fecha_cese <= hasta else 'VIGENTE')
        filas.append([evento, t.get_tipo_doc_display(), t.numero_doc, t.apellido_paterno, t.apellido_materno,
                      t.nombres, t.fecha_nacimiento, t.get_sexo_display() if t.sexo else '', t.direccion, t.telefono,
                      t.email, t.get_tipo_display(), t.get_regimen_display(), t.fecha_ingreso, t.fecha_cese,
                      t.motivo_cese, c.get_tipo_display() if c else '', c.fin if c else None,
                      t.puesto.nombre if t.puesto_id else t.cargo, t.sueldo, t.get_sistema_pensiones_display(),
                      t.afp.nombre if t.afp_id else '', t.cuspp, 'EsSalud', t.banco, t.cuenta_sueldo])
    return excel_response(f'T-Registro_{desde}_{hasta}', f'T-REGISTRO · {Empresa.actual().razon_social} · '
                                                          f'{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}', [
        'Evento', 'Tipo doc.', 'N° doc.', 'Apellido paterno', 'Apellido materno', 'Nombres', 'Nacimiento', 'Sexo',
        'Dirección', 'Teléfono', 'Correo', 'Tipo de trabajador', 'Régimen laboral', 'Fecha de ingreso',
        'Fecha de cese', 'Motivo de cese', 'Tipo de contrato', 'Vence', 'Ocupación', 'Remuneración',
        'Sistema de pensiones', 'AFP', 'CUSPP', 'Seguro de salud', 'Banco', 'Cuenta de sueldo'], filas)


# ---------------------------------------------------------------- solicitudes de vacaciones y permisos
@login_required
def solicitudes_personal(request):
    if request.method == 'POST':
        v = get_object_or_404(Vacacion, pk=request.POST.get('vacacion'))
        try:
            org.resolver_vacacion(v, request.user, request.POST.get('accion') == 'aprobar',
                                  request.POST.get('comentario', ''))
            messages.success(request, f'Solicitud de {v.trabajador.nombre_completo}: {v.get_estado_display().lower()}.')
        except org.ErrorPersonal as exc:
            messages.error(request, str(exc))
        return redirect('planillas:solicitudes_personal')
    pendientes = Vacacion.objects.filter(estado='SOLICITADA').select_related('trabajador', 'solicitado_por')
    return render(request, 'planillas/solicitudes_personal.html', {
        'mias': [v for v in pendientes if org.puede_aprobar_vacacion(request.user, v)],
        'todas': pendientes if puede(request.user, 'planillas.calcular') else [],
        'recientes': Vacacion.objects.exclude(estado='SOLICITADA').exclude(solicitado_por=None).select_related(
            'trabajador')[:30]})


# ---------------------------------------------------------------- portal del trabajador (autoservicio)
class DatosForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Trabajador
        fields = ['email', 'telefono', 'direccion']


@login_required
def mi_portal(request):
    """Autoservicio: boletas, vacaciones y permisos (los aprueba su jefe según la jerarquía), récord vacacional y
    datos de contacto. Solo para usuarios vinculados a un trabajador de la planilla."""
    t = org.trabajador_de_usuario(request.user)
    if t is None:
        return render(request, 'planillas/mi_portal.html', {'sin_trabajador': True})
    datos = DatosForm(request.POST if request.POST.get('que') == 'datos' else None, instance=t, prefix='dp')
    if request.method == 'POST':
        try:
            if request.POST.get('que') == 'vacaciones':
                desde, hasta = _fecha(request.POST.get('desde')), _fecha(request.POST.get('hasta'))
                if not (desde and hasta):
                    raise org.ErrorPersonal('Indique las fechas.')
                tipo = request.POST.get('tipo') if request.POST.get('tipo') in ('GOCE', 'PERMISO') else 'GOCE'
                v = org.solicitar_vacaciones(t, request.user, tipo, desde, hasta, request.POST.get('observaciones', ''))
                aprobador = v.niveles[0]['nombre'] if v.niveles else ''
                messages.success(request, f'Solicitud enviada{" a " + aprobador if aprobador else ""} para su '
                                          f'aprobación.')
            elif request.POST.get('que') == 'datos' and datos.is_valid():
                datos.save()
                messages.success(request, 'Datos de contacto actualizados.')
        except org.ErrorPersonal as exc:
            messages.error(request, str(exc))
        return redirect('mi_portal')
    from .calculo import record_vacacional
    from .models import FilaPlanilla
    try:
        anios, trunco = record_vacacional(t)
    except Exception:  # sin fecha de ingreso u otros datos incompletos: el portal se muestra igual
        anios, trunco = [], None
    return render(request, 'planillas/mi_portal.html', {
        't': t, 'datos': datos, 'jefe': org.jefe_de_trabajador(t),
        'boletas': FilaPlanilla.objects.filter(trabajador=t, planilla__estado__in=['CERRADA', 'PAGADA']).select_related(
            'planilla').order_by('-planilla__periodo')[:24],
        'solicitudes': t.vacaciones.order_by('-creado')[:20],
        'pendientes_vac': sum(a['pendientes'] for a in anios), 'trunco': trunco})


@login_required
def mi_boleta(request, pk):
    from .models import FilaPlanilla
    t = org.trabajador_de_usuario(request.user)
    fila = get_object_or_404(FilaPlanilla.objects.select_related('planilla', 'trabajador__afp',
                                                                  'trabajador__centro_costo'),
                             pk=pk, trabajador=t, planilla__estado__in=['CERRADA', 'PAGADA'])
    return render(request, 'planillas/boletas.html', {
        'planilla': fila.planilla, 'filas': [fila], 'empresa': Empresa.actual(),
        'tipos': [('INGRESO', 'Ingresos'), ('DESCUENTO', 'Descuentos'), ('APORTE', 'Aportes del empleador')]})
