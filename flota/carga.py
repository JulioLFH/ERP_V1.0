"""Carga masiva de flota (FLO-08): equipos y vehículos, componentes, planes preventivos, lecturas de odómetro u
horómetro y documentos con vencimiento. Usa la misma mecánica de core/carga_masiva.py (validar sin grabar y cargar
todo o nada)."""
from datetime import date
from decimal import Decimal

from django.utils import timezone

from core.carga_masiva import ErrorFila, SI_NO, _CONTEXTO, _dec, _elegir, _opciones, _txt, normalizar
from core.utils import a_fecha

D0 = Decimal('0')
TIPOS = ('equipos', 'componentes', 'planes_mantenimiento', 'lecturas', 'documentos_flota')

DEFINICIONES = [
    ('equipos', {
        'titulo': 'Equipos y vehículos', 'icono': 'bi-truck-flatbed', 'modulos': ['flota', 'logistica', 'mantenimiento', 'manufactura'],
        'descripcion': 'Maestro único de máquinas, vehículos, montacargas y herramientas. Un vehículo con placa '
                       'aparece en las guías de remisión. Con «Actualizar existentes» se cambian los que ya existen.',
        'columnas': [
            ('codigo', 'Código', True, 'CAM-01', 'Único'), ('nombre', 'Nombre', True, 'Camión Volvo FH', ''),
            ('tipo', 'Tipo', True, 'Vehículo', 'Máquina, Vehículo, Montacargas, Maquinaria pesada, Herramienta u Otro'),
            ('estado', 'Estado', False, 'Operativo', 'Operativo, En mantenimiento, Inoperativo o De baja'),
            ('placa', 'Placa', False, 'ABC123', 'Obligatoria para vehículos'),
            ('marca_modelo', 'Marca / modelo', False, 'Volvo FH 460', ''),
            ('serie', 'Chasis / N° de serie', False, '9BVRG40D', ''), ('anio', 'Año', False, '2021', ''),
            ('combustible', 'Combustible', False, 'Diésel', 'Diésel, Gasolina, GLP, GNV o Eléctrico'),
            ('medidor', 'Medidor', False, 'km', 'km (odómetro), horas (horómetro) o vacío'),
            ('rendimiento', 'Rendimiento esperado', False, '9.5', 'km u horas por galón'),
            ('puesto', 'Puesto de trabajo (código)', False, '', 'Para máquinas de planta'),
            ('centro_costo', 'Centro de costo (código)', False, '', 'Vacío = el del puesto'),
            ('activo_fijo', 'Activo fijo (código)', False, '', 'Para su depreciación'),
            ('responsable', 'Responsable (DNI)', False, '', 'Trabajador de la planilla'),
            ('ubicacion', 'Ubicación', False, '', ''),
            ('presta', 'Se presta a clientes', False, 'No', 'Sí o No'),
        ]}),
    ('componentes', {
        'titulo': 'Componentes de equipos', 'icono': 'bi-diagram-3', 'modulos': ['flota', 'logistica', 'mantenimiento', 'manufactura'],
        'descripcion': 'Estructura por niveles (sistema › subsistema › componente › pieza). El componente superior '
                       'se indica por su código (puede venir en una fila anterior del mismo archivo).',
        'columnas': [
            ('equipo', 'Equipo (código)', True, 'CAM-01', ''), ('codigo', 'Código', True, 'MOT', 'Único por equipo'),
            ('nombre', 'Nombre', True, 'Motor', ''),
            ('nivel', 'Nivel', True, 'Sistema', 'Sistema, Subsistema, Componente o Pieza'),
            ('padre', 'Pertenece a (código)', False, '', 'Código del componente superior'),
            ('repuesto', 'Repuesto (código de producto)', False, '', ''),
            ('cantidad', 'Cantidad del repuesto', False, '1', ''),
        ]}),
    ('planes_mantenimiento', {
        'titulo': 'Planes preventivos', 'icono': 'bi-calendar-check', 'modulos': ['flota', 'logistica', 'mantenimiento', 'manufactura'],
        'descripcion': 'Cada N días, cada N km u horas, o lo que ocurra primero. Con «Actualizar existentes» se '
                       'reemplaza el plan con la misma tarea del equipo.',
        'columnas': [
            ('equipo', 'Equipo (código)', True, 'CAM-01', ''), ('tarea', 'Tarea', True, 'Cambio de aceite', ''),
            ('componente', 'Componente (código)', False, 'MOT', ''),
            ('cada_dias', 'Cada (días)', False, '90', ''), ('cada_uso', 'Cada (km u horas)', False, '10000', ''),
            ('tolerancia', 'Tolerancia %', False, '10', 'Vacío = 10'),
            ('ultima_fecha', 'Última ejecución', False, '', 'dd/mm/aaaa'),
            ('ultima_lectura', 'Lectura de la última ejecución', False, '', ''),
        ]}),
    ('lecturas', {
        'titulo': 'Lecturas de odómetro / horómetro', 'icono': 'bi-speedometer', 'modulos': ['flota', 'logistica', 'mantenimiento', 'manufactura'],
        'descripcion': 'Quedan por aprobar (Logística › Flota › Lecturas por aprobar). En orden de fecha: ninguna '
                       'puede ser menor a la anterior del equipo.',
        'columnas': [
            ('equipo', 'Equipo (código o placa)', True, 'CAM-01', ''), ('fecha', 'Fecha', True, '15/10/2026',
                                                                         'dd/mm/aaaa'),
            ('lectura', 'Lectura', True, '125400', 'km u horas'),
        ]}),
    ('documentos_flota', {
        'titulo': 'Documentos de flota (SOAT, revisión, licencias)', 'icono': 'bi-calendar-x',
        'modulos': ['flota', 'logistica', 'mantenimiento', 'manufactura'],
        'descripcion': 'Del equipo (código o placa) o del conductor (DNI). SOAT, revisión técnica, MTC y licencia '
                       'vencidos bloquean la guía.',
        'columnas': [
            ('equipo', 'Equipo (código o placa)', False, 'ABC123', 'O el conductor'),
            ('conductor', 'Conductor (DNI)', False, '', ''),
            ('tipo', 'Documento', True, 'SOAT', 'SOAT, Revisión técnica, Tarjeta de propiedad, Póliza, MTC, GPS, '
                                                'Licencia u Otro'),
            ('numero', 'N°', False, '', ''), ('emisor', 'Emisor / aseguradora', False, '', ''),
            ('categoria', 'Categoría (licencia)', False, '', 'A-IIb, A-IIIc…'),
            ('emision', 'Emisión', False, '', 'dd/mm/aaaa'), ('vencimiento', 'Vence', True, '31/12/2026', 'dd/mm/aaaa'),
            ('costo', 'Costo S/', False, '0', ''), ('bloquea', 'Bloquea la guía si vence', False, 'Sí', 'Sí o No'),
        ]}),
]


