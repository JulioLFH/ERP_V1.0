"""Cómo responde Lucho (sin IA por ahora): solo con fuentes validadas del sistema, nunca con conocimiento general.

1. Reconoce el defecto (elegido por el operador o nombrado en el texto, con sus sinónimos).
2. Con la ficha vigente de la combinación, recomienda los parámetros que ingeniería asoció al defecto, citando el
   valor de la ficha y su rango; en el diagnóstico de varias vueltas no repite lo ya ajustado y avisa no revertirlo.
3. Agrega los casos resueltos parecidos y los manuales validados del equipo, puesto, material o proceso.
4. Sin fuente validada lo dice, no da valores y ofrece registrar el caso o escalar.
5. Fuera del dominio de planta (o pidiendo datos que no le corresponden) no responde y lo registra.

La IA con visión podrá conectarse después en `responder` sin cambiar las pantallas ni el registro de consultas."""
import re
import unicodedata

from django.db.models import Q

from .models import CasoPlanta, Defecto, Manual

AVISO = 'Puedo equivocarme: verifique en la máquina antes de cambiar un parámetro.'
# temas que Lucho no atiende: datos de otras áreas o ajenos al trabajo
RESTRINGIDOS = {
    'Datos fuera de su alcance': ['sueldo', 'salario', 'remuneracion', 'planilla', 'boleta de pago', 'cts',
                                  'gratificacion', 'margen', 'utilidad', 'precio de venta', 'cuenta bancaria',
                                  'contrasena', 'clave sol'],
    'Contenido impropio': ['idiota', 'estupido', 'imbecil', 'mierda', 'carajo', 'puta', 'huevon', 'cojudo'],
    'Fuera de tema': ['futbol', 'partido de', 'chiste', 'pelicula', 'serie de', 'novia', 'novio', 'horoscopo',
                      'receta de cocina', 'politica', 'elecciones', 'loteria', 'apuesta', 'musica', 'cancion',
                      'clima de', 'noticias'],
}
# vocabulario de planta (además de los defectos, parámetros y equipos cargados)
PLANTA = ['maquina', 'molde', 'temperatura', 'presion', 'velocidad', 'defecto', 'pieza', 'parametro', 'arranque',
          'ajuste', 'falla', 'merma', 'calidad', 'lote', 'orden', 'produccion', 'husillo', 'mezcla', 'empaque',
          'extrus', 'inyecc', 'sell', 'proceso', 'producto', 'material', 'equipo', 'linea', 'turno', 'ficha',
          'tiempo de ciclo', 'ciclo', 'enfriamiento', 'dosificacion', 'rebaba', 'burbuja', 'mancha', 'quemad',
          'peso', 'medida', 'color', 'humedad', 'resina', 'granulo', 'motor', 'bomba', 'sensor', 'alarma',
          'atasco', 'ruido', 'vibracion', 'fuga', 'set', 'setpoint', 'rango', 'tolerancia', 'manual']


def plano(texto):
    t = unicodedata.normalize('NFD', (texto or '').lower())
    return ''.join(c for c in t if unicodedata.category(c) != 'Mn')


def restriccion(texto):
    """(motivo) si el texto pide algo que Lucho no atiende."""
    t = plano(texto)
    for motivo, palabras in RESTRINGIDOS.items():
        if any(re.search(rf'\b{re.escape(p)}', t) for p in palabras):
            return motivo
    return ''


def es_de_planta(texto, ctx):
    t = plano(texto)
    palabras = list(PLANTA)
    for d in Defecto.objects.filter(activo=True):
        palabras += [plano(p) for p in d.palabras()]
    ficha = ctx.get('ficha')
    if ficha is not None:
        palabras += [plano(s.nombre) for s in ficha.setpoints.all()]
    for clave in ('equipo', 'producto', 'molde'):
        if ctx.get(clave) is not None:
            palabras.append(plano(ctx[clave].nombre))
    return any(p and p in t for p in palabras)


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


def _manuales(defecto, ctx):
    filtro = Q()
    for campo, clave in (('equipo', 'equipo'), ('puesto', 'puesto'), ('material', 'material'),
                         ('proceso', 'proceso'), ('equipo', 'molde')):
        if ctx.get(clave) is not None:
            filtro |= Q(**{campo: ctx[clave]})
    if not filtro:
        return []
    manuales = list(Manual.objects.filter(filtro, activo=True, validado=True).distinct()[:6])
    if defecto is not None:  # primero los que mencionan el defecto
        palabras = [plano(p) for p in defecto.palabras()]
        manuales.sort(key=lambda m: not any(p in plano(m.contenido + ' ' + m.titulo) for p in palabras))
    return manuales[:3]


def _extracto(manual, defecto):
    texto = manual.contenido.strip()
    if defecto is not None:
        for linea in texto.splitlines():
            if any(plano(p) in plano(linea) for p in defecto.palabras()):
                return linea.strip()[:220]
    return texto.splitlines()[0][:220] if texto else ''


