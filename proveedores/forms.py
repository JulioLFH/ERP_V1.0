import re
from decimal import Decimal

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from core.forms import BootstrapMixin
from core.models import Tercero

from .models import AccesoProveedor


class FacturaCabeceraForm(BootstrapMixin, forms.Form):
    serie = forms.CharField(max_length=4, help_text='4 caracteres, ej. F001 o E001')
    numero = forms.CharField(label='Número', max_length=8, help_text='Solo dígitos, ej. 1234')
    fecha_emision = forms.DateField(label='Fecha de emisión')
    observaciones = forms.CharField(label='Comentario (opcional)', max_length=300, required=False)

    def clean_serie(self):
        serie = self.cleaned_data['serie'].upper().strip()
        if not re.fullmatch(r'[FE0-9][A-Z0-9]{3}', serie):
            raise forms.ValidationError('Serie inválida: 4 caracteres que empiezan con F, E o un dígito (F001).')
        return serie

    def clean_numero(self):
        numero = self.cleaned_data['numero'].strip()
        if not numero.isdigit() or int(numero) == 0:
            raise forms.ValidationError('El número debe tener solo dígitos.')
        return str(int(numero))

    def clean_fecha_emision(self):
        fecha = self.cleaned_data['fecha_emision']
        if fecha > timezone.localdate():
            raise forms.ValidationError('La fecha de emisión no puede ser futura.')
        return fecha


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