def _fecha(v, campo, requerido=False):
    if v in (None, ''):
        if requerido:
            raise ErrorFila(f'{campo}: obligatoria.')
        return None
    try:
        return a_fecha(v)
    except ValueError as exc:
        raise ErrorFila(f'{campo}: {exc}')


def _si_no(d, campo, defecto):
    return SI_NO.get(normalizar(d.get(campo)), defecto) if _txt(d.get(campo)) else defecto


def _equipo(texto, campo='Equipo'):
    from produccion.models import Equipo
    t = _txt(texto).upper()
    if not t:
        raise ErrorFila(f'{campo}: obligatorio.')
    eq = Equipo.objects.filter(codigo=t).first() or Equipo.objects.filter(placa=t.replace('-', '')).first()
    if eq is None:
        raise ErrorFila(f'{campo}: {t} no existe.')
    return eq


def _fila_equipo(d, actualizar):
    from activos.models import ActivoFijo
    from contabilidad.models import CentroCosto
    from planillas.models import Trabajador
    from produccion.models import CentroTrabajo, Equipo
    codigo, nombre = _txt(d.get('codigo')).upper(), _txt(d.get('nombre'))
    if not codigo or not nombre:
        raise ErrorFila('Código y nombre son obligatorios.')
    tipo = _elegir(d.get('tipo'), _opciones(Equipo.TIPOS, {'MAQUINA': 'MAQUINA', 'MAQUINARIA PESADA': 'MAQ_PESADA',
                                                           'CAMION': 'VEHICULO', 'AUTO': 'VEHICULO'}), 'Tipo')
    placa = _txt(d.get('placa')).replace(' ', '').replace('-', '').upper()
    if tipo == 'VEHICULO' and not placa:
        raise ErrorFila('Placa: obligatoria para vehículos.')
    datos = {'nombre': nombre[:120], 'tipo': tipo, 'placa': placa[:10],
             'estado': _elegir(d.get('estado'), _opciones(Equipo.ESTADOS, {'MANTENIMIENTO': 'MANTENIMIENTO',
                                                                          'BAJA': 'BAJA'}), 'Estado',
                               requerido=False, defecto='OPERATIVO'),
             'marca_modelo': _txt(d.get('marca_modelo'))[:120], 'serie': _txt(d.get('serie'))[:40],
             'anio': int(_dec(d.get('anio'), 'Año') or 0) or None,
             'combustible': _elegir(d.get('combustible'), _opciones(Equipo.COMBUSTIBLES, {'DIESEL': 'DIESEL'}),
                                    'Combustible', requerido=False),
             'medidor': _elegir(d.get('medidor'), {'KM': 'KM', 'KILOMETROS': 'KM', 'ODOMETRO': 'KM', 'HORAS': 'HORAS',
                                                  'H': 'HORAS', 'HOROMETRO': 'HORAS'}, 'Medidor', requerido=False),
             'rendimiento_esperado': str(_dec(d.get('rendimiento'), 'Rendimiento', minimo=D0) or '') or None,
             'ubicacion': _txt(d.get('ubicacion'))[:80], 'aplica_prestamo': _si_no(d, 'presta', False)}
    for clave, modelo, campo, filtro in (('puesto', CentroTrabajo, 'centro_id', 'codigo'),
                                         ('centro_costo', CentroCosto, 'centro_costo_id', 'codigo'),
                                         ('activo_fijo', ActivoFijo, 'activo_fijo_id', 'codigo'),
                                         ('responsable', Trabajador, 'responsable_id', 'numero_doc')):
        valor = _txt(d.get(clave))
        datos[campo] = None
        if valor:
            obj = modelo.objects.filter(**{filtro: valor}).first()
            if obj is None:
                raise ErrorFila(f'{clave.replace("_", " ").capitalize()} {valor} no existe.')
            datos[campo] = obj.pk
    existe = Equipo.objects.filter(codigo=codigo).exists()
    if existe and not actualizar:
        raise ErrorFila(f'{codigo} ya existe (marque "Actualizar existentes").')
    return {'accion': 'Actualizar' if existe else 'Nuevo', 'codigo': codigo, 'datos': datos,
            'resumen': f'{codigo} · {nombre} · {dict(Equipo.TIPOS)[tipo]}{" · " + placa if placa else ""}'}