def responder(texto, ctx, defecto=None, ajustados=(), caso=None, con_foto=False):
    """dict con la respuesta de Lucho: texto, fuentes [{tipo, texto, url}], cobertura, fuera_de_dominio, motivo,
    defecto y los parámetros recomendados (para la siguiente vuelta)."""
    texto = (texto or '').strip()
    motivo = restriccion(texto)
    if not motivo and texto and defecto is None and not es_de_planta(texto, ctx):
        motivo = 'Fuera de tema'
    if motivo:
        detalle = ('Esa información no está dentro de lo que puedo consultar por usted.'
                   if motivo == 'Datos fuera de su alcance' else
                   'Solo atiendo consultas sobre la planta y el proceso que está trabajando.')
        return {'texto': f'{detalle} Si tiene un problema en la máquina o con el producto, cuénteme o mándeme la foto '
                         f'del defecto.', 'fuentes': [], 'cobertura': 'FUERA', 'fuera_de_dominio': True,
                'motivo': motivo, 'defecto': None, 'recomendados': []}
    defecto = defecto or detectar_defecto(texto, ctx.get('proceso'))
    ficha = ctx.get('ficha')
    nombres_ajustados = sorted({a for a in ajustados if a})
    ajustados = {plano(a) for a in nombres_ajustados}
    partes, fuentes = [], []
    if defecto is None:
        opciones = Defecto.objects.filter(activo=True)
        if ctx.get('proceso') is not None:
            opciones = opciones.filter(proceso=ctx['proceso'])
        nombres = ', '.join(d.nombre for d in opciones[:12])
        partes.append(('Guardé la foto. ' if con_foto else '') +
                      'Aún no sé qué defecto es: elíjalo de la lista' + (f' ({nombres})' if nombres else '') +
                      ' o descríbalo en una línea.')
        if ficha is not None:
            partes.append(f'Tengo a la mano la ficha vigente {ficha} de {ficha.combinacion}.')
            fuentes.append({'tipo': 'Ficha', 'texto': f'{ficha} · {ficha.combinacion}', 'url': ficha.get_absolute_url()})
        return {'texto': '\n'.join(partes), 'fuentes': fuentes, 'cobertura': 'PRECEDENTE' if ficha else 'SIN',
                'fuera_de_dominio': False, 'motivo': '', 'defecto': None, 'recomendados': []}
    partes.append(f'Veo {defecto.nombre.lower()}' + (f' ({defecto.descripcion})' if defecto.descripcion else '') +
                  (f' en {ctx["producto"].nombre}' if ctx.get('producto') else '') +
                  (f', {ctx["equipo"].nombre}' if ctx.get('equipo') else '') + '.')
    if ajustados:
        partes.append('Mantenga como los dejó: ' + ', '.join(n.lower() for n in nombres_ajustados) +
                      ' (no los revierta); probemos con otro parámetro.')
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
    if sin_dato:
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
        for m in manuales:
            extracto = _extracto(m, defecto)
            partes.append(f'• {m.titulo}' + (f' ({m.fabricante.nombre})' if m.fabricante_id else '') +
                          (f': {extracto}' if extracto else '.'))
            fuentes.append({'tipo': 'Manual', 'texto': m.titulo, 'url': m.get_absolute_url()})
    if not (recs or casos or manuales):
        partes.append('No tengo una fuente validada para este defecto (ni ficha, ni casos, ni manuales). No voy a '
                      'inventar un valor: registre el caso para que ingeniería lo valide o escale a su supervisor.')
        cobertura = 'SIN'
    elif recs or any(m for _, m in casos):
        cobertura = 'PRECEDENTE'
    else:
        cobertura = 'PARECIDO'
    partes.append(AVISO)
    return {'texto': '\n'.join(partes), 'fuentes': fuentes, 'cobertura': cobertura, 'fuera_de_dominio': False,
            'motivo': '', 'defecto': defecto, 'recomendados': recomendados}


def registrar_consulta(usuario, ctx, texto, respuesta, caso=None, con_foto=False):
    """Guarda la consulta (auditoría) y, si es uso inadecuado reiterado, avisa al supervisor."""
    from .models import Consulta
    c = Consulta.objects.create(
        usuario=usuario if usuario and usuario.is_authenticated else None, caso=caso, orden=ctx.get('orden'),
        equipo=ctx.get('equipo'), defecto=respuesta.get('defecto'), pregunta=(texto or '')[:4000], con_foto=con_foto,
        respuesta=respuesta['texto'], fuentes=respuesta['fuentes'], recomendados=respuesta['recomendados'],
        cobertura=respuesta['cobertura'],
        fuera_de_dominio=respuesta['fuera_de_dominio'], motivo_uso=respuesta['motivo'][:120])
    if c.fuera_de_dominio:
        _uso_inadecuado(c)
    return c


def _uso_inadecuado(consulta):
    """Desde el umbral de la empresa (consultas fuera de tema en 7 días): queda en la bitácora y el supervisor lo ve
    en la campana y en «Uso inadecuado»."""
    from datetime import timedelta

    from django.utils import timezone

    from core.auditoria import registrar

    from .models import ConfigAsistente, Consulta
    cfg = ConfigAsistente.actual()
    desde = timezone.now() - timedelta(days=7)
    recientes = Consulta.objects.filter(usuario=consulta.usuario, fuera_de_dominio=True, creado__gte=desde)
    if consulta.usuario is None or recientes.count() < max(cfg.umbral_uso_indebido, 1):
        return False
    recientes.filter(supervisor_avisado=False).update(supervisor_avisado=True)
    registrar('MODIFICAR', consulta, {'uso_inadecuado': [None, consulta.motivo_uso]},
              f'Uso inadecuado del asistente: {recientes.count()} consultas fuera de tema en 7 días')
    return True
