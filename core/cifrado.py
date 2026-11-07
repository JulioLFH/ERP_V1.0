"""Cifrado simétrico (Fernet) de claves guardadas en la base: clave SOL secundaria, clave del certificado digital y
(SEG-04) las contraseñas y tokens de integraciones con CampoCifrado. La llave sale de SECRET_KEY, que está fuera de la
base: sin ella (otro servidor con otra SECRET_KEY) las claves no se pueden leer y hay que volver a ingresarlas."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django import forms
from django.conf import settings
from django.db import models

PREFIJO = 'fernet:'


def _fernet():
    llave = hashlib.sha256(f'ceiba-erp:{settings.SECRET_KEY}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(llave))


def cifrar(texto):
    if not texto:
        return ''
    return PREFIJO + _fernet().encrypt(texto.encode('utf-8')).decode('ascii')


def descifrar(valor):
    """El texto original; '' si está vacío o se cifró con otra SECRET_KEY."""
    if not valor:
        return ''
    if not valor.startswith(PREFIJO):
        return valor
    try:
        return _fernet().decrypt(valor[len(PREFIJO):].encode('ascii')).decode('utf-8')
    except InvalidToken:
        return ''


def cifrar_guardados(app, modelo, campos):
    """Para una migración (RunPython): vuelve a guardar los valores existentes de CampoCifrado, así los antiguos sin
    cifrar quedan cifrados."""
    def correr(apps, schema_editor):
        alias = schema_editor.connection.alias
        for obj in apps.get_model(app, modelo).objects.using(alias).all():
            if any(getattr(obj, c) for c in campos):
                obj.save(using=alias, update_fields=campos)
    return correr


class CampoCifrado(models.TextField):
    """SEG-04: texto que se guarda cifrado en la base y se lee descifrado (el código y los formularios ven el texto
    original). max_length es el del texto original: lo valida el formulario, la columna es de texto. Un valor antiguo
    sin cifrar se lee igual y se cifra al volver a guardarse. Las copias de respaldo (JSON) lo llevan cifrado."""

    def from_db_value(self, value, expression, connection):
        return descifrar(value) if value else value

    def to_python(self, value):
        return descifrar(value) if isinstance(value, str) and value.startswith(PREFIJO) else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value or value.startswith(PREFIJO):
            return value or ''
        return cifrar(value)

    def value_to_string(self, obj):
        return cifrar(self.value_from_object(obj) or '')

    def formfield(self, **kwargs):
        opciones = {'max_length': self.max_length, 'required': not self.blank, 'label': self.verbose_name,
                    'help_text': self.help_text}
        opciones.update({k: v for k, v in kwargs.items() if k not in ('form_class', 'choices_form_class')})
        return forms.CharField(**opciones)
