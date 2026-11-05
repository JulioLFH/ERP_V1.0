"""Herramientas de los agentes: funciones del ERP que el modelo puede pedir. Cada una exige su permiso (el mismo
que el usuario necesita en pantalla) y devuelve JSON. Solo leen: el agente no graba ni aprueba nada."""
import json
import logging
from datetime import date, datetime
from decimal import Decimal

log = logging.getLogger(__name__)
MAX_RESULTADO = 20000  # caracteres que se devuelven al modelo por llamada


class ErrorHerramienta(Exception):
    """Error que se le explica al modelo (dato no encontrado, parámetro inválido)."""


class Herramienta:
    def __init__(self, funcion, nombre, descripcion, parametros, permiso):
        self.funcion, self.nombre, self.descripcion = funcion, nombre, descripcion
        self.parametros, self.permiso = parametros, permiso

    def definicion(self):
        """Definición para la API: todos los parámetros obligatorios y estrictos (sin campos extra)."""
        return {'name': self.nombre, 'description': self.descripcion, 'strict': True,
                'input_schema': {'type': 'object', 'properties': self.parametros,
                                 'required': list(self.parametros), 'additionalProperties': False}}


def herramienta(nombre, descripcion, parametros, permiso):
    def decorar(funcion):
        return Herramienta(funcion, nombre, descripcion, parametros, permiso)
    return decorar


def _json(valor):
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (date, datetime)):
        return valor.strftime('%d/%m/%Y')
    return str(valor)


def ejecutar(h, entrada, usuario):
    """(texto JSON, ok) de la herramienta para el usuario: sin el permiso o con parámetros que no son los suyos
    devuelve el error (el modelo lo explica); un error interno no se muestra en detalle."""
    from core.permisos import puede
    if not puede(usuario, h.permiso):
        return 'Su usuario no tiene permiso para esta consulta en el ERP.', False
    entrada = entrada if isinstance(entrada, dict) else {}
    faltan, sobran = set(h.parametros) - set(entrada), set(entrada) - set(h.parametros)
    if faltan or sobran:
        return f'Parámetros inválidos (faltan: {sorted(faltan)}, sobran: {sorted(sobran)}).', False
    try:
        datos = h.funcion(usuario, **entrada)
    except ErrorHerramienta as exc:
        return str(exc), False
    except Exception:
        log.exception('Herramienta %s', h.nombre)
        return 'Error interno al consultar el ERP; intente con otra pregunta.', False
    texto = json.dumps(datos, ensure_ascii=False, default=_json)
    if len(texto) > MAX_RESULTADO:
        texto = texto[:MAX_RESULTADO] + ' …(resultado recortado: pida un filtro más específico)'
    return texto, True
