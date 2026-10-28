"""Reglas del asistente de planta: fichas de parámetros (validación, revisión y aprobación por otra persona), casos de
planta enganchados a la orden (merma, parada, no conformidad y aviso de mantenimiento) e importación de fichas."""
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.permisos import puede

from .models import CasoPlanta, FichaParametros, ParametroProceso, Proceso, Setpoint

D0 = Decimal('0')
MAQUINAS = ('MAQUINA', 'MAQ_PESADA', 'HERRAMIENTA', 'OTRO')


class ErrorAsistente(Exception):
    pass


def _num(valor, campo):
    if valor in (None, ''):
        raise ErrorAsistente(f'{campo}: falta el valor.')
    try:
        return Decimal(str(valor).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        raise ErrorAsistente(f'{campo}: «{valor}» no es un número.')


def _nombre(u):
    return (u.get_full_name() or u.username) if u else ''


# ---------------------------------------------------------------- fichas de parámetros
def limpiar_setpoints(filas):
    """[{nombre, unidad, minimo, objetivo, maximo, nota, parametro}] → validados (filas vacías se ignoran).
    Mínimo ≤ objetivo ≤ máximo, unidad obligatoria y sin parámetros repetidos."""
    salida, vistos = [], set()
    for n, f in enumerate(filas, 1):
        nombre = (f.get('nombre') or '').strip()
        if not nombre and not any(str(f.get(k) or '').strip() for k in ('minimo', 'objetivo', 'maximo')):
            continue
        if not nombre:
            raise ErrorAsistente(f'Fila {n}: falta el nombre del parámetro.')
        unidad = (f.get('unidad') or '').strip()
        if not unidad:
            raise ErrorAsistente(f'{nombre}: la unidad es obligatoria.')
        mn, obj, mx = (_num(f.get(k), f'{nombre} ({k})') for k in ('minimo', 'objetivo', 'maximo'))
        if not mn <= obj <= mx:
            raise ErrorAsistente(f'{nombre}: debe cumplirse mínimo ≤ objetivo ≤ máximo ({mn:g} ≤ {obj:g} ≤ {mx:g}).')
        if nombre.lower() in vistos:
            raise ErrorAsistente(f'{nombre}: el parámetro está repetido.')
        vistos.add(nombre.lower())
        salida.append({'nombre': nombre[:80], 'unidad': unidad[:15], 'minimo': mn, 'objetivo': obj, 'maximo': mx,
                       'nota': (f.get('nota') or '').strip()[:200], 'parametro': f.get('parametro'),
                       'orden': n * 10})
    return salida


def vigente(producto, equipo, proceso=None, molde=None):
    """La ficha vigente de la combinación (con el molde si se indica; si no, la del equipo)."""
    if producto is None or equipo is None:
        return None
    qs = FichaParametros.objects.filter(estado='VIGENTE', producto=producto, equipo=equipo)
    if proceso is not None:
        qs = qs.filter(proceso=proceso)
    if molde is not None:
        con_molde = qs.filter(molde=molde).order_by('-revision').first()
        if con_molde:
            return con_molde
    return qs.order_by('molde_id', '-revision').first()


def _misma_combinacion(ficha):
    return FichaParametros.objects.filter(producto_id=ficha.producto_id, equipo_id=ficha.equipo_id,
                                          molde_id=ficha.molde_id, proceso_id=ficha.proceso_id)


@transaction.atomic
def guardar_ficha(ficha, filas, usuario):
    """Crea o edita una ficha en borrador (o rechazada) con sus parámetros."""
    if ficha.pk and ficha.estado not in ('BORRADOR', 'RECHAZADA'):
        raise ErrorAsistente('Solo se edita una ficha en borrador o rechazada: para cambiar una vigente, cree una '
                             'nueva revisión.')
    if ficha.molde_id and ficha.molde.tipo != 'MOLDE':
        raise ErrorAsistente('El molde debe ser un equipo de tipo «Molde / herramental de proceso».')
    setpoints = limpiar_setpoints(filas)
    if not setpoints:
        raise ErrorAsistente('Agregue al menos un parámetro con su mínimo, objetivo y máximo.')
    if not ficha.pk:
        ficha.revision = (_misma_combinacion(ficha).aggregate(m=Max('revision'))['m'] or 0) + 1
    if ficha.estado == 'RECHAZADA':
        ficha.estado, ficha.motivo_rechazo = 'BORRADOR', ''
    ficha.save()
    ficha.setpoints.all().delete()
    por_nombre = {p.nombre.lower(): p for p in ParametroProceso.objects.filter(proceso_id=ficha.proceso_id)}
    for s in setpoints:
        Setpoint.objects.create(ficha=ficha, parametro=por_nombre.get(s['nombre'].lower()), nombre=s['nombre'],
                                unidad=s['unidad'], minimo=s['minimo'], objetivo=s['objetivo'], maximo=s['maximo'],
                                orden=s['orden'], nota=s['nota'])
    return ficha


def aprobadores_fichas(excepto=None):
    from colaboracion.servicios import usuarios_internos
    return [u for u in usuarios_internos() if (excepto is None or u.pk != excepto.pk)
            and not u.is_superuser and puede(u, 'asistente.aprobar_ficha')][:10]


def _avisar(de, para, texto, url):
    """Aviso por chat directo (Conversaciones): nunca detiene el flujo."""
    if de is None or para is None or de.pk == para.pk:
        return
    try:
        from colaboracion.avisos import _escribir
        _escribir(de, para, texto, url)
    except Exception:  # noqa: BLE001
        pass


@transaction.atomic
def proponer(ficha, usuario):
    if ficha.estado not in ('BORRADOR', 'RECHAZADA'):
        raise ErrorAsistente('La ficha ya fue enviada a aprobación.')
    if not ficha.setpoints.exists():
        raise ErrorAsistente('La ficha no tiene parámetros.')
    ficha.estado, ficha.propuesta_por, ficha.propuesta_en = 'POR_APROBAR', usuario, timezone.now()
    ficha.motivo_rechazo = ''
    ficha.save(update_fields=['estado', 'propuesta_por', 'propuesta_en', 'motivo_rechazo'])
    for u in aprobadores_fichas(excepto=usuario):
        _avisar(usuario, u, f'Solicito su aprobación para la ficha de parámetros {ficha} ({ficha.combinacion}).',
                ficha.get_absolute_url())
    return ficha


@transaction.atomic
def aprobar(ficha, usuario):
    """Publica el estándar: otra persona que quien lo propuso (segregación); la vigente anterior queda retirada."""
    if ficha.estado != 'POR_APROBAR':
        raise ErrorAsistente('Solo se aprueba una ficha por aprobar.')
    otros = get_user_model().objects.filter(is_active=True).exclude(pk=usuario.pk).exists()
    if ficha.propuesta_por_id == usuario.pk and otros:
        raise ErrorAsistente('Quien propone la ficha no aprueba su propio estándar: la aprueba otra persona de '
                             'ingeniería o calidad.')
    _misma_combinacion(ficha).filter(estado='VIGENTE').exclude(pk=ficha.pk).update(estado='RETIRADA')
    ficha.estado, ficha.aprobada_por, ficha.aprobada_en = 'VIGENTE', usuario, timezone.now()
    ficha.vigente_desde = timezone.localdate()
    ficha.save(update_fields=['estado', 'aprobada_por', 'aprobada_en', 'vigente_desde'])
    _avisar(usuario, ficha.propuesta_por, f'Aprobé la ficha de parámetros {ficha}: ya es el estándar vigente de '
                                          f'{ficha.combinacion}.', ficha.get_absolute_url())
    return ficha


@transaction.atomic
def rechazar(ficha, usuario, motivo):
    if ficha.estado != 'POR_APROBAR':
        raise ErrorAsistente('Solo se rechaza una ficha por aprobar.')
    if len((motivo or '').strip()) < 5:
        raise ErrorAsistente('Indique el motivo del rechazo.')
    ficha.estado, ficha.motivo_rechazo = 'RECHAZADA', motivo.strip()[:250]
    ficha.save(update_fields=['estado', 'motivo_rechazo'])
    _avisar(usuario, ficha.propuesta_por, f'Rechacé la ficha de parámetros {ficha}.\nMotivo: {ficha.motivo_rechazo}',
            ficha.get_absolute_url())
    return ficha


@transaction.atomic
def nueva_revision(base, usuario, valores=None, caso=None, nota=''):
    """Copia la ficha en una revisión nueva. valores = {nombre del parámetro: valor real}: el objetivo pasa a ese valor
    y el rango se amplía si hace falta. Con caso: queda propuesta (por aprobar) desde el caso resuelto."""
    valores = {k.lower(): v for k, v in (valores or {}).items()}
    nueva = FichaParametros(producto=base.producto, equipo=base.equipo, molde=base.molde, proceso=base.proceso,
                            nota=nota or base.nota, origen_caso=caso)
    filas = []
    for s in base.setpoints.all():
        v = valores.get(s.nombre.lower())
        mn, obj, mx = s.minimo, s.objetivo, s.maximo
        if v is not None:
            obj, mn, mx = v, min(mn, v), max(mx, v)
        filas.append({'nombre': s.nombre, 'unidad': s.unidad, 'minimo': mn, 'objetivo': obj, 'maximo': mx,
                      'nota': s.nota})
    guardar_ficha(nueva, filas, usuario)
    if caso is not None:
        proponer(nueva, usuario)
    return nueva


# ---------------------------------------------------------------- contexto desde la orden
def ordenes_abiertas():
    from produccion.models import OrdenProduccion
    return (OrdenProduccion.objects.filter(estado__in=OrdenProduccion.ABIERTAS)
            .select_related('producto').order_by('-fecha', '-id'))


def contexto_de_orden(orden):
    """Producto, equipos, proceso, lote, material y ficha vigente de la orden abierta (sin teclear)."""
    if orden is None:
        return {}
    centros = [h.centro for h in orden.horas.select_related('centro')]
    from produccion.models import Equipo
    equipos = list(Equipo.objects.filter(centro__in=centros, activo=True, tipo__in=MAQUINAS))
    moldes = list(Equipo.objects.filter(centro__in=centros, activo=True, tipo='MOLDE'))
    proceso = Proceso.objects.filter(activo=True, puestos__in=centros).first()
    equipo = equipos[0] if len(equipos) == 1 else None
    ficha = vigente(orden.producto, equipo, proceso) if equipo else None
    if ficha is None and equipos:
        ficha = next((f for f in (vigente(orden.producto, e, proceso) for e in equipos) if f), None)
        if ficha is not None:
            equipo = ficha.equipo
    proceso = proceso or (ficha.proceso if ficha else None)
    declaracion = orden.declaraciones.exclude(lote='').order_by('-numero').first()
    material = (orden.consumos.filter(adicional=False).select_related('producto')
                .order_by('-cantidad_plan').first())
    return {'orden': orden, 'producto': orden.producto, 'equipos': equipos, 'equipo': equipo,
            'moldes': moldes, 'molde': moldes[0] if len(moldes) == 1 else (ficha.molde if ficha else None),
            'proceso': proceso, 'ficha': ficha, 'lote': declaracion.lote if declaracion else orden.numero,
            'material': material.producto if material else None}


# ---------------------------------------------------------------- casos de planta
def abrir_caso(usuario, ctx, defecto=None, problema='', foto=None):
    from core.sustentos import adjuntar
    caso = CasoPlanta.objects.create(
        orden=ctx.get('orden'), producto=ctx.get('producto'), equipo=ctx.get('equipo'), molde=ctx.get('molde'),
        proceso=ctx.get('proceso') or (defecto.proceso if defecto else None), material=ctx.get('material'),
        lote=(ctx.get('lote') or '')[:60], ficha=ctx.get('ficha'), defecto=defecto, problema=(problema or '')[:2000],
        autor=usuario if usuario and usuario.is_authenticated else None)
    if foto:
        adjuntar(caso, foto, usuario, 'Foto del defecto')
    return caso


def costo_unitario(caso):
    o = caso.orden
    if o is not None:
        for valor in (o.costo_unitario if o.estado == 'TERMINADA' else None, o.costo_estandar_unit):
            if valor:
                return valor
    return caso.producto.costo_promedio if caso.producto_id else D0


def _hora_de(orden, equipo):
    horas = list(orden.horas.select_related('centro'))
    if equipo is not None:
        for h in horas:
            if h.centro_id == equipo.centro_id:
                return h
    return horas[0] if horas else None


@transaction.atomic
def resolver(caso, usuario, resuelto, causa='', accion='', resultado='', piezas=None, minutos=0, defecto=None,
             parametros=None, fotos=(), parada=None, abrir_nc=False, avisar_mantenimiento=False,
             prioridad='NORMAL'):
    """Cierra el caso. Resuelto: defecto, causa y acción obligatorios. Las piezas perdidas van a la orden como merma
    (costo real del defecto en soles), la parada al registro de paradas (OEE), y si se pide se abre la no
    conformidad y el aviso de mantenimiento. Parámetros reales fuera de la ficha: se propone una revisión nueva."""
    from core.sustentos import ErrorSustento, adjuntar, validar_imagen
    if caso.estado != 'PENDIENTE':
        raise ErrorAsistente('El caso ya está cerrado.')
    defecto = defecto or caso.defecto
    causa, accion, resultado = (causa or '').strip(), (accion or '').strip(), (resultado or '').strip()
    if resuelto and (defecto is None or len(causa) < 3 or len(accion) < 3):
        raise ErrorAsistente('Para dar el caso por resuelto indique el defecto, la causa y la acción realizada.')
    piezas = _num(piezas, 'Piezas perdidas') if piezas not in (None, '') else D0
    if piezas < 0:
        raise ErrorAsistente('Las piezas perdidas no pueden ser negativas.')
    reales = []
    for p in parametros or []:
        if (p.get('nombre') or '').strip() and str(p.get('valor') or '').strip():
            reales.append({'nombre': p['nombre'].strip()[:80], 'valor': _num(p['valor'], p['nombre'])})
    for f in fotos:
        try:
            validar_imagen(f)
        except ErrorSustento as exc:
            raise ErrorAsistente(f'Foto de parámetros: {exc}')
    caso.defecto, caso.causa, caso.accion, caso.resultado = defecto, causa, accion, resultado
    caso.piezas_perdidas, caso.minutos = piezas, max(int(minutos or 0), 0)
    caso.costo = (piezas * costo_unitario(caso)).quantize(Decimal('0.01'))
    caso.parametros_reales = [{'nombre': r['nombre'], 'valor': str(r['valor'])} for r in reales]
    caso.estado = 'RESUELTO' if resuelto else 'NO_RESUELTO'
    caso.resuelto_en = timezone.now()
    o = caso.orden
    from produccion.avanzado import ErrorAvanzado, registrar_avance, registrar_parada
    from produccion.models import OrdenProduccion
    if piezas > 0 and o is not None and o.estado in OrdenProduccion.ABIERTAS:
        hora = _hora_de(o, caso.equipo)
        try:
            registrar_avance(o, hora, D0, piezas, D0, _nombre(usuario), f'Caso {caso.numero}: '
                             f'{defecto or "defecto"}'[:200], usuario)
            caso.merma_registrada = True
        except ErrorAvanzado as exc:
            raise ErrorAsistente(f'Merma a la orden: {exc}')
    if parada and parada.get('motivo') and parada.get('minutos'):
        if o is None:
            raise ErrorAsistente('Para registrar la parada el caso debe estar ligado a una orden de producción.')
        try:
            caso.parada = registrar_parada(o, _hora_de(o, caso.equipo), parada['motivo'],
                                           Decimal(str(parada['minutos'])), usuario, equipo=caso.equipo)
        except ErrorAvanzado as exc:
            raise ErrorAsistente(f'Parada: {exc}')
    if abrir_nc:
        from calidad.models import NoConformidad
        caso.no_conformidad = NoConformidad.objects.create(
            origen='PRODUCCION', proceso=(str(caso.proceso) if caso.proceso else '')[:120],
            descripcion=f'Caso de planta {caso.numero}: {defecto or "defecto"} en {caso.producto or ""} '
                        f'({caso.equipo or "sin equipo"}){f", lote {caso.lote}" if caso.lote else ""}. '
                        f'{caso.problema}'.strip()[:2000],
            causa_raiz=causa, detectado_por=_nombre(usuario)[:120],
            creado_por=usuario if usuario and usuario.is_authenticated else None)
    if avisar_mantenimiento:
        if caso.equipo is None:
            raise ErrorAsistente('Para avisar a mantenimiento el caso debe tener su equipo.')
        from produccion.mantenimiento import crear_aviso
        caso.aviso = crear_aviso(caso.equipo, f'Caso {caso.numero}: {defecto or "falla"}. {causa}'.strip(),
                                 prioridad, False, usuario)
    caso.save()
    for f in fotos:
        adjuntar(caso, f, usuario, 'Foto de parámetros')
    propuesta = None
    if resuelto and caso.ficha_id and reales:
        fuera = {}
        por_nombre = {s.nombre.lower(): s for s in caso.ficha.setpoints.all()}
        for r in reales:
            s = por_nombre.get(r['nombre'].lower())
            if s is not None and not s.dentro(r['valor']):
                fuera[s.nombre] = r['valor']
        if fuera:
            propuesta = nueva_revision(caso.ficha, usuario, fuera, caso=caso,
                                       nota=f'Propuesta desde el caso {caso.numero}: {", ".join(fuera)} fuera de la '
                                            f'ficha {caso.ficha}.')
    return caso, propuesta


# ---------------------------------------------------------------- importación desde Excel
COLUMNAS = ['Producto (código)', 'Equipo (código)', 'Molde (código, opcional)', 'Proceso (código)', 'Parámetro',
            'Unidad', 'Mínimo', 'Objetivo', 'Máximo', 'Nota']


def importar(filas, usuario):
    """filas = [(n, dict por columna)]: agrupa por producto × equipo × molde × proceso y crea una ficha por grupo,
    enviada a aprobación. Valida todo antes de grabar: si una fila falla, no se graba nada."""
    from core.models import Producto
    from produccion.models import Equipo
    grupos, errores = {}, []
    for n, f in filas:
        try:
            prod = Producto.objects.filter(codigo=str(f.get('Producto (código)') or '').strip()).first()
            eq = Equipo.objects.filter(codigo=str(f.get('Equipo (código)') or '').strip()).first()
            cod_molde = str(f.get('Molde (código, opcional)') or '').strip()
            molde = Equipo.objects.filter(codigo=cod_molde).first() if cod_molde else None
            proc = Proceso.objects.filter(codigo=str(f.get('Proceso (código)') or '').strip()).first()
            faltan = [c for c, v in (('producto', prod), ('equipo', eq), ('proceso', proc)) if v is None]
            if cod_molde and molde is None:
                faltan.append('molde')
            if faltan:
                raise ErrorAsistente(f'no existe el {", ".join(faltan)} indicado')
            fila = limpiar_setpoints([{'nombre': f.get('Parámetro'), 'unidad': f.get('Unidad'),
                                       'minimo': f.get('Mínimo'), 'objetivo': f.get('Objetivo'),
                                       'maximo': f.get('Máximo'), 'nota': f.get('Nota')}])
            if fila:
                grupos.setdefault((prod, eq, molde, proc), []).extend(fila)
        except ErrorAsistente as exc:
            errores.append(f'Fila {n}: {exc}')
    if errores:
        raise ErrorAsistente(' · '.join(errores[:15]) + (f' (y {len(errores) - 15} más)' if len(errores) > 15 else ''))
    if not grupos:
        raise ErrorAsistente('El archivo no tiene parámetros.')
    creadas = []
    with transaction.atomic():
        for (prod, eq, molde, proc), setpoints in grupos.items():
            ficha = guardar_ficha(FichaParametros(producto=prod, equipo=eq, molde=molde, proceso=proc,
                                                  nota='Importada desde Excel'), setpoints, usuario)
            creadas.append(proponer(ficha, usuario))
    return creadas
