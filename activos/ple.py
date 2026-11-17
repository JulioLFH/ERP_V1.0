"""PLE 7.1 (070100): Registro de Activos Fijos - Detalle de los activos fijos revaluados y no revaluados (libro anual).

Usa las mismas filas que el Excel del formato 7.1 (servicios.registro). Supuestos (no validados con el PLE de SUNAT):
- Estructura de 37 campos del Anexo 2 de la R.S. 286-2009/SUNAT (y modificatorias); los campos de libre
  utilización no se envían.
- Catálogo propio (tabla 13 = 9) y sin código de existencia GS1/OSCE (campos 6 y 7 vacíos).
- Todos los activos son «no revaluados» (tabla 18 = 1), en estado «resto de activos» (tabla 19 = 9) y con
  depreciación en línea recta (tabla 20 = 1), igual que el Excel.
- Retiros y/o bajas (campo 18) y su depreciación (campo 31) van en negativo porque disminuyen el saldo; cambiar
  SIGNO_RETIROS si el validador del PLE los pide positivos.
- Marca, modelo y serie vacíos van como «-».
"""
from decimal import Decimal

from core.models import Empresa
from core.utils import txt_response

from . import servicios

CODIGO_LIBRO = '070100'
SIGNO_RETIROS = Decimal('-1')
CATALOGO_PROPIO = '9'        # tabla 13: otros (catálogo propio del contribuyente)
TIPO_ACTIVO = '1'            # tabla 18: no revaluado
ESTADO_ACTIVO = '9'          # tabla 19: resto de activos (ni en desuso ni obsoletos)
METODO_DEPRECIACION = '1'    # tabla 20: línea recta
ESTADO_OPERACION = '1'       # la operación corresponde al periodo

# Campos del formato 7.1 en el orden del PLE (número de campo: contenido)
CAMPOS_7_1 = [
    'periodo',               # 1  AAAA0000 (libro anual)
    'cuo',                   # 2  código único de la operación (código del activo)
    'correlativo',           # 3  A/M/C + número
    'catalogo',              # 4  código del catálogo utilizado (tabla 13)
    'codigo_activo',         # 5  código propio del activo fijo en el catálogo del campo 4
    'catalogo_existencia',   # 6  catálogo del código de existencia (tabla 13), opcional
    'codigo_existencia',     # 7  código de existencia (GS1 / OSCE), opcional
    'tipo_activo',           # 8  tipo de activo fijo (tabla 18)
    'cuenta',                # 9  cuenta contable del activo fijo
    'estado_activo',         # 10 estado del activo fijo (tabla 19)
    'descripcion',           # 11 descripción del activo fijo
    'marca',                 # 12 marca
    'modelo',                # 13 modelo
    'serie',                 # 14 número de serie y/o placa
    'saldo_inicial',         # 15 importe del saldo inicial
    'adquisiciones',         # 16 adquisiciones adicionales
    'mejoras',               # 17 mejoras
    'retiros',               # 18 retiros y/o bajas
    'otros_ajustes',         # 19 otros ajustes en el valor del activo
    'revaluacion_voluntaria',  # 20 revaluación voluntaria
    'revaluacion_reorganizacion',  # 21 revaluación por reorganización de sociedades
    'otras_revaluaciones',   # 22 otras revaluaciones
    'ajuste_inflacion',      # 23 ajuste por inflación del activo
    'fecha_adquisicion',     # 24 dd/mm/aaaa
    'fecha_uso',             # 25 dd/mm/aaaa
    'metodo',                # 26 método de depreciación (tabla 20)
    'documento_cambio',      # 27 n° de documento de autorización del cambio de método
    'porcentaje',            # 28 porcentaje de depreciación
    'dep_anterior',          # 29 depreciación acumulada al cierre del ejercicio anterior
    'dep_ejercicio',         # 30 depreciación del ejercicio sin considerar la revaluación
    'dep_retiros',           # 31 depreciación de los retiros y/o bajas
    'dep_otros_ajustes',     # 32 depreciación de otros ajustes
    'dep_revaluacion_voluntaria',  # 33 depreciación de la revaluación voluntaria
    'dep_revaluacion_reorganizacion',  # 34 depreciación de la revaluación por reorganización
    'dep_otras_revaluaciones',  # 35 depreciación de otras revaluaciones
    'dep_ajuste_inflacion',  # 36 ajuste por inflación de la depreciación
    'estado',                # 37 estado de la operación
]
# largo máximo de los textos (Anexo 2); el resto se recorta
LARGOS = {'cuo': 40, 'codigo_activo': 24, 'descripcion': 40, 'marca': 20, 'modelo': 20, 'serie': 30}


