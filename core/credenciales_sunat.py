"""Ajustes › Credenciales SUNAT y SIRE (v1.115): en una sola pantalla, los datos de acceso a SUNAT de la empresa.

- Usuario SOL secundario y su clave (facturación electrónica directa y guías de remisión).
- API SUNAT (consulta de validez de comprobantes): client_id y client_secret.
- SIRE: usuario SOL, su clave, client_id y client_secret.
- Guías de remisión (API GRE): client_id y client_secret.

Las claves y secretos se guardan cifrados en la base y no se vuelven a mostrar. Nada se borra solo: un campo de clave
vacío deja la guardada como está; para quitar una hay que marcar «Borrar» a propósito. Cada cambio queda en la
auditoría (sin los valores). Si un dato se guardó con otra llave del servidor, se avisa para volver a ingresarlo."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .cifrado import cifrar, descifrar, ilegible
from .models import Empresa, FacturacionConfig

# (clave del formulario, título, modelo 'empresa' | 'facturacion', campo, tipo: 'texto' | 'secreto' | 'campo_cifrado')
# 'secreto' = CharField que guarda el valor cifrado a mano; 'campo_cifrado' = CampoCifrado (se cifra solo al guardar)
GRUPOS = [
    ('Usuario SOL secundario', 'Facturación electrónica directa con SUNAT y guías de remisión.', [
        ('sol_usuario', 'Usuario SOL', 'facturacion', 'sol_usuario', 'texto'),
        ('sol_clave', 'Clave SOL', 'facturacion', 'sol_clave', 'secreto'),
    ]),
    ('SIRE (registros de ventas y compras)', 'Propuestas, cruce, aceptación y envío al SIRE.', [
        ('sire_usuario_sol', 'Usuario SOL del SIRE', 'empresa', 'sire_usuario_sol', 'texto'),
        ('sire_clave_sol', 'Clave SOL del SIRE', 'empresa', 'sire_clave_sol', 'secreto'),
        ('sire_client_id', 'SIRE: client_id', 'empresa', 'sire_client_id', 'texto'),
        ('sire_client_secret', 'SIRE: client_secret', 'empresa', 'sire_client_secret', 'campo_cifrado'),
    ]),
    ('API SUNAT (consulta de validez)', 'Validación de comprobantes de proveedores y, si el SIRE no tiene las suyas, '
                                        'también el SIRE.', [
        ('sunat_client_id', 'SUNAT API: client_id', 'empresa', 'sunat_client_id', 'texto'),
        ('sunat_client_secret', 'SUNAT API: client_secret', 'empresa', 'sunat_client_secret', 'campo_cifrado'),
    ]),
    ('Guías de remisión (API GRE)', 'Envío de guías de remisión electrónicas a SUNAT.', [
        ('gre_client_id', 'Guías: client_id', 'facturacion', 'gre_client_id', 'texto'),
        ('gre_client_secret', 'Guías: client_secret', 'facturacion', 'gre_client_secret', 'secreto'),
    ]),
]
CAMPOS = [c for _, _, campos in GRUPOS for c in campos]


def _objetos():
    return {'empresa': Empresa.actual(), 'facturacion': FacturacionConfig.actual()}


def estado(valor, tipo):
    """'guardada' | 'vacia' | 'ilegible' (se guardó con otra llave del servidor: hay que volver a ingresarla)."""
    if not valor:
        return 'vacia'
    if tipo == 'secreto':
        return 'guardada' if descifrar(valor) else 'ilegible'
    return 'ilegible' if ilegible(valor) else 'guardada'


class CredencialesForm(forms.Form):
    def __init__(self, *args, objetos=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.objetos = objetos or _objetos()
        for clave, titulo, modelo, campo, tipo in CAMPOS:
            actual = getattr(self.objetos[modelo], campo) or ''
            if tipo == 'texto':
                largo = self.objetos[modelo]._meta.get_field(campo).max_length
                self.fields[clave] = forms.CharField(label=titulo, required=False, max_length=largo,
                                                     initial=actual,
                                                     widget=forms.TextInput(attrs={'class': 'form-control',
                                                                                   'autocomplete': 'off'}))
            else:
                est = estado(actual, tipo)
                ayuda = {'guardada': 'Guardada (cifrada). Escriba otra solo para cambiarla.',
                         'vacia': 'No registrada.',
                         'ilegible': 'Hay un valor guardado con otra llave del servidor que esta instalación no puede '
                                     'leer: vuelva a ingresarlo.'}[est]
                self.fields[clave] = forms.CharField(label=titulo, required=False, max_length=200,
                                                     widget=forms.PasswordInput(attrs={'class': 'form-control',
                                                                                       'autocomplete': 'new-password'}),
                                                     help_text=ayuda)
                if est != 'vacia':
                    self.fields[f'{clave}__borrar'] = forms.BooleanField(
                        label=f'Borrar {titulo.lower()} guardada', required=False,
                        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
        self.order_fields([n for c in CAMPOS for n in (c[0], f'{c[0]}__borrar')])

    def grupos(self):
        """Para la plantilla: [(título, ayuda, [(campo, campo_borrar, estado)])]."""
        salida = []
        for titulo, ayuda, campos in GRUPOS:
            filas = []
            for clave, _, modelo, campo, tipo in campos:
                est = None if tipo == 'texto' else estado(getattr(self.objetos[modelo], campo) or '', tipo)
                filas.append((self[clave], self[f'{clave}__borrar'] if f'{clave}__borrar' in self.fields else None,
                              est))
            salida.append((titulo, ayuda, filas))
        return salida

    def guardar(self, usuario=None):
        """Guarda solo lo que cambió y solo estos campos (update_fields). Devuelve los títulos cambiados."""
        cambios = {'empresa': {}, 'facturacion': {}}
        titulos = []
        for clave, titulo, modelo, campo, tipo in CAMPOS:
            obj = self.objetos[modelo]
            if tipo == 'texto':
                nuevo = (self.cleaned_data.get(clave) or '').strip()
                if nuevo != (getattr(obj, campo) or ''):
                    cambios[modelo][campo] = [getattr(obj, campo) or '', nuevo]
                    setattr(obj, campo, nuevo)
                    titulos.append(titulo)
                continue
            nuevo = (self.cleaned_data.get(clave) or '').strip()
            if nuevo:
                setattr(obj, campo, cifrar(nuevo) if tipo == 'secreto' else nuevo)
            elif self.cleaned_data.get(f'{clave}__borrar'):
                setattr(obj, campo, '')
            else:
                continue  # vacío = la guardada no cambia
            cambios[modelo][campo] = ['***', '***' if nuevo else '(borrada)']
            titulos.append(titulo)
        from .auditoria import registrar
        for modelo, campos in cambios.items():
            if campos:
                obj = self.objetos[modelo]
                obj.save(update_fields=list(campos))
                registrar('MODIFICAR', obj, campos, 'Ajustes › Credenciales SUNAT y SIRE')
        return titulos


@login_required
def credenciales(request):
    form = CredencialesForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        titulos = form.guardar(request.user)
        if titulos:
            messages.success(request, 'Guardado: ' + ', '.join(titulos) + '.')
        else:
            messages.info(request, 'No hubo cambios.')
        return redirect('credenciales_sunat')
    return render(request, 'core/credenciales_sunat.html', {'form': form})
