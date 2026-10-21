"""Ajustes > Usuarios y permisos: alta de usuarios y asignación de módulos (grupos)."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import BootstrapMixin
from .models import PerfilAcceso
from .modulos import GRUPO_COSTOS, GRUPOS, POR_CLAVE


class UsuarioForm(BootstrapMixin, forms.ModelForm):
    modulos = forms.MultipleChoiceField(
        label='Módulos permitidos', required=False, widget=forms.CheckboxSelectMultiple,
        choices=[(clave, POR_CLAVE[clave]['nombre']) for clave in GRUPOS])
    es_admin = forms.BooleanField(label='Administrador (acceso a todos los módulos)', required=False)
    ver_costos = forms.BooleanField(
        label='Puede ver costos de inventario', required=False,
        help_text='Costo promedio, valorizado, kardex valorizado y valorización al cierre. Los administradores '
                  'siempre los ven.')
    acciones = forms.MultipleChoiceField(label='Acciones permitidas', required=False,
                                         widget=forms.CheckboxSelectMultiple)
    almacenes = forms.ModelMultipleChoiceField(label='Almacenes en que opera', required=False, queryset=None,
                                               widget=forms.CheckboxSelectMultiple,
                                               help_text='Ninguno marcado = todos los almacenes')
    series = forms.ModelMultipleChoiceField(label='Series que puede emitir', required=False, queryset=None,
                                            widget=forms.CheckboxSelectMultiple,
                                            help_text='Ninguna marcada = todas las series')
    limite_aprobacion = forms.DecimalField(label='Aprueba órdenes de compra hasta S/', required=False,
                                           min_value=0, max_digits=14, decimal_places=2,
                                           help_text='0 o vacío = sin límite')
    trabajador = forms.ModelChoiceField(label='Rinde gastos como', required=False, queryset=None,
                                        help_text='Trabajador responsable de sus entregas a rendir (rendición de '
                                                  'gastos)')
    trabajador_planilla = forms.ModelChoiceField(
        label='Trabajador de la planilla', required=False, queryset=None,
        help_text='Relación 1 a 1: su jefe sale de la estructura organizativa y, al registrar su cese, el usuario se '
                  'desactiva y sus aprobaciones pendientes pasan a su jefe')
    jefe = forms.ModelChoiceField(label='Jefe directo', required=False, queryset=None,
                                  help_text='Solo si no tiene puesto en la estructura organizativa (allí el jefe es '
                                            'quien ocupa el puesto superior)')
    celular = forms.CharField(label='Celular (WhatsApp)', required=False, max_length=20,
                              help_text='Con código de país, ej. 51987654321. Para enviar gastos por WhatsApp')
    clave1 = forms.CharField(label='Contraseña', required=False, widget=forms.PasswordInput)
    clave2 = forms.CharField(label='Repetir contraseña', required=False, widget=forms.PasswordInput)
    # permisos por pantalla, datos sensibles y filtros por dato
    perfil_acceso = forms.ModelChoiceField(
        label='Perfil de acceso', required=False, queryset=None,
        help_text='Al elegir un perfil (o marcar «volver a aplicar») se copian sus módulos, acciones, pantallas y '
                  'límites; luego puede ajustar lo propio del usuario')
    aplicar_perfil = forms.BooleanField(label='Volver a aplicar el perfil', required=False)
    ocultar_sueldos = forms.BooleanField(label='Ocultar sueldos y boletas de pago', required=False,
                                         help_text='No abre planillas, boletas, PLAME ni movimientos de personal; '
                                                   'en la lista de trabajadores no ve el sueldo')
    ocultar_saldos = forms.BooleanField(label='Ocultar saldos de caja y bancos', required=False,
                                        help_text='Ve sus cuentas sin el saldo; no abre flujo de caja, conciliación '
                                                  'ni extractos')
    centros = forms.ModelMultipleChoiceField(
        label='Solo trabajadores y planillas de estos centros de costo', required=False, queryset=None,
        widget=forms.CheckboxSelectMultiple, help_text='Ninguno marcado = todos (incluye los subcentros)')
    cuentas = forms.ModelMultipleChoiceField(label='Solo estas cuentas de caja y bancos', required=False,
                                             queryset=None, widget=forms.CheckboxSelectMultiple,
                                             help_text='Ninguna marcada = todas')
    vendedor = forms.CharField(label='Solo las ventas del vendedor', required=False, max_length=80,
                               help_text='Comprobantes y pedidos con este vendedor. Vacío = todos')

    CAMPOS_PROPIOS = ('modulos', 'es_admin', 'ver_costos', 'acciones', 'almacenes', 'series', 'limite_aprobacion',
                      'perfil_acceso', 'aplicar_perfil', 'ocultar_sueldos', 'ocultar_saldos', 'centros', 'cuentas',
                      'vendedor')

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        labels = {'username': 'Usuario', 'first_name': 'Nombres', 'last_name': 'Apellidos', 'is_active': 'Activo'}
        help_texts = {'username': 'Sin espacios. Con este nombre ingresa al sistema.'}

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        from .models import Almacen, PerfilUsuario, Serie
        from .permisos import ACCIONES
        self.fields['modulos'].widget.attrs.pop('class', None)
        self.fields['acciones'].choices = [(f'{m}.{a}', d) for m, lista in ACCIONES.items() for a, d, _ in lista]
        self.fields['almacenes'].queryset = Almacen.objects.filter(activo=True)
        self.fields['series'].queryset = Serie.objects.filter(activo=True, tipo__in=['01', '03', '07', '08', '12',
                                                                                      '00', '09', '31'])
        from contabilidad.models import CentroCosto
        from finanzas.models import Cuenta
        from .models import PerfilAcceso
        self.fields['perfil_acceso'].queryset = PerfilAcceso.objects.all()
        self.fields['centros'].queryset = CentroCosto.objects.filter(activo=True)
        self.fields['cuentas'].queryset = Cuenta.objects.all()
        for nombre in ('acciones', 'almacenes', 'series', 'centros', 'cuentas'):
            self.fields[nombre].widget.attrs.pop('class', None)
        from .models import Tercero
        self.fields['trabajador'].queryset = Tercero.objects.filter(activo=True, tipo_doc__in=['1', '4', '7'])
        jefes = User.objects.filter(is_active=True, acceso_proveedor__isnull=True).order_by('username')
        self.fields['jefe'].queryset = jefes.exclude(pk=self.instance.pk) if self.instance.pk else jefes
        from planillas.models import Trabajador
        libres = Trabajador.objects.filter(fecha_ingreso__isnull=False).exclude(fecha_cese__lt=timezone.localdate())
        self.fields['trabajador_planilla'].queryset = (
            libres.filter(usuario__isnull=True) | libres.filter(usuario=self.instance.pk or 0)).distinct()
        if self.instance.pk:
            self.initial['trabajador_planilla'] = Trabajador.objects.filter(usuario=self.instance).values_list(
                'pk', flat=True).first()
        perfil = PerfilUsuario.objects.filter(usuario=self.instance).first() if self.instance.pk else None
        if perfil:
            self.initial['trabajador'], self.initial['jefe'] = perfil.trabajador_id, perfil.jefe_id
            self.initial['celular'] = perfil.celular
        if self.instance.pk:
            nombres = set(self.instance.groups.values_list('name', flat=True))
            self.initial['modulos'] = [c for c, n in GRUPOS.items() if n in nombres]
            self.initial['es_admin'] = self.instance.is_superuser
            self.initial['ver_costos'] = GRUPO_COSTOS in nombres
            self.fields['clave1'].help_text = 'Déjela vacía para no cambiarla.'
        else:
            self.fields['clave1'].required = self.fields['clave2'].required = True
            from django.contrib.auth.password_validation import password_validators_help_texts
            self.fields['clave1'].help_text = ' '.join(password_validators_help_texts())
        self.pantallas_actuales = dict(perfil.pantallas or {}) if perfil else {}
        if perfil:
            self.initial['acciones'] = perfil.acciones
            self.initial['almacenes'] = list(perfil.almacenes.all())
            self.initial['series'] = list(perfil.series.all())
            self.initial['limite_aprobacion'] = perfil.limite_aprobacion
            self.initial['perfil_acceso'] = perfil.perfil_acceso_id
            self.initial['ocultar_sueldos'], self.initial['ocultar_saldos'] = not perfil.ver_sueldos, \
                not perfil.ver_saldos
            self.initial['centros'] = list(perfil.centros.all())
            self.initial['cuentas'] = list(perfil.cuentas.all())
            self.initial['vendedor'] = perfil.vendedor
        elif self.instance.pk:  # sin perfil hoy puede todo en sus módulos (salvo las acciones explícitas)
            from .permisos import EXPLICITAS
            self.initial['acciones'] = [c for c, _ in self.fields['acciones'].choices if c not in EXPLICITAS]
        else:  # usuario nuevo: las acciones sensibles (anular, aprobar...) se asignan a propósito
            self.initial['acciones'] = [f'{m}.{a}' for m, lista in ACCIONES.items() for a, _, s in lista if not s]

    def clean(self):
        data = super().clean()
        c1, c2 = data.get('clave1'), data.get('clave2')
        if c1 or c2:
            if c1 != c2:
                self.add_error('clave2', 'Las contraseñas no coinciden.')
            else:
                try:
                    validate_password(c1, self.instance)
                except forms.ValidationError as exc:
                    self.add_error('clave1', exc)
        if self.instance.pk and self.instance == self.editor:
            if not data.get('is_active'):
                self.add_error('is_active', 'No puede desactivar su propio usuario.')
            if self.instance.is_superuser and not data.get('es_admin'):
                self.add_error('es_admin', 'No puede quitarse a sí mismo el rol de administrador.')
        if not data.get('es_admin') and not data.get('modulos') and not self._perfil_a_aplicar(data):
            self.add_error('modulos', 'Asigne al menos un módulo (o un perfil de acceso).')
        return data

    def _perfil_a_aplicar(self, data):
        p = data.get('perfil_acceso')
        if p is None:
            return None
        anterior = self.initial.get('perfil_acceso')
        return p if data.get('aplicar_perfil') or p.pk != anterior else None

    def pantallas_enviadas(self):
        """{clave: nivel} de la tabla de pantallas del formulario (campos «pantalla|<ruta>»)."""
        from .politicas import NIVELES_PANTALLA
        validos = {n for n, _ in NIVELES_PANTALLA if n}
        if not self.is_bound:
            return self.pantallas_actuales
        return {k.partition('|')[2]: v for k, v in self.data.items() if k.startswith('pantalla|') and v in validos}

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_superuser = user.is_staff = bool(self.cleaned_data.get('es_admin'))
        if self.cleaned_data.get('clave1'):
            user.set_password(self.cleaned_data['clave1'])
        user.save()
        from .models import IntentoAcceso
        IntentoAcceso.objects.filter(usuario__iexact=user.username).delete()  # el administrador lo desbloquea
        grupos = [Group.objects.get_or_create(name=GRUPOS[c])[0] for c in self.cleaned_data.get('modulos', [])]
        if self.cleaned_data.get('ver_costos'):
            grupos.append(Group.objects.get_or_create(name=GRUPO_COSTOS)[0])
        # conserva grupos ajenos a los módulos y al permiso de costos
        otros = user.groups.exclude(name__in=list(GRUPOS.values()) + [GRUPO_COSTOS])
        user.groups.set(list(otros) + grupos)
        from .models import PerfilUsuario
        perfil, _ = PerfilUsuario.objects.get_or_create(usuario=user)
        perfil.acciones = sorted(self.cleaned_data.get('acciones') or [])
        perfil.limite_aprobacion = self.cleaned_data.get('limite_aprobacion') or 0
        perfil.trabajador, perfil.jefe = self.cleaned_data.get('trabajador'), self.cleaned_data.get('jefe')
        perfil.celular = (self.cleaned_data.get('celular') or '').strip()
        perfil.pantallas = self.pantallas_enviadas()
        perfil.ver_sueldos = not self.cleaned_data.get('ocultar_sueldos')
        perfil.ver_saldos = not self.cleaned_data.get('ocultar_saldos')
        perfil.vendedor = (self.cleaned_data.get('vendedor') or '').strip()
        perfil.perfil_acceso = self.cleaned_data.get('perfil_acceso')
        perfil.save()
        perfil.almacenes.set(self.cleaned_data.get('almacenes') or [])
        perfil.series.set(self.cleaned_data.get('series') or [])
        perfil.centros.set(self.cleaned_data.get('centros') or [])
        perfil.cuentas.set(self.cleaned_data.get('cuentas') or [])
        aplicar = self._perfil_a_aplicar(self.cleaned_data)
        if aplicar is not None and not user.is_superuser:
            from .politicas import aplicar_perfil
            aplicar_perfil(perfil, aplicar)
        from planillas.models import Trabajador
        t = self.cleaned_data.get('trabajador_planilla')
        Trabajador.objects.filter(usuario=user).exclude(pk=t.pk if t else 0).update(usuario=None)
        if t is not None and t.usuario_id != user.pk:
            Trabajador.objects.filter(pk=t.pk).update(usuario=user)
        return user


@login_required
def lista(request):
    # los usuarios del portal de proveedores se administran en Compras › Accesos de proveedores, no aquí
    usuarios = User.objects.filter(acceso_proveedor__isnull=True).prefetch_related('groups').order_by(
        '-is_active', 'username')
    filas = []
    for u in usuarios:
        nombres = {g.name for g in u.groups.all()}
        filas.append({'u': u, 'modulos': ['Todos'] if u.is_superuser else
                      [POR_CLAVE[c]['nombre'] for c, n in GRUPOS.items() if n in nombres],
                      'ver_costos': u.is_superuser or GRUPO_COSTOS in nombres})
    return render(request, 'core/usuarios.html', {'filas': filas})


def _guardar(request, usuario, titulo):
    form = UsuarioForm(request.POST or None, instance=usuario, editor=request.user)
    if request.method == 'POST' and form.is_valid():
        u = form.save()
        messages.success(request, f'Usuario {u.username} guardado.')
        from .segregacion import conflictos
        for c in conflictos(User.objects.get(pk=u.pk)):  # recargado: sin la caché de módulos y perfil
            messages.warning(request, f'Segregación de funciones ({c["riesgo"].lower()}): {c["motivo"]} '
                                      f'({c["nombre_a"]} + {c["nombre_b"]}).')
        return redirect('usuarios')
    from .permisos import ACCIONES
    marcadas = set(form['acciones'].value() or [])
    grupos_acciones = [{'modulo': POR_CLAVE[m], 'acciones': [
        {'valor': f'{m}.{a}', 'texto': d, 'sensible': s, 'marcada': f'{m}.{a}' in marcadas} for a, d, s in lista]}
        for m, lista in ACCIONES.items() if m in POR_CLAVE]
    return render(request, 'core/usuario_form.html', {'form': form, 'titulo': titulo,
                                                       'modulos_info': [POR_CLAVE[c] for c in GRUPOS],
                                                       'grupos_acciones': grupos_acciones,
                                                       'grupos_pantallas': grupos_pantallas(form.pantallas_enviadas()),
                                                       'niveles': _niveles()})


def _niveles():
    from .politicas import NIVELES_PANTALLA
    return NIVELES_PANTALLA


def grupos_pantallas(actuales):
    """Pantallas del menú por módulo con el nivel de acceso actual (para la tabla de permisos por pantalla)."""
    from .politicas import catalogo
    grupos = {}
    for p in catalogo():
        grupos.setdefault(p['modulo'], []).append({**p, 'nivel': actuales.get(p['clave'], '')})
    from .modulos import DERIVADOS
    return [{'modulo': POR_CLAVE[m], 'pantallas': lista,
             'origenes': ' '.join(sorted({m} | (DERIVADOS.get(m, set()) if m not in GRUPOS else set())))}
            for m, lista in grupos.items() if m in POR_CLAVE]


class PerfilAccesoForm(BootstrapMixin, forms.ModelForm):
    modulos = forms.MultipleChoiceField(label='Módulos', required=False, widget=forms.CheckboxSelectMultiple,
                                        choices=[(clave, POR_CLAVE[clave]['nombre']) for clave in GRUPOS])
    acciones = forms.MultipleChoiceField(label='Acciones', required=False, widget=forms.CheckboxSelectMultiple)
    actualizar_usuarios = forms.BooleanField(label='Aplicar los cambios a los usuarios que tienen este perfil',
                                             required=False, initial=True)

    class Meta:
        model = PerfilAcceso
        fields = ['nombre', 'descripcion', 'ver_costos', 'ver_sueldos', 'ver_saldos', 'limite_aprobacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .permisos import ACCIONES
        self.fields['acciones'].choices = [(f'{m}.{a}', d) for m, lista in ACCIONES.items() for a, d, _ in lista]
        for nombre in ('modulos', 'acciones'):
            self.fields[nombre].widget.attrs.pop('class', None)
        if self.instance.pk:
            self.initial['modulos'], self.initial['acciones'] = self.instance.modulos, self.instance.acciones

    def clean_modulos(self):
        if not self.cleaned_data.get('modulos'):
            raise forms.ValidationError('Marque al menos un módulo.')
        return self.cleaned_data['modulos']

    def pantallas_enviadas(self):
        from .politicas import NIVELES_PANTALLA
        validos = {n for n, _ in NIVELES_PANTALLA if n}
        if not self.is_bound:
            return dict(self.instance.pantallas or {})
        return {k.partition('|')[2]: v for k, v in self.data.items() if k.startswith('pantalla|') and v in validos}

    def save(self, commit=True):
        p = super().save(commit=False)
        p.modulos = sorted(self.cleaned_data.get('modulos') or [])
        p.acciones = sorted(self.cleaned_data.get('acciones') or [])
        p.pantallas = self.pantallas_enviadas()
        p.save()
        if self.cleaned_data.get('actualizar_usuarios'):
            from .models import PerfilUsuario
            from .politicas import aplicar_perfil
            for pu in PerfilUsuario.objects.filter(perfil_acceso=p, usuario__is_superuser=False).select_related(
                    'usuario'):
                aplicar_perfil(pu, p)
        return p


@login_required
def perfiles(request):
    """Perfiles de acceso (roles): se definen una vez y se aplican a varios usuarios."""
    from django.db.models import Count

    from .models import PerfilAcceso
    return render(request, 'core/perfiles_acceso.html', {
        'perfiles': PerfilAcceso.objects.annotate(n=Count('usuarios')), 'modulos': POR_CLAVE})


@login_required
def perfil_editar(request, pk=None):
    from .models import PerfilAcceso
    from .permisos import ACCIONES
    perfil = get_object_or_404(PerfilAcceso, pk=pk) if pk else PerfilAcceso()
    form = PerfilAccesoForm(request.POST or None, instance=perfil)
    if request.method == 'POST' and form.is_valid():
        p = form.save()
        messages.success(request, f'Perfil de acceso «{p.nombre}» guardado.')
        return redirect('perfiles_acceso')
    marcadas = set(form['acciones'].value() or [])
    grupos_acciones = [{'modulo': POR_CLAVE[m], 'acciones': [
        {'valor': f'{m}.{a}', 'texto': d, 'sensible': s, 'marcada': f'{m}.{a}' in marcadas} for a, d, s in lista]}
        for m, lista in ACCIONES.items() if m in POR_CLAVE]
    return render(request, 'core/perfil_acceso_form.html', {
        'form': form, 'titulo': f'Perfil {perfil.nombre}' if pk else 'Nuevo perfil de acceso',
        'modulos_info': [POR_CLAVE[c] for c in GRUPOS], 'grupos_acciones': grupos_acciones,
        'grupos_pantallas': grupos_pantallas(form.pantallas_enviadas()), 'niveles': _niveles()})


@login_required
def segregacion(request):
    """Matriz de segregación de funciones: qué usuarios tienen permisos incompatibles."""
    from .segregacion import REGLAS, REGLAS_FIJAS, estricta, reporte
    filas, admins = reporte()
    return render(request, 'core/segregacion.html', {
        'filas': filas, 'admins': admins, 'reglas': REGLAS, 'estricta': estricta(), 'reglas_fijas': REGLAS_FIJAS,
        'con_conflicto': sum(1 for f in filas if f['conflictos'])})


@login_required
def nuevo(request):
    return _guardar(request, User(is_active=True), 'Nuevo usuario')


@login_required
def editar(request, pk):
    usuario = get_object_or_404(User, pk=pk, acceso_proveedor__isnull=True)  # los del portal: en Compras
    if request.method == 'POST' and request.POST.get('accion') == 'quitar_2fa':
        # el usuario perdió el teléfono y sus códigos de respaldo: solo un administrador, con motivo
        from .auditoria import registrar
        from .models import SegundoFactor
        motivo = request.POST.get('motivo', '').strip()
        if not request.user.is_superuser:
            messages.error(request, 'Solo un administrador puede quitar la verificación en dos pasos.')
        elif len(motivo) < 10:
            messages.error(request, 'Indique el motivo (mínimo 10 caracteres).')
        else:
            SegundoFactor.objects.filter(usuario=usuario).delete()
            registrar('MODIFICAR', usuario, {'Doble factor': ['Activo', 'Quitado por el administrador']}, motivo)
            messages.success(request, f'Se quitó la verificación en dos pasos de {usuario.username}; '
                                      'deberá activarla de nuevo.')
        return redirect('usuario_editar', pk)
    return _guardar(request, usuario, 'Editar usuario')
