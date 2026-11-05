from django import forms

from core.forms import BootstrapMixin

from .models import ConfigAgentes


class ConfigForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ConfigAgentes
        fields = ['activo', 'api_key', 'modelo', 'esfuerzo', 'tope_mensual_usd', 'max_herramientas']
        widgets = {'api_key': forms.PasswordInput(render_value=False)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['api_key'].required = False
        if self.instance.api_key:  # la clave no se muestra: vacío = se mantiene la guardada
            self.fields['api_key'].help_text = 'Hay una clave guardada: déjelo vacío para mantenerla.'
        self.fields['quitar_clave'] = forms.BooleanField(label='Quitar la clave guardada', required=False)

    def clean_api_key(self):
        return (self.cleaned_data.get('api_key') or '').strip() or self.instance.api_key

    def clean_max_herramientas(self):
        valor = self.cleaned_data.get('max_herramientas') or 0
        if not 1 <= valor <= 20:
            raise forms.ValidationError('Entre 1 y 20.')
        return valor

    def save(self, commit=True):
        if self.cleaned_data.get('quitar_clave'):
            self.instance.api_key = ''
        return super().save(commit)