def _fila_componente(d, actualizar):
    from core.models import Producto

    from .models import ComponenteEquipo
    eq = _equipo(d.get('equipo'))
    codigo, nombre = _txt(d.get('codigo')).upper(), _txt(d.get('nombre'))
    if not codigo or not nombre:
        raise ErrorFila('Código y nombre son obligatorios.')
    nivel = _elegir(d.get('nivel'), _opciones(ComponenteEquipo.NIVELES, {'PIEZA': 'PIEZA', 'REPUESTO': 'PIEZA'}),
                    'Nivel')
    padre = _txt(d.get('padre')).upper()
    vistos = _CONTEXTO.setdefault('componentes', set())
    if padre and (eq.codigo, padre) not in vistos and not eq.componentes.filter(codigo=padre).exists():
        raise ErrorFila(f'Pertenece a: {padre} no existe en {eq.codigo} (debe ir en una fila anterior).')
    repuesto = _txt(d.get('repuesto')).upper()
    if repuesto and not Producto.objects.filter(codigo=repuesto).exists():
        raise ErrorFila(f'Repuesto {repuesto} no existe.')
    vistos.add((eq.codigo, codigo))
    existe = eq.componentes.filter(codigo=codigo).exists()
    if existe and not actualizar:
        raise ErrorFila(f'{codigo} ya existe en {eq.codigo} (marque "Actualizar existentes").')
    return {'accion': 'Actualizar' if existe else 'Nuevo', 'codigo': f'{eq.codigo}|{codigo}',
            'datos': {'equipo': eq.pk, 'codigo': codigo, 'nombre': nombre[:120], 'nivel': nivel, 'padre': padre,
                      'repuesto': repuesto,
                      'cantidad': str(_dec(d.get('cantidad'), 'Cantidad', minimo=D0) or Decimal('1'))},
            'resumen': f'{eq.codigo} · {codigo} {nombre} · {dict(ComponenteEquipo.NIVELES)[nivel]}'}


