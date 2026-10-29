"""Cómo responde Lucho (sin IA por ahora): solo con fuentes validadas del sistema, nunca con conocimiento general.

1. Reconoce el defecto (elegido por el operador, nombrado en el texto con sus sinónimos o el que ya tiene el caso).
2. Con la ficha vigente de la combinación, recomienda los parámetros que ingeniería asoció al defecto, citando el
   valor de la ficha y su rango; en el diagnóstico de varias vueltas no repite lo que el operario dijo haber ajustado
   (con el valor que dio) y, agotados los parámetros, propone escalar.
3. Agrega los casos resueltos parecidos y los manuales validados (sección y página del archivo leído).
4. Con la orden abierta responde sobre su receta (componentes al estándar para la cantidad de la orden) y su hoja de
   ruta (operaciones, puesto, horas y cuál sigue).
5. Si el texto no es un defecto del catálogo, busca en los manuales y casos del equipo; sin fuente validada lo dice,
   no da valores y ofrece registrar el caso o crear el defecto.
6. Fuera del dominio de planta (o pidiendo datos que no le corresponden) no responde y lo registra; si el mensaje
   mezcla una consulta de planta con otra cosa, responde lo de planta y registra lo otro.

La IA con visión podrá conectarse después en `responder` sin cambiar las pantallas ni el registro de consultas."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.db.models import Q, Sum
from django.urls import reverse

from .models import CasoPlanta, Defecto, Manual

AVISO = 'Puedo equivocarme: verifique en la máquina antes de cambiar un parámetro.'
# temas que Lucho no atiende: datos de otras áreas o ajenos al trabajo
RESTRINGIDOS = {
    'Datos fuera de su alcance': [
        'sueldo', 'salario', 'remuneracion', 'planilla', 'boleta de pago', 'cts', 'gratificacion', 'margen',
        'utilidad', 'precio de venta', 'cuenta bancaria', 'contrasena', 'clave sol', 'costo unitario', 'costo de',
        # LUC-06 / LUC-10: cuánto gana o le pagan a alguien, despidos y renuncias de otros
        'cuanto gana', 'cuanto ganan', 'gana mi', 'gana el', 'gana la', 'le pagan', 'les pagan', 'cuanto paga',
        'cuanto cobra', 'despedid', 'despidieron', 'despido'],
    'Contenido impropio': ['idiota', 'estupido', 'imbecil', 'mierda', 'carajo', 'puta', 'huevon', 'cojudo', 'inutil'],
    'Intento de saltar las reglas': ['ignora tus reglas', 'ignora las reglas', 'ignora tus instrucciones',
                                     'olvida tus reglas', 'olvida tus instrucciones'],
    'Fuera de tema': ['futbol', 'partido de', 'chiste', 'pelicula', 'serie de', 'novia', 'novio', 'horoscopo',
                      'receta de cocina', 'politica', 'elecciones', 'loteria', 'apuesta', 'musica', 'cancion',
                      'clima de', 'noticias', 'carta de renuncia'],
}
# vocabulario de planta (además de los defectos, parámetros, equipos, componentes y operaciones de la orden)
PLANTA = ['maquina', 'molde', 'temperatura', 'presion', 'velocidad', 'defecto', 'pieza', 'parametro', 'arranque',
          'ajuste', 'falla', 'merma', 'calidad', 'lote', 'orden', 'produccion', 'husillo', 'mezcla', 'empaque',
          'extrus', 'inyecc', 'sell', 'proceso', 'producto', 'material', 'equipo', 'linea', 'turno', 'ficha',
          'tiempo de ciclo', 'ciclo', 'enfriamiento', 'dosificacion', 'rebaba', 'burbuja', 'mancha', 'quemad',
          'peso', 'medida', 'color', 'humedad', 'resina', 'granulo', 'motor', 'bomba', 'sensor', 'alarma',
          'atasco', 'ruido', 'vibracion', 'vibra', 'fuga', 'set', 'setpoint', 'rango', 'tolerancia', 'manual',
          # LUC-02: la orden, su receta y su hoja de ruta también son de planta
          'receta', 'hoja de ruta', 'operacion', 'insumo', 'componente', 'ingrediente', 'puesto', 'etapa',
          'que sigue', 'siguiente', 'formula']
RECETA = ['receta', 'insumo', 'componente', 'ingrediente', 'formula', 'cuanto lleva', 'cuanta lleva', 'lleva la',
          'materiales de']
RUTA = ['hoja de ruta', 'operacion', 'que sigue', 'siguiente paso', 'siguiente etapa', 'etapa', 'ruta de']
VACIAS = {'para', 'como', 'cuando', 'donde', 'esta', 'este', 'esto', 'tiene', 'hace', 'sale', 'salen', 'pero',
          'porque', 'sobre', 'entre', 'desde', 'hasta', 'otra', 'otro', 'mucho', 'poco', 'cual', 'cuales', 'tengo',
          'quiero', 'puedo', 'debo', 'favor', 'ayuda', 'ahora', 'tambien', 'luego', 'despues', 'antes', 'sigue',
          'siguen', 'subi', 'baje', 'puse'}
LEET = str.maketrans({'1': 'i', '3': 'e', '0': 'o', '4': 'a', '5': 's', '7': 't', '@': 'a', '$': 's', '!': 'i'})


def plano(texto):
    t = unicodedata.normalize('NFD', (texto or '').lower())
    return ''.join(c for c in t if unicodedata.category(c) != 'Mn')


def normalizado(texto):
    """LUC-06: para buscar lenguaje impropio y datos ajenos: sin tildes, números por letras dentro de las palabras
    («m13rda»), sin letras repetidas («mierdaaa») y sin signos entre letras («m.i.e.r.d.a»)."""
    t = plano(texto)
    t = re.sub(r'\b(?=\w*[a-z])(?=\w*[0-9@$!])[\w@$!]+', lambda m: m.group(0).translate(LEET), t)
    t = re.sub(r'\b\w(?:[.\-_*]\w){2,}\b', lambda m: re.sub(r'[.\-_*]', '', m.group(0)), t)
    return re.sub(r'([a-z])\1{2,}', r'\1', t)


def restriccion(texto):
    """(motivo) si el texto pide algo que Lucho no atiende."""
    t = normalizado(texto)
    for motivo, palabras in RESTRINGIDOS.items():
        if any(re.search(rf'\b{re.escape(p)}', t) for p in palabras):
            return motivo
    return ''


def _partes(texto):
    """El mensaje en frases (para separar lo de planta de lo que no corresponde)."""
    return [p.strip() for p in re.split(r'[.,;:?¿!¡\n]|\by de paso\b|\bde paso\b|\bpor cierto\b', texto or '')
            if p and p.strip()]


def _vocabulario(ctx):
    palabras = list(PLANTA)
    for d in Defecto.objects.filter(activo=True):
        palabras += [plano(p) for p in d.palabras()]
    ficha = ctx.get('ficha')
    if ficha is not None:
        palabras += [plano(s.nombre) for s in ficha.setpoints.all()]
    for clave in ('equipo', 'producto', 'molde', 'material'):
        if ctx.get(clave) is not None:
            palabras.append(plano(ctx[clave].nombre))
    orden = ctx.get('orden')
    if orden is not None:  # LUC-02: componentes de la receta y operaciones de la hoja de ruta
        palabras += [plano(c.producto.nombre).split()[0] for c in orden.consumos.select_related('producto')
                     if c.producto.nombre]
        palabras += [w for h in orden.horas.all() for w in plano(h.descripcion).split() if len(w) > 4]
    return [p for p in palabras if p]


def es_de_planta(texto, ctx):
    t = plano(texto)
    return any(p in t for p in _vocabulario(ctx))


def detectar_defecto(texto, proceso=None):
    t = plano(texto)
    if not t:
        return None
    qs = Defecto.objects.filter(activo=True)
    if proceso is not None:
        qs = qs.filter(proceso=proceso)
    for d in qs:
        if any(plano(p) and plano(p) in t for p in d.palabras()):
            return d
    return None


def _valor(x):
    return f'{x.normalize():f}' if hasattr(x, 'normalize') else str(x)


def _numero(texto):
    try:
        return Decimal(texto.replace(',', '.'))
    except (InvalidOperation, AttributeError):
        return None


def _clave_parametro(nombre):
    """La palabra que identifica al parámetro en lo que escribe el operario («velocidad» del agitador)."""
    for w in plano(nombre).split():
        if len(w) >= 4 and w not in VACIAS:
            return w
    return plano(nombre)


def ajustes_en(texto, ctx, defecto=None):
    """LUC-05: {nombre del parámetro: valor} que el operario dice haber cambiado («subí el tiempo de mezcla a 24
    min»). Solo los parámetros que nombra con un verbo de cambio; el valor es el primer número después del nombre."""
    t = plano(texto)
    if not re.search(r'\b(sub[ií]|baj[eé]|pus[eo]|cambi[eé]|ajust[eé]|aument[eé]|reduj[eo]|dej[eé]|mov[ií]|'
                     r'increment|disminu|modifiqu|regul[eé]|lo puse|la puse|esta en|lo tengo|la tengo)', t):
        return {}
    nombres = []
    ficha = ctx.get('ficha')
    if ficha is not None:
        nombres += [s.nombre for s in ficha.setpoints.all()]
    if defecto is not None:
        nombres += [a.parametro.nombre for a in defecto.ajustes.select_related('parametro')]
    salida = {}
    for nombre in dict.fromkeys(nombres):
        clave = _clave_parametro(nombre)
        m = re.search(rf'\b{re.escape(clave)}\w*', t)
        if m is None:
            continue
        numero = re.search(r'(\d+(?:[.,]\d+)?)', t[m.end():m.end() + 60])
        salida[nombre] = numero.group(1).replace(',', '.') if numero else ''
    return salida


def _recomendaciones(defecto, ficha, ajustados):
    """[(texto, fuente, nombre del parámetro)] según los ajustes del defecto y los valores de la ficha."""
    salida, sin_dato = [], []
    if defecto is None:
        return salida, sin_dato
    por_param = {}
    if ficha is not None:
        for s in ficha.setpoints.select_related('parametro'):
            por_param[s.parametro_id or plano(s.nombre)] = s
            por_param[plano(s.nombre)] = s
    for a in defecto.ajustes.select_related('parametro'):
        s = por_param.get(a.parametro_id) or por_param.get(plano(a.parametro.nombre))
        nombre = a.parametro.nombre
        if plano(nombre) in ajustados:
            continue
        if s is None:
            sin_dato.append(nombre)
            continue
        verbo = {'SUBIR': 'Suba', 'BAJAR': 'Baje', 'REVISAR': 'Revise'}[a.sentido]
        limite = s.maximo if a.sentido == 'SUBIR' else s.minimo if a.sentido == 'BAJAR' else None
        texto = (f'{verbo} {nombre.lower()}: la ficha marca {_valor(s.objetivo)} {s.unidad} '
                 f'(rango {_valor(s.minimo)} a {_valor(s.maximo)} {s.unidad})')
        if limite is not None:
            texto += f'; no pase de {_valor(limite)} {s.unidad}'
        texto += '.' + (f' {a.nota[0].upper()}{a.nota[1:].rstrip(".")}.' if a.nota else '')
        salida.append((texto, f'Ficha {ficha}', nombre))
    return salida, sin_dato


def _casos(defecto, ctx, excluir=None):
    if defecto is None:
        return []
    qs = CasoPlanta.objects.filter(estado='RESUELTO', defecto=defecto).exclude(pk=getattr(excluir, 'pk', None))
    prod, eq = ctx.get('producto'), ctx.get('equipo')
    mismos = qs.filter(Q(producto=prod) | Q(equipo=eq)) if (prod or eq) else qs.none()
    elegidos = list(mismos[:3])
    if len(elegidos) < 3:
        elegidos += list(qs.exclude(pk__in=[c.pk for c in elegidos])[:3 - len(elegidos)])
    return [(c, (prod is not None and c.producto_id == getattr(prod, 'pk', None)) or
             (eq is not None and c.equipo_id == getattr(eq, 'pk', None))) for c in elegidos]


def _manuales_del_contexto(ctx):
    filtro = Q()
    for campo, clave in (('equipo', 'equipo'), ('puesto', 'puesto'), ('material', 'material'),
                         ('proceso', 'proceso'), ('equipo', 'molde')):
        if ctx.get(clave) is not None:
            filtro |= Q(**{campo: ctx[clave]})
    if not filtro:
        return []
    return list(Manual.objects.filter(filtro, activo=True, validado=True).distinct()[:12])


def _citas(manual, palabras):
    """[(texto de la cita)] del manual que mencionan alguna de las palabras: secciones del archivo leído (con su
    página) y líneas de los puntos clave. LUC-08: si nada coincide, no se cita nada (antes salía la primera línea)."""
    citas = []
    for s in manual.secciones or []:
        if any(p in plano(s['titulo'] + ' ' + s['texto']) for p in palabras):
            citas.append(f'{s["titulo"]} (pág. {s["pagina"]}): {s["texto"][:260]}')
    for linea in (manual.contenido or '').splitlines():
        if linea.strip() and any(p in plano(linea) for p in palabras):
            citas.append(linea.strip()[:220])
    return citas[:2]


def _manuales(defecto, ctx):
    """[(manual, citas)] de los manuales validados del contexto que tratan el defecto."""
    if defecto is None:
        return []
    palabras = [plano(p) for p in defecto.palabras() if p]
    salida = []
    for m in _manuales_del_contexto(ctx):
        citas = _citas(m, palabras)
        if citas:
            salida.append((m, citas))
    return salida[:3]


def _terminos(texto):
    """Palabras con contenido del mensaje (raíz de 5 letras para «vibra» / «vibración»)."""
    return {w[:5] for w in re.findall(r'[a-z]{4,}', plano(texto)) if w not in VACIAS}


def _buscar_conocimiento(texto, ctx):
    """LUC-04: el texto no es un defecto del catálogo: se busca en las secciones de los manuales validados del
    equipo (o de todos si el contexto no tiene manuales) y en los casos resueltos del equipo."""
    terminos = _terminos(texto)
    if not terminos:
        return [], []
    minimo = 2 if len(terminos) > 2 else 1
    manuales = _manuales_del_contexto(ctx) or list(Manual.objects.filter(activo=True, validado=True)[:30])
    hallados = []
    for m in manuales:
        for s in m.secciones or []:
            contenido = _terminos(s['titulo'] + ' ' + s['texto'])
            puntaje = len(terminos & contenido)
            if puntaje >= minimo:
                hallados.append((puntaje, m, f'{s["titulo"]} (pág. {s["pagina"]}): {s["texto"][:260]}'))
        for linea in (m.contenido or '').splitlines():
            puntaje = len(terminos & _terminos(linea))
            if linea.strip() and puntaje >= minimo:
                hallados.append((puntaje, m, linea.strip()[:220]))
    hallados.sort(key=lambda x: -x[0])
    casos = []
    if ctx.get('equipo') is not None:
        for c in CasoPlanta.objects.filter(estado='RESUELTO', equipo=ctx['equipo'])[:50]:
            if len(terminos & _terminos(f'{c.problema} {c.causa}')) >= minimo:
                casos.append(c)
    return hallados[:2], casos[:2]


# ---------------------------------------------------------------- la orden: receta y hoja de ruta (LUC-03)
def _cant(x):
    return f'{x:,.3f}'.rstrip('0').rstrip('.')


def _receta(texto, ctx):
    orden = ctx.get('orden')
    consumos = list(orden.consumos.filter(adicional=False).select_related('producto').order_by('-cantidad_plan'))
    if not consumos:
        return [], []
    t = plano(texto)
    version = f' (versión {orden.version.codigo})' if orden.version_id else ''
    lista = f'Receta {orden.lista.codigo}{version}' if orden.lista_id else f'Receta{version}'
    base = f'para {_cant(orden.cantidad)} {orden.producto.unidad} de la orden {orden.numero}'
    nombrados = [c for c in consumos if any(w in t for w in plano(c.producto.nombre).split() if len(w) > 3)]
    partes = []
    for c in nombrados:
        partes.append(f'La {lista.lower()} lleva {_cant(c.cantidad_plan)} {c.producto.unidad} de {c.producto.nombre} '
                      f'{base} (al estándar, con su merma).')
    if not nombrados:
        partes.append(f'{lista} {base}, al estándar:')
        partes += [f'• {c.producto.nombre}: {_cant(c.cantidad_plan)} {c.producto.unidad}' for c in consumos[:15]]
    fuentes = [{'tipo': 'Receta', 'texto': f'{orden.lista.codigo if orden.lista_id else "Receta"} · {orden.numero}',
                'url': reverse('manufactura:orden', args=[orden.pk])}]
    return partes, fuentes


def _hoja_ruta(ctx):
    orden = ctx.get('orden')
    horas = list(orden.horas.select_related('centro').order_by('secuencia', 'id'))
    if not horas:
        return [], []
    hechas = {h['hora']: h['b'] for h in orden.avances.values('hora').annotate(b=Sum('cantidad_buena'))}
    siguiente = next((h for h in horas if (hechas.get(h.pk) or 0) < orden.cantidad), None)
    partes = []
    if siguiente is not None:
        partes.append(f'Sigue la operación {siguiente.descripcion} en {siguiente.centro} '
                      f'({_cant(siguiente.horas_plan)} h al estándar).')
    else:
        partes.append('Todas las operaciones de la hoja de ruta tienen reportada la cantidad de la orden.')
    partes.append(f'Hoja de ruta de la orden {orden.numero}:')
    for h in horas:
        avance = hechas.get(h.pk) or 0
        estado = ' ✓' if avance >= orden.cantidad else (f' (van {_cant(avance)})' if avance else '')
        partes.append(f'• {h.descripcion} · {h.centro} · {_cant(h.horas_plan)} h{estado}')
    return partes, [{'tipo': 'Hoja de ruta', 'texto': f'{orden.numero}',
                     'url': reverse('manufactura:orden', args=[orden.pk])}]


def _de_la_orden(texto, ctx):
    """(partes, fuentes) si la pregunta es sobre la receta o la hoja de ruta de la orden abierta."""
    if ctx.get('orden') is None:
        return [], []
    t = plano(texto)
    partes, fuentes = [], []
    orden = ctx['orden']
    componente = any(w in t for c in orden.consumos.select_related('producto')
                     for w in plano(c.producto.nombre).split() if len(w) > 3)
    if any(p in t for p in RECETA) or (componente and re.search(r'\b(cuant[oa]s?|lleva|necesit)', t)):
        p, f = _receta(texto, ctx)
        partes += p
        fuentes += f
    if any(p in t for p in RUTA):
        p, f = _hoja_ruta(ctx)
        partes += p
        fuentes += f
    return partes, fuentes


def _fuera(motivo):
    detalle = ('Esa información no está dentro de lo que puedo consultar por usted.'
               if motivo == 'Datos fuera de su alcance' else
               'Solo atiendo consultas sobre la planta y el proceso que está trabajando.')
    return {'texto': f'{detalle} Si tiene un problema en la máquina o con el producto, cuénteme o mándeme la foto '
                     f'del defecto.', 'fuentes': [], 'cobertura': 'FUERA', 'fuera_de_dominio': True,
            'motivo': motivo, 'defecto': None, 'recomendados': [], 'ajustes': {}}


def responder(texto, ctx, defecto=None, ajustados=(), caso=None, con_foto=False):
    """dict con la respuesta de Lucho: texto, fuentes [{tipo, texto, url}], cobertura, fuera_de_dominio, motivo,
    defecto, los parámetros recomendados y los que el operario dice haber ajustado en este mensaje."""
    texto = (texto or '').strip()
    motivo = restriccion(texto)
    nota_fuera = ''
    consulta = texto
    if motivo:
        # LUC-06: un mensaje que mezcla planta con otra cosa: se responde lo de planta y se registra lo otro
        de_planta = [p for p in _partes(texto) if not restriccion(p) and (
            es_de_planta(p, ctx) or detectar_defecto(p, ctx.get('proceso')))]
        if not de_planta and defecto is None:
            return _fuera(motivo)
        consulta = '. '.join(de_planta)
        nota_fuera = ('Sobre lo otro que pidió: esa información no está dentro de lo que puedo consultar por usted.'
                      if motivo == 'Datos fuera de su alcance' else
                      'Sobre lo otro: solo atiendo consultas de planta.')
    elif texto and defecto is None and not getattr(caso, 'defecto_id', None) and not es_de_planta(texto, ctx):
        return _fuera('Fuera de tema')  # dentro de un caso con defecto, «ya hice eso y sigue» es seguimiento
    respuesta = _responder_planta(consulta, ctx, defecto, ajustados, caso, con_foto)
    if nota_fuera:
        respuesta['texto'] = f'{respuesta["texto"]}\n{nota_fuera}'
        respuesta['motivo'] = motivo  # queda en la bitácora de uso inadecuado (sin cortar la ayuda de planta)
    return respuesta


def _responder_planta(texto, ctx, defecto, ajustados, caso, con_foto):
    ficha = ctx.get('ficha')
    orden_partes, orden_fuentes = _de_la_orden(texto, ctx)
    defecto = defecto or detectar_defecto(texto, ctx.get('proceso'))
    if defecto is None and caso is not None and caso.defecto_id:
        defecto = caso.defecto  # LUC-05: el defecto del caso se mantiene en todas las vueltas
    nuevos = ajustes_en(texto, ctx, defecto)
    nombres_ajustados = sorted({a for a in ajustados if a} | set(nuevos))
    ajustados = {plano(a) for a in nombres_ajustados}
    partes, fuentes = [], []
    if defecto is None:
        if orden_partes:  # LUC-03: pregunta sobre la receta o la hoja de ruta de la orden
            return {'texto': '\n'.join(orden_partes), 'fuentes': orden_fuentes, 'cobertura': 'PRECEDENTE',
                    'fuera_de_dominio': False, 'motivo': '', 'defecto': None, 'recomendados': [], 'ajustes': nuevos}
        hallados, casos = _buscar_conocimiento(texto, ctx)
        if hallados or casos:  # LUC-04: lo explica un manual o un caso del equipo aunque no esté en el catálogo
            partes.append('No es un defecto del catálogo, pero encontré esto en fuentes validadas:')
            for _, m, cita in hallados:
                partes.append(f'• {m.titulo}' + (f' ({m.fabricante.nombre})' if m.fabricante_id else '') + f': {cita}')
                fuentes.append({'tipo': 'Manual', 'texto': m.titulo, 'url': m.get_absolute_url()})
            for c in casos:
                partes.append(f'• Caso {c.numero}: causa: {c.causa[:120]}; acción: {c.accion[:160]}.')
                fuentes.append({'tipo': 'Caso', 'texto': c.numero, 'url': c.get_absolute_url()})
            partes.append('Si no se resuelve, registre el caso; ingeniería puede crear el defecto en el catálogo.')
            partes.append(AVISO)
            return {'texto': '\n'.join(partes), 'fuentes': fuentes, 'cobertura': 'PARECIDO',
                    'fuera_de_dominio': False, 'motivo': '', 'defecto': None, 'recomendados': [], 'ajustes': nuevos}
        opciones = Defecto.objects.filter(activo=True)
        if ctx.get('proceso') is not None:
            opciones = opciones.filter(proceso=ctx['proceso'])
        nombres = ', '.join(d.nombre for d in opciones[:12])
        partes.append(('Guardé la foto. ' if con_foto else '') +
                      'Aún no sé qué defecto es: elíjalo de la lista' + (f' ({nombres})' if nombres else '') +
                      ' o descríbalo en una línea. Tampoco encontré algo parecido en los manuales ni en los casos del '
                      'equipo: si no está en la lista, registre el caso para que ingeniería cree el defecto.')
        if ficha is not None:
            partes.append(f'Tengo a la mano la ficha vigente {ficha} de {ficha.combinacion}.')
            fuentes.append({'tipo': 'Ficha', 'texto': f'{ficha} · {ficha.combinacion}', 'url': ficha.get_absolute_url()})
        return {'texto': '\n'.join(partes), 'fuentes': fuentes, 'cobertura': 'PRECEDENTE' if ficha else 'SIN',
                'fuera_de_dominio': False, 'motivo': '', 'defecto': None, 'recomendados': [], 'ajustes': nuevos}
    partes.append(f'Veo {defecto.nombre.lower()}' + (f' ({defecto.descripcion})' if defecto.descripcion else '') +
                  (f' en {ctx["producto"].nombre}' if ctx.get('producto') else '') +
                  (f', {ctx["equipo"].nombre}' if ctx.get('equipo') else '') + '.')
    if nuevos:
        anotados = []
        for nombre, valor in nuevos.items():
            s = next((x for x in (ficha.setpoints.all() if ficha else []) if plano(x.nombre) == plano(nombre)), None)
            v = _numero(valor)
            linea = f'{nombre.lower()} {valor} {s.unidad if s else ""}'.strip() if valor else nombre.lower()
            if s is not None and v is not None:
                linea += ' (dentro del rango de la ficha)' if s.dentro(v) else \
                    f' (FUERA del rango {_valor(s.minimo)} a {_valor(s.maximo)}: vuelva al rango)'
            anotados.append(linea)
        partes.append('Anoté: ' + '; '.join(anotados) + '.')
    if ajustados:
        partes.append('Mantenga como los dejó: ' + ', '.join(n.lower() for n in nombres_ajustados) +
                      ' (no los revierta).')
    recs, sin_dato = _recomendaciones(defecto, ficha, ajustados)
    recomendados = []
    if recs:
        partes.append('Le sugiero, en este orden y uno a la vez:')
        for n, (linea, fuente, nombre) in enumerate(recs[:3], 1):
            partes.append(f'{n}. {linea}')
            recomendados.append(nombre)
        fuentes.append({'tipo': 'Ficha', 'texto': f'{ficha} · {ficha.combinacion}', 'url': ficha.get_absolute_url()})
    elif ficha is None:
        partes.append('No hay una ficha de parámetros vigente para esta combinación: no le doy valores. Pida a '
                      'ingeniería que cargue y apruebe la ficha.')
    elif ajustados and defecto.ajustes.exists():
        # LUC-05: ya se movieron todos los parámetros que la ficha asocia al defecto
        partes.append(f'Ya se ajustaron los parámetros que la ficha asocia a {defecto.nombre.lower()} '
                      f'({", ".join(n.lower() for n in nombres_ajustados)}). Si el defecto sigue, no hay otro ajuste '
                      f'validado: escale a su supervisor o a ingeniería y registre el caso.')
    if sin_dato and ficha is not None:  # LUC-09: sin ficha no se habla de lo que «la ficha no recoge»
        partes.append('La ficha no recoge ' + ', '.join(n.lower() for n in sin_dato) +
                      ': ahí no tengo un valor validado; consúltelo con ingeniería.')
    casos = _casos(defecto, ctx, excluir=caso)
    if casos:
        partes.append('Casos resueltos parecidos:')
        for c, mismo in casos:
            partes.append(f'• {c.numero} ({c.creado:%d/%m/%Y}{", mismo producto o equipo" if mismo else ""}): '
                          f'causa: {c.causa[:120]}; acción: {c.accion[:160]}.')
            fuentes.append({'tipo': 'Caso', 'texto': f'{c.numero} · {c.defecto}', 'url': c.get_absolute_url()})
    manuales = _manuales(defecto, ctx)
    if manuales:
        partes.append('En los manuales:')
        for m, citas in manuales:
            for cita in citas:
                partes.append(f'• {m.titulo}' + (f' ({m.fabricante.nombre})' if m.fabricante_id else '') +
                              f': {cita}')
            fuentes.append({'tipo': 'Manual', 'texto': m.titulo, 'url': m.get_absolute_url()})
    if orden_partes:
        partes += orden_partes
        fuentes += orden_fuentes
    agotado = bool(ficha and ajustados and not recs and defecto.ajustes.exists())
    if not (recs or casos or manuales or agotado):
        partes.append('No tengo una fuente validada para este defecto (ni ficha, ni casos, ni manuales). No voy a '
                      'inventar un valor: registre el caso para que ingeniería lo valide o escale a su supervisor.')
        cobertura = 'SIN'
    elif recs or agotado or any(m for _, m in casos):
        cobertura = 'PRECEDENTE'
    else:
        cobertura = 'PARECIDO'
    partes.append(AVISO)
    return {'texto': '\n'.join(partes), 'fuentes': fuentes, 'cobertura': cobertura, 'fuera_de_dominio': False,
            'motivo': '', 'defecto': defecto, 'recomendados': recomendados, 'ajustes': nuevos}


def registrar_consulta(usuario, ctx, texto, respuesta, caso=None, con_foto=False):
    """Guarda la consulta (auditoría) y, si es uso inadecuado reiterado, avisa al supervisor."""
    from .models import Consulta
    c = Consulta.objects.create(
        usuario=usuario if usuario and usuario.is_authenticated else None, caso=caso, orden=ctx.get('orden'),
        equipo=ctx.get('equipo'), defecto=respuesta.get('defecto'), pregunta=(texto or '')[:4000], con_foto=con_foto,
        respuesta=respuesta['texto'], fuentes=respuesta['fuentes'], recomendados=respuesta['recomendados'],
        cobertura=respuesta['cobertura'], ajustes=respuesta.get('ajustes') or {},
        fuera_de_dominio=respuesta['fuera_de_dominio'], motivo_uso=respuesta['motivo'][:120])
    if c.motivo_uso:
        _uso_inadecuado(c)
    return c


def _uso_inadecuado(consulta):
    """Desde el umbral de la empresa (consultas fuera de tema en 7 días, sin las descartadas como falso positivo):
    queda en la bitácora con la pregunta, la respuesta y la orden o el caso, y el supervisor lo ve en la campana y en
    «Uso inadecuado»."""
    from datetime import timedelta

    from django.utils import timezone

    from core.auditoria import registrar

    from .models import ConfigAsistente, Consulta
    cfg = ConfigAsistente.actual()
    desde = timezone.now() - timedelta(days=7)
    recientes = Consulta.objects.filter(usuario=consulta.usuario, creado__gte=desde, descartada=False).exclude(
        motivo_uso='')
    if consulta.usuario is None or recientes.count() < max(cfg.umbral_uso_indebido, 1):
        return False
    recientes.filter(supervisor_avisado=False).update(supervisor_avisado=True)
    registrar('MODIFICAR', consulta, {'uso_inadecuado': [None, consulta.motivo_uso],
                                      'pregunta': [None, consulta.pregunta[:200]],
                                      'respuesta': [None, consulta.respuesta[:200]],
                                      'orden_o_caso': [None, str(consulta.caso or consulta.orden or '')]},
              f'Uso inadecuado del asistente: {recientes.count()} consultas fuera de tema en 7 días')
    return True
