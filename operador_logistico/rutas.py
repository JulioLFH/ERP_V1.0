"""v1.117.15: rutas de recojo en Lima y Callao.

La sugerencia toma las solicitudes de recojo pendientes del día (Asignado, sin ruta, con fecha pedida hasta ese día o
sin fecha), las agrupa por zona (Norte, Centro, Este, Sur, Callao) según el distrito del almacén del cliente, las ordena
por cercanía saliendo del almacén de Go (vecino más cercano) y abre otra ruta al pasar la capacidad de la unidad (kg,
bultos o paradas). Logística la ajusta a mano y la asigna a un operador, que la ve en el celular con el botón de
Google Maps.

Dónde está cada almacén, sin servicios de mapas pagados:
1. las coordenadas de la ficha logística del cliente (se aprenden solas en el primer recojo con el GPS del celular);
2. el GPS de un recojo anterior del mismo cliente;
3. el centro aproximado del distrito (ubigeo del almacén): alcanza para la zona y un orden aproximado."""
import math
from decimal import Decimal
from urllib.parse import quote

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from .models import ClienteLogistico, ConfigOperador, SolicitudRecojo, RutaRecojo


class ErrorRuta(Exception):
    pass


# ubigeo -> (distrito, zona, latitud, longitud aproximadas del centro del distrito)
DISTRITOS = {
    '150101': ('Lima', 'Centro', -12.0464, -77.0428), '150102': ('Ancón', 'Norte', -11.7739, -77.1764),
    '150103': ('Ate', 'Este', -12.0257, -76.9200), '150104': ('Barranco', 'Sur', -12.1494, -77.0216),
    '150105': ('Breña', 'Centro', -12.0597, -77.0503), '150106': ('Carabayllo', 'Norte', -11.8900, -77.0300),
    '150107': ('Chaclacayo', 'Este', -11.9750, -76.7700), '150108': ('Chorrillos', 'Sur', -12.1700, -77.0150),
    '150109': ('Cieneguilla', 'Este', -12.0700, -76.7800), '150110': ('Comas', 'Norte', -11.9400, -77.0600),
    '150111': ('El Agustino', 'Este', -12.0450, -76.9950), '150112': ('Independencia', 'Norte', -11.9900, -77.0500),
    '150113': ('Jesús María', 'Centro', -12.0750, -77.0450), '150114': ('La Molina', 'Este', -12.0800, -76.9300),
    '150115': ('La Victoria', 'Centro', -12.0700, -77.0200), '150116': ('Lince', 'Centro', -12.0850, -77.0350),
    '150117': ('Los Olivos', 'Norte', -11.9700, -77.0700), '150118': ('Lurigancho', 'Este', -11.9800, -76.8500),
    '150119': ('Lurín', 'Sur', -12.2750, -76.8700), '150120': ('Magdalena del Mar', 'Centro', -12.0910, -77.0700),
    '150121': ('Pueblo Libre', 'Centro', -12.0750, -77.0650), '150122': ('Miraflores', 'Centro', -12.1200, -77.0300),
    '150123': ('Pachacámac', 'Sur', -12.2300, -76.8600), '150124': ('Pucusana', 'Sur', -12.4800, -76.8000),
    '150125': ('Puente Piedra', 'Norte', -11.8650, -77.0750), '150126': ('Punta Hermosa', 'Sur', -12.3350, -76.8250),
    '150127': ('Punta Negra', 'Sur', -12.3650, -76.7950), '150128': ('Rímac', 'Centro', -12.0300, -77.0300),
    '150129': ('San Bartolo', 'Sur', -12.3900, -76.7800), '150130': ('San Borja', 'Centro', -12.1000, -76.9950),
    '150131': ('San Isidro', 'Centro', -12.0970, -77.0360),
    '150132': ('San Juan de Lurigancho', 'Este', -11.9800, -77.0000),
    '150133': ('San Juan de Miraflores', 'Sur', -12.1600, -76.9700), '150134': ('San Luis', 'Este', -12.0750, -76.9950),
    '150135': ('San Martín de Porres', 'Norte', -12.0000, -77.0850),
    '150136': ('San Miguel', 'Centro', -12.0770, -77.0900), '150137': ('Santa Anita', 'Este', -12.0450, -76.9700),
    '150138': ('Santa María del Mar', 'Sur', -12.4100, -76.7750), '150139': ('Santa Rosa', 'Norte', -11.8000, -77.1650),
    '150140': ('Santiago de Surco', 'Sur', -12.1400, -76.9900), '150141': ('Surquillo', 'Centro', -12.1150, -77.0150),
    '150142': ('Villa El Salvador', 'Sur', -12.2150, -76.9400),
    '150143': ('Villa María del Triunfo', 'Sur', -12.1650, -76.9350),
    '070101': ('Callao', 'Callao', -12.0500, -77.1200), '070102': ('Bellavista', 'Callao', -12.0600, -77.1050),
    '070103': ('Carmen de la Legua', 'Callao', -12.0450, -77.0950), '070104': ('La Perla', 'Callao', -12.0700, -77.1150),
    '070105': ('La Punta', 'Callao', -12.0720, -77.1630), '070106': ('Ventanilla', 'Callao', -11.8750, -77.1300),
    '070107': ('Mi Perú', 'Callao', -11.8550, -77.1250),
}
ZONAS = ['Norte', 'Callao', 'Centro', 'Este', 'Sur']
SIN_ZONA = 'Fuera de Lima o sin distrito'
MAX_GOOGLE = 10  # paradas que Google Maps abre en un solo recorrido (origen + 9 puntos intermedios + destino)