def _fila_plan(d, actualizar):
    eq = _equipo(d.get('equipo'))
    tarea = _txt(d.get('tarea'))
    if not tarea:
        raise ErrorFila('Tarea: obligatoria.')
    dias = _dec(d.get('cada_dias'), 'Cada (días)', minimo=D0)
    uso = _dec(d.get('cada_uso'), 'Cada (km u horas)', minimo=D0)
    if not dias and not uso:
        raise ErrorFila('Indique cada cuántos días o cada cuántos km u horas.')
    if uso and not eq.medidor:
        raise ErrorFila(f'{eq.codigo} no tiene odómetro ni horómetro: el plan por uso no aplica.')
    componente = _txt(d.get('componente')).upper()
    if componente and not eq.componentes.filter(codigo=componente).exists():
        raise ErrorFila(f'Componente {componente} no existe en {eq.codigo}.')
    ultima = _fecha(d.get('ultima_fecha'), 'Última ejecución')
    existe = eq.planes.filter(tarea__iexact=tarea, activo=True).exists()
    if existe and not actualizar:
        raise ErrorFila(f'{eq.codigo} ya tiene el plan «{tarea}» (marque "Actualizar existentes").')
    return {'accion': 'Actualizar' if existe else 'Nuevo', 'codigo': f'{eq.codigo}|{normalizar(tarea)}',
            'datos': {'equipo': eq.pk, 'tarea': tarea[:150], 'componente': componente,
                      'frecuencia_dias': int(dias) if dias else None, 'frecuencia_uso': str(uso) if uso else None,
                      'tolerancia_pct': str(_dec(d.get('tolerancia'), 'Tolerancia', minimo=D0) or Decimal('10')),
                      'ultima_fecha': ultima.isoformat() if ultima else None,
                      'ultima_lectura': str(_dec(d.get('ultima_lectura'), 'Última lectura', minimo=D0) or '') or None},
            'resumen': f'{eq.codigo} · {tarea} · ' + ' o '.join(
                x for x in (f'cada {int(dias)} días' if dias else '', f'cada {uso} {eq.unidad_medidor}' if uso else '')
                if x)}


def _fila_lectura(d, actualizar):
    from .lecturas import ultima
    eq = _equipo(d.get('equipo'))
    if not eq.medidor:
        raise ErrorFila(f'{eq.codigo} no tiene odómetro ni horómetro.')
    fecha = _fecha(d.get('fecha'), 'Fecha', requerido=True)
    valor = _dec(d.get('lectura'), 'Lectura', requerido=True, minimo=D0)
    previas = _CONTEXTO.setdefault('lecturas', {})
    anterior = max([v for v in (ultima(eq), previas.get(eq.pk)) if v is not None], default=None)
    if anterior is not None and valor < anterior:
        raise ErrorFila(f'{eq.codigo}: {valor} es menor a la lectura anterior ({anterior}).')
    if fecha > timezone.localdate():
        raise ErrorFila('Fecha: no puede ser futura.')
    previas[eq.pk] = valor
    return {'accion': 'Nuevo', 'codigo': f'{eq.codigo}|{fecha.isoformat()}|{valor}',
            'datos': {'equipo': eq.pk, 'fecha': fecha.isoformat(), 'valor': str(valor)},
            'resumen': f'{eq.codigo} · {fecha:%d/%m/%Y} · {valor} {eq.unidad_medidor}'}


def _fila_documento(d, actualizar):
    from logistica.models import Conductor

    from .models import DocumentoFlota
    eq = _equipo(d.get('equipo')) if _txt(d.get('equipo')) else None
    conductor = None
    if _txt(d.get('conductor')):
        conductor = Conductor.objects.filter(numero_doc=_txt(d.get('conductor'))).first()
        if conductor is None:
            raise ErrorFila(f'Conductor {_txt(d.get("conductor"))} no existe.')
    if (eq is None) == (conductor is None):
        raise ErrorFila('Indique el equipo o el conductor (uno de los dos).')
    tipo = _elegir(d.get('tipo'), _opciones(DocumentoFlota.TIPOS, {'REVISION TECNICA': 'REVISION', 'MTC': 'MTC',
                                                                   'LICENCIA': 'LICENCIA', 'POLIZA': 'POLIZA'}),
                   'Documento')
    if tipo == 'LICENCIA' and conductor is None:
        raise ErrorFila('La licencia es de un conductor.')
    vence = _fecha(d.get('vencimiento'), 'Vence', requerido=True)
    emision = _fecha(d.get('emision'), 'Emisión')
    if emision and vence < emision:
        raise ErrorFila('El vencimiento es anterior a la emisión.')
    titular = eq.codigo if eq else conductor.numero_doc
    return {'accion': 'Nuevo', 'codigo': f'{titular}|{tipo}|{_txt(d.get("numero"))}|{vence.isoformat()}',
            'datos': {'equipo': eq.pk if eq else None, 'conductor': conductor.pk if conductor else None, 'tipo': tipo,
                      'numero': _txt(d.get('numero'))[:40], 'emisor': _txt(d.get('emisor'))[:100],
                      'categoria': _txt(d.get('categoria'))[:20], 'emision': emision.isoformat() if emision else None,
                      'vencimiento': vence.isoformat(), 'costo': str(_dec(d.get('costo'), 'Costo', minimo=D0) or D0),
                      'bloquea': _si_no(d, 'bloquea', tipo in DocumentoFlota.BLOQUEAN)},
            'resumen': f'{titular} · {dict(DocumentoFlota.TIPOS)[tipo]} · vence {vence:%d/%m/%Y}'}