def _texto(valor, campo=None, vacio=''):
    texto = ' '.join(str(valor or '').replace('|', ' ').split())
    if campo in LARGOS:
        texto = texto[:LARGOS[campo]].strip()
    return texto or vacio


def _monto(valor):
    return f'{(valor or Decimal("0")):.2f}'


def nombre_archivo(ruc, anio, con_datos):
    """LE + RUC + AAAA + 00 (mes) + 00 (día) + libro + 00 (oportunidad) + operaciones + contenido + moneda + PLE."""
    return f'LE{ruc}{anio}0000{CODIGO_LIBRO}001{"1" if con_datos else "0"}11.txt'


def linea(anio, f):
    a = f['a']
    cero = _monto(0)
    valores = {
        'periodo': f'{anio}0000', 'cuo': _texto(a.codigo or a.pk, 'cuo'), 'correlativo': 'M1',
        'catalogo': CATALOGO_PROPIO, 'codigo_activo': _texto(a.codigo, 'codigo_activo'),
        'catalogo_existencia': '', 'codigo_existencia': '', 'tipo_activo': TIPO_ACTIVO,
        'cuenta': a.categoria.cuenta_activo.codigo, 'estado_activo': ESTADO_ACTIVO,
        'descripcion': _texto(a.nombre, 'descripcion', '-'), 'marca': _texto(a.marca, 'marca', '-'),
        'modelo': _texto(a.modelo, 'modelo', '-'), 'serie': _texto(a.serie, 'serie', '-'),
        'saldo_inicial': _monto(f['saldo_inicial']), 'adquisiciones': _monto(f['adquisiciones']), 'mejoras': cero,
        'retiros': _monto(SIGNO_RETIROS * f['retiros'] if f['retiros'] else 0), 'otros_ajustes': cero,
        'revaluacion_voluntaria': cero, 'revaluacion_reorganizacion': cero, 'otras_revaluaciones': cero,
        'ajuste_inflacion': cero, 'fecha_adquisicion': a.fecha_adquisicion.strftime('%d/%m/%Y'),
        'fecha_uso': a.fecha_uso.strftime('%d/%m/%Y'), 'metodo': METODO_DEPRECIACION, 'documento_cambio': '',
        'porcentaje': _monto(a.tasa), 'dep_anterior': _monto(f['dep_anterior']),
        'dep_ejercicio': _monto(f['dep_ejercicio']),
        'dep_retiros': _monto(SIGNO_RETIROS * f['dep_retiros'] if f['dep_retiros'] else 0),
        'dep_otros_ajustes': cero, 'dep_revaluacion_voluntaria': cero, 'dep_revaluacion_reorganizacion': cero,
        'dep_otras_revaluaciones': cero, 'dep_ajuste_inflacion': cero, 'estado': ESTADO_OPERACION}
    return '|'.join(valores[c] for c in CAMPOS_7_1) + '|'


def respuesta(anio, filas=None):
    """Archivo TXT del PLE 7.1 del ejercicio."""
    if filas is None:
        filas, _ = servicios.registro(anio)
    lineas = [linea(anio, f) for f in filas]
    return txt_response(nombre_archivo(Empresa.actual().ruc, anio, bool(lineas)), lineas)