def distancia_km(a, b):
    """Distancia en línea recta (haversine) entre dos (latitud, longitud)."""
    lat1, lon1, lat2, lon2 = (math.radians(float(x)) for x in (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def distrito(ubigeo):
    fila = DISTRITOS.get((ubigeo or '').strip())
    return fila[0] if fila else ''


def zona_de(ubigeo):
    fila = DISTRITOS.get((ubigeo or '').strip())
    return fila[1] if fila else SIN_ZONA


def base():
    """(latitud, longitud) del almacén de Go o None."""
    cfg = ConfigOperador.actual()
    if cfg.base_latitud is not None and cfg.base_longitud is not None:
        return (cfg.base_latitud, cfg.base_longitud)
    return None


def punto(sol, perfil=None):
    """(latitud, longitud, fuente) del almacén de la solicitud, o (None, None, '')."""
    perfil = perfil if perfil is not None else ClienteLogistico.objects.filter(tercero_id=sol.cliente_id).first()
    if perfil and perfil.almacen_latitud is not None and perfil.almacen_longitud is not None:
        return perfil.almacen_latitud, perfil.almacen_longitud, 'ficha'
    anterior = SolicitudRecojo.objects.filter(cliente_id=sol.cliente_id, latitud__isnull=False,
                                              longitud__isnull=False).order_by('-atendida_en', '-pk').first()
    if anterior is not None:
        return anterior.latitud, anterior.longitud, 'recojo anterior'
    fila = DISTRITOS.get((sol.almacen_ubigeo or (perfil.almacen_ubigeo if perfil else '') or '').strip())
    if fila:
        return Decimal(str(fila[2])), Decimal(str(fila[3])), 'distrito'
    return None, None, ''


def carga(sol):
    """(bultos, kg) de la solicitud: sus guías (del XML) o los bultos que indicó el cliente."""
    t = sol.guias.aggregate(b=Sum('bultos'), k=Sum('peso'))
    bultos = t['b'] or sol.bultos or 0
    return int(bultos), t['k'] or Decimal('0')


def detalle(sol, perfil=None):
    """Lo que la pantalla y la hoja muestran de cada parada."""
    lat, lon, fuente = punto(sol, perfil)
    bultos, kg = carga(sol)
    ubigeo = sol.almacen_ubigeo or (perfil.almacen_ubigeo if perfil else '')
    return {'sol': sol, 'lat': lat, 'lon': lon, 'fuente': fuente, 'bultos': bultos, 'kg': kg,
            'distrito': distrito(ubigeo), 'zona': zona_de(ubigeo)}


def _ordenar(items, desde):
    """Vecino más cercano desde el punto de partida (o desde la parada más al norte). Las paradas sin ubicación van al
    final, en el orden en que llegaron."""
    con = [i for i in items if i['lat'] is not None]
    sin = [i for i in items if i['lat'] is None]
    if not con:
        return sin
    actual = desde or (max(con, key=lambda i: i['lat'])['lat'], max(con, key=lambda i: i['lat'])['lon'])
    orden = []
    while con:
        cerca = min(con, key=lambda i: distancia_km(actual, (i['lat'], i['lon'])))
        con.remove(cerca)
        orden.append(cerca)
        actual = (cerca['lat'], cerca['lon'])
    return orden + sin


def _partir(items, cfg):
    """Corta la lista ordenada en rutas que respetan la capacidad de la unidad."""
    tramos, actual, kg, bultos = [], [], Decimal('0'), 0
    for i in items:
        pasa = actual and (
            (cfg.capacidad_kg and kg + i['kg'] > cfg.capacidad_kg) or
            (cfg.capacidad_bultos and bultos + i['bultos'] > cfg.capacidad_bultos) or
            (cfg.max_paradas and len(actual) >= cfg.max_paradas))
        if pasa:
            tramos.append(actual)
            actual, kg, bultos = [], Decimal('0'), 0
        actual.append(i)
        kg += i['kg']
        bultos += i['bultos']
    if actual:
        tramos.append(actual)
    return tramos


def pendientes(fecha):
    """Solicitudes por recoger ese día que aún no están en una ruta."""
    from django.db.models import Q
    return SolicitudRecojo.objects.filter(estado='ASIGNADO', ruta__isnull=True) \
        .filter(Q(fecha_recojo__isnull=True) | Q(fecha_recojo__lte=fecha)).select_related('cliente').order_by('creado')


@transaction.atomic
def sugerir(fecha, usuario=None, rehacer=False):
    """Arma las rutas sugeridas del día con lo pendiente. rehacer: deshace antes las rutas del día que siguen en
    «Armando» (las que ya salieron no se tocan). Devuelve las rutas creadas."""
    if rehacer:
        for r in RutaRecojo.objects.filter(fecha=fecha, estado='ARMANDO'):
            eliminar(r)
    cfg = ConfigOperador.actual()
    perfiles = {p.tercero_id: p for p in ClienteLogistico.objects.all()}
    por_zona = {}
    for sol in pendientes(fecha):
        d = detalle(sol, perfiles.get(sol.cliente_id))
        por_zona.setdefault(d['zona'], []).append(d)
    desde = base()
    creadas = []
    for zona in ZONAS + [SIN_ZONA]:
        items = por_zona.get(zona)
        if not items:
            continue
        tramos = _partir(_ordenar(items, desde), cfg)
        for n, tramo in enumerate(tramos, 1):
            ruta = RutaRecojo.objects.create(fecha=fecha, zona=zona if len(tramos) == 1 else f'{zona} {n}',
                                             sugerida=True, creado_por=usuario if _es_usuario(usuario) else None)
            for orden, i in enumerate(tramo, 1):
                SolicitudRecojo.objects.filter(pk=i['sol'].pk).update(ruta=ruta, orden_ruta=orden)
            creadas.append(ruta)
    return creadas


def _es_usuario(usuario):
    return usuario is not None and getattr(usuario, 'is_authenticated', False)


def _editable(ruta):
    if ruta.estado == 'CERRADA':
        raise ErrorRuta(f'La ruta {ruta.numero} ya está cerrada.')


def nueva(fecha, usuario=None, zona='Manual'):
    return RutaRecojo.objects.create(fecha=fecha, zona=zona, creado_por=usuario if _es_usuario(usuario) else None)


def agregar(sol, ruta):
    """Pone la solicitud al final de la ruta (o la pasa de otra ruta a esta)."""
    _editable(ruta)
    if sol.estado != 'ASIGNADO':
        raise ErrorRuta(f'La solicitud {sol.numero} ya no está por recoger.')
    anterior = sol.ruta
    sol.ruta = ruta
    sol.orden_ruta = (ruta.paradas.exclude(pk=sol.pk).aggregate(m=Max('orden_ruta'))['m'] or 0) + 1
    if ruta.operador_id:
        sol.operador_id = ruta.operador_id
    sol.save(update_fields=['ruta', 'orden_ruta', 'operador'])
    if anterior is not None and anterior.pk != ruta.pk:
        renumerar(anterior)


def quitar(sol):
    """La saca de su ruta: vuelve a «sin ruta» para otra ruta u otro día."""
    ruta = sol.ruta
    if ruta is None:
        return
    _editable(ruta)
    sol.ruta, sol.orden_ruta = None, 0
    sol.save(update_fields=['ruta', 'orden_ruta'])
    renumerar(ruta)


def renumerar(ruta):
    for n, s in enumerate(ruta.paradas.order_by('orden_ruta', 'pk'), 1):
        if s.orden_ruta != n:
            SolicitudRecojo.objects.filter(pk=s.pk).update(orden_ruta=n)


def mover(sol, paso):
    """Sube (-1) o baja (+1) la parada un lugar dentro de su ruta."""
    ruta = sol.ruta
    if ruta is None:
        return
    _editable(ruta)
    renumerar(ruta)
    paradas = list(ruta.paradas.order_by('orden_ruta', 'pk'))
    i = next(n for n, s in enumerate(paradas) if s.pk == sol.pk)
    j = i + paso
    if 0 <= j < len(paradas):
        paradas[i], paradas[j] = paradas[j], paradas[i]
        for n, s in enumerate(paradas, 1):
            SolicitudRecojo.objects.filter(pk=s.pk).update(orden_ruta=n)


def ordenar_por_cercania(ruta):
    """Vuelve a ordenar las paradas pendientes de la ruta por cercanía (las ya recogidas quedan primero)."""
    _editable(ruta)
    perfiles = {p.tercero_id: p for p in ClienteLogistico.objects.all()}
    hechas = list(ruta.paradas.exclude(estado='ASIGNADO').order_by('orden_ruta', 'pk'))
    items = [detalle(s, perfiles.get(s.cliente_id)) for s in ruta.paradas.filter(estado='ASIGNADO')]
    desde = base()
    if hechas and hechas[-1].latitud is not None:
        desde = (hechas[-1].latitud, hechas[-1].longitud)
    for n, s in enumerate(hechas + [i['sol'] for i in _ordenar(items, desde)], 1):
        SolicitudRecojo.objects.filter(pk=s.pk).update(orden_ruta=n)


@transaction.atomic
def asignar(ruta, operador=None, placa=''):
    """Operador y placa de la ruta; sus paradas quedan asignadas a ese operador (lo ve en el celular)."""
    _editable(ruta)
    ruta.operador, ruta.placa = operador, (placa or '').strip().upper()[:15]
    ruta.save(update_fields=['operador', 'placa'])
    ruta.paradas.filter(estado='ASIGNADO').update(operador=operador)


def iniciar(ruta):
    _editable(ruta)
    if ruta.operador_id is None:
        raise ErrorRuta('Asigne el operador antes de iniciar la ruta.')
    if not ruta.paradas.exists():
        raise ErrorRuta('La ruta no tiene paradas.')
    ruta.estado = 'EN_CURSO'
    ruta.save(update_fields=['estado'])


@transaction.atomic
def eliminar(ruta):
    """Deshace la ruta (solo si sigue en «Armando»): sus solicitudes vuelven a «sin ruta»."""
    if ruta.estado != 'ARMANDO':
        raise ErrorRuta(f'La ruta {ruta.numero} ya salió: no se puede deshacer.')
    ruta.paradas.update(ruta=None, orden_ruta=0)
    ruta.delete()


def al_recoger(solicitud):
    """Después de un recojo: guarda la ubicación del almacén si la ficha no la tiene y cierra la ruta si ya no le
    quedan paradas por recoger."""
    if solicitud.latitud is not None and solicitud.longitud is not None:
        ClienteLogistico.objects.filter(tercero_id=solicitud.cliente_id, almacen_latitud__isnull=True).update(
            almacen_latitud=solicitud.latitud, almacen_longitud=solicitud.longitud)
    ruta = solicitud.ruta
    if ruta is not None and ruta.estado != 'CERRADA' and not ruta.paradas.filter(estado='ASIGNADO').exists():
        ruta.estado = 'CERRADA'
        ruta.save(update_fields=['estado'])


def _lugar(d):
    if d['lat'] is not None and d['fuente'] != 'distrito':
        return f"{d['lat']},{d['lon']}"
    sol = d['sol']
    return ', '.join(x for x in (sol.almacen, d['distrito'], 'Lima, Perú') if x)


def enlace_google(ruta, detalles=None):
    """Recorrido en Google Maps: sale del almacén de Go, pasa por las paradas pendientes en orden y vuelve. Google
    abre hasta 10 paradas: con más, el enlace lleva las primeras."""
    detalles = detalles if detalles is not None else [detalle(s) for s in ruta.paradas_en_orden()]
    puntos = [_lugar(d) for d in detalles if d['sol'].estado == 'ASIGNADO'][:MAX_GOOGLE]
    if not puntos:
        return ''
    cfg = ConfigOperador.actual()
    b = base()
    origen = f'{b[0]},{b[1]}' if b else (cfg.base_direccion or '')
    if origen:
        destino, intermedios = origen, puntos[:9]
    else:
        destino, intermedios = puntos[-1], puntos[:-1][:8]
    url = f'https://www.google.com/maps/dir/?api=1&travelmode=driving&destination={quote(destino)}'
    if origen:
        url += f'&origin={quote(origen)}'
    if intermedios:
        url += f"&waypoints={quote('|'.join(intermedios))}"
    return url


def ruta_del_operador(usuario, fecha=None):
    """La ruta de hoy del operador (en curso o armada y asignada a él) para el celular."""
    fecha = fecha or timezone.localdate()
    return RutaRecojo.objects.filter(operador=usuario, fecha=fecha, estado__in=('EN_CURSO', 'ARMANDO')) \
        .order_by('-estado', 'id').first()