VALIDADORES = {'equipos': _fila_equipo, 'componentes': _fila_componente, 'planes_mantenimiento': _fila_plan,
               'lecturas': _fila_lectura, 'documentos_flota': _fila_documento}


def cargar(tipo, filas, usuario):
    from core.models import Producto
    from produccion.models import Equipo, PlanMantenimiento

    from . import servicios
    from .lecturas import registrar
    from .models import ComponenteEquipo, DocumentoFlota
    if tipo == 'equipos':
        for f in filas:
            eq, _ = Equipo.objects.update_or_create(codigo=f['codigo'], defaults=dict(f['datos']))
            servicios.vehiculo_de_equipo(eq)
        return f'{len(filas)} equipos cargados.'
    if tipo == 'componentes':
        for f in filas:  # en orden: el componente superior va en una fila anterior
            d = f['datos']
            padre = (ComponenteEquipo.objects.filter(equipo_id=d['equipo'], codigo=d['padre']).first()
                     if d['padre'] else None)
            ComponenteEquipo.objects.update_or_create(
                equipo_id=d['equipo'], codigo=d['codigo'],
                defaults={'nombre': d['nombre'], 'nivel': d['nivel'], 'padre': padre,
                          'cantidad_repuesto': Decimal(d['cantidad']), 'activo': True,
                          'repuesto': Producto.objects.filter(codigo=d['repuesto']).first() if d['repuesto'] else None})
        return f'{len(filas)} componentes cargados.'
    if tipo == 'planes_mantenimiento':
        for f in filas:
            d = dict(f['datos'])
            equipo = d.pop('equipo')
            comp = d.pop('componente')
            d['componente'] = ComponenteEquipo.objects.filter(equipo_id=equipo, codigo=comp).first() if comp else None
            d['ultima_fecha'] = date.fromisoformat(d['ultima_fecha']) if d['ultima_fecha'] else None
            for campo in ('frecuencia_uso', 'tolerancia_pct', 'ultima_lectura'):
                d[campo] = Decimal(d[campo]) if d[campo] else None
            d['tolerancia_pct'] = d['tolerancia_pct'] or Decimal('10')
            plan = PlanMantenimiento.objects.filter(equipo_id=equipo, tarea__iexact=d['tarea'], activo=True).first() \
                or PlanMantenimiento(equipo_id=equipo)
            for campo, valor in d.items():
                setattr(plan, campo, valor)
            plan.save()
        return f'{len(filas)} planes preventivos cargados.'
    if tipo == 'lecturas':
        orden = sorted(filas, key=lambda f: (f['datos']['equipo'], f['datos']['fecha'], Decimal(f['datos']['valor'])))
        for f in orden:
            d = f['datos']
            registrar(Equipo.objects.get(pk=d['equipo']), Decimal(d['valor']), date.fromisoformat(d['fecha']),
                      usuario, 'CARGA', observacion='Carga masiva')
        return f'{len(filas)} lecturas cargadas: quedan por aprobar.'
    if tipo == 'documentos_flota':
        for f in filas:
            d = dict(f['datos'])
            doc = DocumentoFlota(equipo_id=d.pop('equipo'), conductor_id=d.pop('conductor'),
                                 vencimiento=date.fromisoformat(d.pop('vencimiento')),
                                 emision=date.fromisoformat(d['emision']) if d['emision'] else None,
                                 costo=Decimal(d.pop('costo')))
            d.pop('emision')
            for campo, valor in d.items():
                setattr(doc, campo, valor)
            servicios.registrar_documento(doc, usuario)
        return f'{len(filas)} documentos cargados.'
    raise ValueError(tipo)
