"""Procesos de ejemplo para arrancar (inyección, extrusión, mezclado y empaque): sus parámetros, su catálogo de
defectos y qué parámetro se revisa ante cada defecto. La empresa los ajusta en Configuración › Procesos."""
from django.db import transaction

from .models import AjusteDefecto, Defecto, ParametroProceso, Proceso

# código, nombre, [(parámetro, unidad)], [(código, defecto, cómo se reconoce, sinónimos, [(parámetro, sentido, nota)])]
PROCESOS = [
    ('INY', 'Moldeo por inyección',
     [('Temperatura de barril', '°C'), ('Temperatura de molde', '°C'), ('Velocidad de inyección', '%'),
      ('Presión de inyección', 'bar'), ('Presión de mantenimiento', 'bar'), ('Tiempo de mantenimiento', 's'),
      ('Contrapresión', 'bar'), ('Tiempo de enfriamiento', 's'), ('Dosificación', 'mm')],
     [('REB', 'Rebaba', 'material que sale por la línea de partición', 'flash, rebabas',
       [('Presión de mantenimiento', 'BAJAR', ''), ('Velocidad de inyección', 'BAJAR', ''),
        ('Temperatura de barril', 'BAJAR', '')]),
      ('QUE', 'Quemado', 'marcas negras o amarillas al final del llenado', 'quemadura, efecto diesel, quemada',
       [('Velocidad de inyección', 'BAJAR', 'el aire atrapado se calienta al comprimirse'),
        ('Temperatura de barril', 'BAJAR', ''), ('Contrapresión', 'BAJAR', '')]),
      ('RCH', 'Rechupado', 'hundimientos en zonas gruesas', 'rechupe, hundimiento, sink',
       [('Presión de mantenimiento', 'SUBIR', 'compacta mejor la masa'), ('Tiempo de mantenimiento', 'SUBIR', ''),
        ('Tiempo de enfriamiento', 'SUBIR', '')]),
      ('DCO', 'Disparo corto', 'pieza incompleta', 'pieza incompleta, falta de llenado, short shot',
       [('Dosificación', 'SUBIR', ''), ('Velocidad de inyección', 'SUBIR', ''),
        ('Temperatura de barril', 'SUBIR', '')]),
      ('BUR', 'Burbujas', 'vacíos o burbujas dentro de la pieza', 'burbuja, vacío, aire',
       [('Contrapresión', 'SUBIR', ''), ('Temperatura de barril', 'REVISAR', 'revise también la humedad del material')]),
      ('MAN', 'Manchas', 'rayas plateadas o manchas en la superficie', 'mancha, rayas, plateado',
       [('Temperatura de barril', 'REVISAR', 'material húmedo: revise el secado'),
        ('Velocidad de inyección', 'BAJAR', '')])]),
    ('EXT', 'Extrusión',
     [('Temperatura de cabezal', '°C'), ('Velocidad de husillo', 'rpm'), ('Velocidad de jalado', 'm/min'),
      ('Presión de cabezal', 'bar'), ('Temperatura del agua de enfriamiento', '°C')],
     [('GEL', 'Grumos o geles', 'puntos sin fundir en el producto', 'grumos, geles, puntos',
       [('Temperatura de cabezal', 'SUBIR', ''), ('Velocidad de husillo', 'BAJAR', '')]),
      ('ESP', 'Espesor irregular', 'el espesor o el diámetro varía', 'espesor, diámetro irregular',
       [('Velocidad de jalado', 'REVISAR', ''), ('Presión de cabezal', 'REVISAR', '')]),
      ('RUG', 'Superficie rugosa', 'acabado áspero o mate', 'rugosa, piel de tiburón',
       [('Temperatura de cabezal', 'SUBIR', ''), ('Velocidad de husillo', 'BAJAR', '')])]),
    ('MEZ', 'Mezclado y formulación',
     [('Tiempo de mezcla', 'min'), ('Velocidad del agitador', 'rpm'), ('Temperatura', '°C'),
      ('Dosificación de aditivo', '%')],
     [('GRU', 'Grumos', 'aglomerados sin dispersar', 'grumo, aglomerado',
       [('Tiempo de mezcla', 'SUBIR', ''), ('Velocidad del agitador', 'SUBIR', '')]),
      ('COL', 'Color fuera de estándar', 'tono distinto al patrón', 'color, tono',
       [('Dosificación de aditivo', 'REVISAR', '')]),
      ('VIS', 'Viscosidad fuera de rango', 'muy espeso o muy fluido', 'viscosidad, espeso, fluido',
       [('Temperatura', 'REVISAR', ''), ('Tiempo de mezcla', 'REVISAR', '')])]),
    ('EMP', 'Empaque y sellado',
     [('Temperatura de sellado', '°C'), ('Tiempo de sellado', 's'), ('Presión de sellado', 'bar'),
      ('Velocidad de línea', 'unid/min')],
     [('SDE', 'Sello débil o fuga', 'el sello se abre o deja pasar', 'fuga, sello débil, mal sellado',
       [('Temperatura de sellado', 'SUBIR', ''), ('Tiempo de sellado', 'SUBIR', ''),
        ('Presión de sellado', 'SUBIR', '')]),
      ('SQU', 'Sello quemado', 'sello deformado o perforado', 'sello quemado, perforado',
       [('Temperatura de sellado', 'BAJAR', ''), ('Tiempo de sellado', 'BAJAR', '')]),
      ('BPE', 'Bajo peso', 'unidades por debajo del peso declarado', 'bajo peso, peso bajo',
       [('Velocidad de línea', 'REVISAR', 'revise también la dosificadora')])]),
]


@transaction.atomic
def cargar():
    """Crea los procesos de ejemplo que falten (no toca los que ya existen). Devuelve cuántos creó."""
    creados = 0
    for codigo, nombre, parametros, defectos in PROCESOS:
        if Proceso.objects.filter(codigo=codigo).exists():
            continue
        p = Proceso.objects.create(codigo=codigo, nombre=nombre)
        por_nombre = {}
        for n, (param, unidad) in enumerate(parametros, 1):
            por_nombre[param] = ParametroProceso.objects.create(proceso=p, nombre=param, unidad=unidad, orden=n * 10)
        for cod, defecto, como, sinonimos, ajustes in defectos:
            d = Defecto.objects.create(proceso=p, codigo=cod, nombre=defecto, descripcion=como, sinonimos=sinonimos)
            for prioridad, (param, sentido, nota) in enumerate(ajustes, 1):
                AjusteDefecto.objects.create(defecto=d, parametro=por_nombre[param], sentido=sentido,
                                             prioridad=prioridad, nota=nota)
        creados += 1
    return creados
