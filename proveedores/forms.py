import re
from decimal import Decimal

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from core.forms import BootstrapMixin
from core.models import Tercero

from .models import AccesoProveedor


MAX_PDF = 5 * 1024 * 1024


class FacturaCabeceraForm(BootstrapMixin, forms.Form):
    archivo_pdf = forms.FileField(label='Factura en PDF', help_text='Representación impresa (máx. 5 MB)',
                                  widget=forms.ClearableFileInput(attrs={'accept': '.pdf,application/pdf'}))
    archivo_xml = forms.FileField(label='Factura en XML (o .zip de SUNAT)', required=False,
                                  help_text='Recomendado: con el XML se completan y validan los datos solos',
                                  widget=forms.ClearableFileInput(attrs={'accept': '.xml,.zip'}))
    serie = forms.CharField(max_length=4, required=False, help_text='4 caracteres, ej. F001 o E001')
    numero = forms.CharField(label='Número', max_length=8, required=False, help_text='Solo dígitos, ej. 1234')
    fecha_emision = forms.DateField(label='Fecha de emisión', required=False)
    total = forms.DecimalField(label='Monto total de la factura', max_digits=14, decimal_places=2, required=False,
                               min_value=Decimal('0.01'), help_text='Importe total con IGV, tal como figura en la factura')
    observaciones = forms.CharField(label='Comentario (opcional)', max_length=300, required=False)

    datos_xml = None

    def clean_archivo_pdf(self):
        f = self.cleaned_data['archivo_pdf']
        if f.size > MAX_PDF:
            raise forms.ValidationError('El PDF supera los 5 MB.')
        inicio = f.read(5)
        f.seek(0)
        if inicio != b'%PDF-':
            raise forms.ValidationError('El archivo no es un PDF.')
        return f

    def clean_archivo_xml(self):
        from .xml_ubl import ErrorXML, leer
        f = self.cleaned_data.get('archivo_xml')
        if f:
            try:
                self.datos_xml = leer(f.read(), f.name)
            except ErrorXML as exc:
                raise forms.ValidationError(str(exc))
        return f

    def clean(self):
        datos = super().clean()
        if self.datos_xml:  # los datos de la cabecera salen del XML
            return datos
        for campo in ('serie', 'numero', 'fecha_emision', 'total'):
            if not datos.get(campo) and campo not in self.errors:
                self.add_error(campo, 'Obligatorio (o cargue el XML de la factura).')
        serie = (datos.get('serie') or '').upper().strip()
        if serie and not re.fullmatch(r'[FE0-9][A-Z0-9]{3}', serie):
            self.add_error('serie', 'Serie inválida: 4 caracteres que empiezan con F, E o un dígito (F001).')
        datos['serie'] = serie
        numero = (datos.get('numero') or '').strip()
        if numero and (not numero.isdigit() or int(numero) == 0):
            self.add_error('numero', 'El número debe tener solo dígitos.')
        elif numero:
            datos['numero'] = str(int(numero))
        if datos.get('fecha_emision') and datos['fecha_emision'] > timezone.localdate():
            self.add_error('fecha_emision', 'La fecha de emisión no puede ser futura.')
        return datos


def leer_lineas(data, lineas):
    """{oc_item_id: (cantidad, precio)} desde el POST (campos cant_<id> y precio_<id>) y errores de formato."""
    enviadas, errores = {}, []
    for linea in lineas:
        pk = linea['item'].pk
        try:
            cantidad = Decimal(str(data.get(f'cant_{pk}') or '0').replace(',', ''))
            precio = Decimal(str(data.get(f'precio_{pk}') or '0').replace(',', ''))
        except Exception:
            errores.append(f'{linea["item"].descripcion}: cantidad o precio inválido.')
            continue
        enviadas[pk] = (cantidad.quantize(Decimal('0.01')), precio.quantize(Decimal('0.0001')))
    return enviadas, errores


class AccesoForm(BootstrapMixin, forms.Form):
    tercero = forms.ModelChoiceField(Tercero.objects.none(), label='Proveedor')
    username = forms.CharField(label='Usuario', max_length=150, help_text='Con este nombre ingresa al portal')
    nombre = forms.CharField(label='Nombre del contacto', max_length=150, required=False)
    email = forms.EmailField(label='Correo', help_text='Recibe las órdenes de compra y conformidades')
    activo = forms.BooleanField(required=False, initial=True)
    clave1 = forms.CharField(label='Contraseña', required=False, widget=forms.PasswordInput)
    clave2 = forms.CharField(label='Repetir contraseña', required=False, widget=forms.PasswordInput)

    def __init__(self, *args, acceso=None, **kwargs):
        self.acceso = acceso
        super().__init__(*args, **kwargs)
        self.fields['tercero'].queryset = Tercero.objects.filter(activo=True, tipo__in=['PROVEEDOR', 'AMBOS'])
        if acceso:
            u = acceso.usuario
            self.initial.update(tercero=acceso.tercero_id, username=u.username, nombre=u.first_name,
                                email=u.email, activo=u.is_active)
            self.fields['clave1'].help_text = 'Déjela vacía para no cambiarla.'
        else:
            self.fields['clave1'].required = self.fields['clave2'].required = True

    def clean_username(self):
        nombre = self.cleaned_data['username'].strip()
        qs = User.objects.filter(username__iexact=nombre)
        if self.acceso:
            qs = qs.exclude(pk=self.acceso.usuario_id)
        if qs.exists():
            raise forms.ValidationError('Ese usuario ya existe.')
        return nombre

    def clean(self):
        datos = super().clean()
        c1, c2 = datos.get('clave1'), datos.get('clave2')
        if c1 or c2:
            if c1 != c2:
                self.add_error('clave2', 'Las contraseñas no coinciden.')
            else:
                try:
                    validate_password(c1)
                except forms.ValidationError as exc:
                    self.add_error('clave1', exc)
        return datos

    def save(self):
        d = self.cleaned_data
        u = self.acceso.usuario if self.acceso else User()
        u.username, u.first_name, u.email, u.is_active = d['username'], d['nombre'], d['email'], d['activo']
        u.is_staff = u.is_superuser = False
        if d.get('clave1'):
            u.set_password(d['clave1'])
        u.save()
        u.groups.clear()  # el portal no da acceso a ningún módulo del ERP
        acceso = self.acceso or AccesoProveedor(usuario=u)
        acceso.tercero = d['tercero']
        acceso.save()
        return acceso
