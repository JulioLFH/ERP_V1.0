"""Reglas del CRM (v1.81): probabilidades y nombres de etapas configurables, visibilidad por nivel, historial de
etapas, duplicados, crédito del cliente, conversión de prospecto, cotizaciones unidas a la oportunidad, reasignación de
cartera, asignación de los prospectos de la web y avisos a operaciones."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

D0 = Decimal('0')
PARAM_PROB = {'PROSPECTO': 'CRM_PROB_PROSPECTO', 'CALIFICADO': 'CRM_PROB_CALIFICADO',
              'PROPUESTA': 'CRM_PROB_PROPUESTA', 'NEGOCIACION': 'CRM_PROB_NEGOCIACION'}


class ErrorCRM(Exception):
    pass


def parametro(clave):
    from core import parametros
    return parametros.valor(clave)


def probabilidad_de(etapa):
    """Probabilidad de la etapa (CRM-07): Ganada 100, Perdida 0 y las abiertas según los parámetros del CRM."""
    if etapa == 'GANADA':
        return 100
    if etapa == 'PERDIDA':
        return 0
    return int(parametro(PARAM_PROB[etapa])) if etapa in PARAM_PROB else 0


def etapas():
    """[(código, nombre)] con los nombres propios de la empresa (Configuración del CRM)."""
    from .models import ConfigCRM, Oportunidad
    propios = ConfigCRM.actual().nombres_etapas or {}
    return [(c, propios.get(c) or n) for c, n in Oportunidad.ETAPAS]


def nombre_etapa(codigo):
    return dict(etapas()).get(codigo, codigo)


# ---------------------------------------------------------------- visibilidad por nivel (CRM-15)
def ve_todo(user):
    from core.permisos import puede
    return user.is_superuser or puede(user, 'crm.ver_todas')


def equipo_de(user):
    """IDs de los usuarios cuyas oportunidades ve el usuario: él mismo, los miembros de los equipos que dirige y
    quienes lo tienen como jefe directo."""
    from core.models import PerfilUsuario
    from .models import EquipoVenta
    ids = {user.pk}
    for e in EquipoVenta.objects.filter(jefe=user, activo=True).prefetch_related('miembros'):
        ids |= {m.pk for m in e.miembros.all()}
    ids |= set(PerfilUsuario.objects.filter(jefe=user).values_list('usuario_id', flat=True))
    return ids


def visibles(user, qs):
    """Oportunidades que el usuario puede ver: todas (gerente), las de su equipo (jefe) o las suyas (vendedor)."""
    if ve_todo(user):
        return qs
    return qs.filter(Q(responsable_id__in=equipo_de(user)) | Q(responsable__isnull=True))


def puede_ver(user, o):
    return visibles(user, type(o).objects.filter(pk=o.pk)).exists()


def puede_reasignar(user, o):
    return ve_todo(user) or (o.responsable_id in equipo_de(user) and o.responsable_id != user.pk) or \
        (o.responsable_id is None)


# ---------------------------------------------------------------- etapas, cierre y avisos
@transaction.atomic
def cambiar_etapa(o, etapa, usuario=None, guardar=True):
    """Cambia la etapa dejando el historial (conversión y ciclo de venta) y la fecha de cierre; al ganar avisa a
    operaciones (CRM-21)."""
    from .models import CambioEtapa
    anterior = o.etapa
    if anterior == etapa:
        return o
    o.etapa = etapa
    o.cerrada_en = timezone.localdate() if etapa in ('GANADA', 'PERDIDA') else None
    if guardar:
        o.save(update_fields=['etapa', 'cerrada_en', 'actualizado'])
    CambioEtapa.objects.create(oportunidad=o, de=anterior, a=etapa,
                               usuario=usuario if usuario is not None and usuario.is_authenticated else None)
    if etapa in ('GANADA', 'PERDIDA'):  # CRM-22: sus actividades pendientes se cierran (no quedan como vencidas)
        o.actividades.filter(hecha=False).update(
            hecha=True, resultado=f'Cerrada: oportunidad {"ganada" if etapa == "GANADA" else "perdida"}')
    if etapa == 'GANADA':
        transaction.on_commit(lambda: avisar_ganada(o))
    return o


def avisar_ganada(o):
    """Correo a los responsables de operaciones (Configuración del CRM) y al residente de la obra vinculada."""
    from core.correo import enviar_simple
    from .models import ConfigCRM
    destinos = set(ConfigCRM.actual().avisar_ganadas.filter(is_active=True).values_list('email', flat=True))
    from obras.models import Licitacion
    lic = Licitacion.objects.filter(oportunidad=o).select_related('obra__residente').first()
    if lic and lic.obra.residente_id and lic.obra.residente.email:
        destinos.add(lic.obra.residente.email)
    destinos.discard('')
    if not destinos:
        return 0
    inicio = f' Inicio probable: {o.fecha_inicio:%d/%m/%Y}.' if o.fecha_inicio else ''
    try:
        enviar_simple(sorted(destinos), f'Oportunidad ganada: {o.nombre}',
                      f'Se ganó la oportunidad «{o.nombre}» de {o.cliente} por S/ {o.monto:,.2f}.{inicio} '
                      f'Responsable: {o.responsable or "—"}. Planifique personal, equipos y compras.')
    except Exception:  # noqa: BLE001  (sin correo configurado: queda el aviso en Inicio)
        return 0
    return len(destinos)


def duplicados(o):
    """Oportunidades abiertas que podrían ser la misma (CRM-02): mismo cliente o prospecto, nombre parecido, mismo
    RUC, teléfono o correo."""
    from .models import Oportunidad
    filtro = Q()
    if o.tercero_id:
        filtro |= Q(tercero_id=o.tercero_id)
    if (o.prospecto or '').strip():
        filtro |= Q(prospecto__iexact=o.prospecto.strip())
    if (o.telefono or '').strip():
        filtro |= Q(telefono=o.telefono.strip())
    if (o.email or '').strip():
        filtro |= Q(email__iexact=o.email.strip())
    if (o.nombre or '').strip():
        filtro |= Q(nombre__iexact=o.nombre.strip())
    if not filtro:
        return []
    qs = Oportunidad.objects.filter(filtro, etapa__in=Oportunidad.ABIERTAS)
    if o.pk:
        qs = qs.exclude(pk=o.pk)
    return list(qs.select_related('tercero', 'responsable')[:10])


# ---------------------------------------------------------------- crédito del cliente (CRM-16)
def credito(tercero):
    """{limite, deuda, vencida, disponible, dias_pago, en_mora, documentos_vencidos} del cliente."""
    if tercero is None:
        return None
    from .models import Venta
    hoy = timezone.localdate()
    deuda = [v for v in Venta.objects.con_saldos().cobrables().filter(tercero=tercero, estado='REGISTRADO')
             .exclude(tipo_comprobante='07') if v.saldo > 0]
    vencidos = [v for v in deuda if v.fecha_vencimiento and v.fecha_vencimiento < hoy]
    total = sum((v.saldo_pen for v in deuda), D0)
    vencida = sum((v.saldo_pen for v in vencidos), D0)
    dias = []
    desde = hoy - timedelta(days=365)
    for v in Venta.objects.filter(tercero=tercero, estado='REGISTRADO', fecha_emision__gte=desde).exclude(
            tipo_comprobante='07').prefetch_related('movimientos'):
        fechas = [m.fecha for m in v.movimientos.all()]
        if fechas:
            dias.append((max(fechas) - v.fecha_emision).days)
    limite = tercero.limite_credito or D0
    return {'limite': limite, 'deuda': total, 'vencida': vencida, 'documentos_vencidos': len(vencidos),
            'disponible': (limite - total) if limite else None,
            'dias_pago': round(sum(dias) / len(dias)) if dias else None, 'en_mora': bool(vencidos)}


# ---------------------------------------------------------------- prospecto -> cliente (CRM-02)
@transaction.atomic
def convertir_en_cliente(o, tipo_doc, numero_doc, usuario=None, nombre='', direccion=''):
    """Crea (o encuentra) el cliente con su documento y reemplaza el prospecto en la oportunidad. Con RUC trae la
    razón social y la dirección del padrón de SUNAT si está disponible."""
    from core.models import Tercero
    numero_doc = (numero_doc or '').strip()
    if tipo_doc == '6' and (len(numero_doc) != 11 or not numero_doc.isdigit()):
        raise ErrorCRM('El RUC tiene 11 dígitos.')
    if tipo_doc == '1' and (len(numero_doc) != 8 or not numero_doc.isdigit()):
        raise ErrorCRM('El DNI tiene 8 dígitos.')
    if not numero_doc:
        raise ErrorCRM('Indique el número de documento.')
    tercero = Tercero.objects.filter(numero_doc=numero_doc).first()
    if tercero is None:
        datos = {}
        if tipo_doc == '6':
            try:
                from core.padron_ruc import consultar
                datos = consultar(numero_doc)
            except Exception:  # noqa: BLE001  (sin conexión o sin token: se usan los datos del prospecto)
                datos = {}
        tercero = Tercero.objects.create(
            tipo='CLIENTE', tipo_doc=tipo_doc, numero_doc=numero_doc,
            nombre=(datos.get('razon_social') or nombre or o.prospecto or o.nombre)[:200],
            direccion=(datos.get('direccion') or direccion or '')[:250], ubigeo=datos.get('ubigeo') or '',
            email=o.email or '', telefono=o.telefono or '')
    elif tercero.tipo == 'PROVEEDOR':
        tercero.tipo = 'AMBOS'
        tercero.save(update_fields=['tipo'])
    if o.contacto:
        from .models import ContactoCliente
        if not ContactoCliente.objects.filter(tercero=tercero, nombre__iexact=o.contacto).exists():
            ContactoCliente.objects.create(tercero=tercero, nombre=o.contacto[:120], telefono=o.telefono or '',
                                           email=o.email or '', principal=not tercero.contactos_crm.exists())
    prospecto = o.prospecto
    o.tercero, o.prospecto = tercero, ''
    if prospecto and prospecto not in (o.notas or ''):
        o.notas = f'{o.notas}\nProspecto: {prospecto}'.strip()
    o.save(update_fields=['tercero', 'prospecto', 'notas', 'actualizado'])
    from .models import ConsentimientoDatos
    ConsentimientoDatos.objects.filter(oportunidad=o, tercero__isnull=True).update(tercero=tercero)
    asignar_cartera(tercero, o.responsable)
    return tercero


def asignar_cartera(tercero, responsable):
    """El cliente queda en la cartera del vendedor (si aún no tiene uno)."""
    if tercero is None or responsable is None:
        return
    from .models import CarteraCliente
    CarteraCliente.objects.get_or_create(tercero=tercero, defaults={'responsable': responsable})


# ---------------------------------------------------------------- cotización <-> oportunidad (CRM-03, CRM-14)
def vincular_cotizacion(cot, o):
    """La cotización vuelve a la oportunidad: queda unida, el monto de la oportunidad pasa a ser el de la
    cotización (en soles, sin IGV) y una oportunidad en Prospecto o Calificado pasa a Propuesta."""
    from .models import Cotizacion
    Cotizacion.objects.filter(pk=cot.pk).update(oportunidad=o)
    cot.oportunidad = o
    base = (cot.base_imponible or D0) + (cot.no_gravado or D0) or cot.total
    monto = base * (cot.tipo_cambio or 1) if cot.moneda == 'USD' else base
    o.cotizacion = cot if cot.tipo == 'COT' or not o.cotizacion_id else o.cotizacion
    o.monto = monto.quantize(Decimal('0.01'))
    if not o.tercero_id and cot.tercero_id:
        o.tercero = cot.tercero
    o.save(update_fields=['cotizacion', 'monto', 'tercero', 'actualizado'])
    if o.etapa in ('PROSPECTO', 'CALIFICADO'):
        cambiar_etapa(o, 'PROPUESTA')
    return o


def asegurar_oportunidad(cot, usuario=None):
    """CRM-14: una cotización desde el monto del parámetro sin oportunidad crea la suya (origen «cotización sin
    oportunidad», responsable el vendedor) para que el embudo quede completo."""
    from .models import Oportunidad
    desde = parametro('CRM_EXIGIR_OPORTUNIDAD_DESDE')
    if cot.oportunidad_id or not desde or cot.total < desde or cot.estado == 'ANULADO':
        return None
    o = Oportunidad.objects.create(
        nombre=f'{cot.get_tipo_display()} {cot.numero}'[:150], tercero=cot.tercero, etapa='PROPUESTA',
        origen='COTIZACION', responsable=usuario if usuario is not None and usuario.is_authenticated else None,
        fecha_cierre=cot.fecha + timedelta(days=cot.validez_dias or 15) if cot.fecha else None)
    vincular_cotizacion(cot, o)
    return o


def ganar_desde_cotizacion(cot, usuario=None):
    """CRM-03: la cotización aprobada o atendida (pedido confirmado, comprobante emitido) gana su oportunidad."""
    o = cot.oportunidad
    if o is None or o.etapa not in o.ABIERTAS:
        return None
    from obras.models import Licitacion
    if Licitacion.objects.filter(oportunidad=o, resultado='EN_CURSO').exists():
        return None  # licitación de obra: el resultado se registra en Obras
    return cambiar_etapa(o, 'GANADA', usuario)


def cotizaciones_por_vencer(usuario_ids=None):
    """Cotizaciones pendientes de oportunidades abiertas con la validez vencida (aviso al vendedor: CRM-03)."""
    from .models import Cotizacion
    hoy = timezone.localdate()
    qs = Cotizacion.objects.filter(tipo='COT', estado='PENDIENTE', oportunidad__etapa__in=[
        'PROSPECTO', 'CALIFICADO', 'PROPUESTA', 'NEGOCIACION']).select_related('oportunidad', 'tercero')
    if usuario_ids is not None:
        qs = qs.filter(oportunidad__responsable_id__in=usuario_ids)
    return [c for c in qs if c.fecha and c.fecha + timedelta(days=c.validez_dias or 0) < hoy]


# ---------------------------------------------------------------- cartera y reasignación (CRM-15)
def cartera_abierta(usuario):
    from .models import ActividadCRM, CarteraCliente, Oportunidad
    return {'oportunidades': Oportunidad.objects.filter(responsable=usuario, etapa__in=Oportunidad.ABIERTAS).count(),
            'actividades': ActividadCRM.objects.filter(hecha=False).filter(
                Q(responsable=usuario) | Q(responsable__isnull=True, usuario=usuario)).count(),
            'clientes': CarteraCliente.objects.filter(responsable=usuario).count()}


@transaction.atomic
def reasignar(de, a, usuario=None, motivo='', oportunidades=None):
    """Pasa las oportunidades abiertas (o las indicadas), las actividades pendientes y los clientes de la cartera
    de un vendedor a otro, con su historial."""
    from .models import ActividadCRM, CarteraCliente, Oportunidad, Reasignacion
    if a is None or de is None or a.pk == de.pk:
        raise ErrorCRM('Elija a quién pasar la cartera (otro usuario).')
    if not a.is_active:
        raise ErrorCRM(f'{a} está inactivo.')
    qs = Oportunidad.objects.filter(responsable=de, etapa__in=Oportunidad.ABIERTAS)
    if oportunidades is not None:
        qs = Oportunidad.objects.filter(pk__in=[o.pk for o in oportunidades], responsable=de)
    ids = list(qs.values_list('pk', flat=True))
    n_op = Oportunidad.objects.filter(pk__in=ids).update(responsable=a)
    act = ActividadCRM.objects.filter(hecha=False).filter(Q(responsable=de) | Q(responsable__isnull=True, usuario=de))
    if oportunidades is not None:
        act = act.filter(oportunidad_id__in=ids)
    n_act = act.update(responsable=a)
    n_cli = CarteraCliente.objects.filter(responsable=de).update(responsable=a) if oportunidades is None else 0
    Reasignacion.objects.create(de=de, a=a, oportunidades=n_op, actividades=n_act, clientes=n_cli,
                                motivo=(motivo or '')[:200],
                                usuario=usuario if usuario is not None and usuario.is_authenticated else None)
    return n_op, n_act, n_cli


# ---------------------------------------------------------------- formulario web (CRM-09)
def asignar_web():
    """Responsable del prospecto que llega de la web: fijo o rotativo entre los miembros del equipo."""
    from .models import ConfigCRM
    cfg = ConfigCRM.actual()
    if cfg.asignacion_web == 'ROTATIVO' and cfg.equipo_web_id:
        miembros = list(cfg.equipo_web.miembros.filter(is_active=True).order_by('pk'))
        if miembros:
            ids = [m.pk for m in miembros]
            siguiente = miembros[(ids.index(cfg.ultimo_asignado_id) + 1) % len(miembros)] \
                if cfg.ultimo_asignado_id in ids else miembros[0]
            ConfigCRM.objects.filter(pk=cfg.pk).update(ultimo_asignado=siguiente)
            return siguiente
    return cfg.responsable_web


# ---------------------------------------------------------------- clientes sin compras (CRM-12)
def clientes_inactivos(dias=None, usuario_ids=None):
    """[(cliente, última compra, días)] de clientes con ventas que no compran hace `dias` (por defecto el parámetro)."""
    from django.db.models import Max
    from .models import CarteraCliente, Venta
    dias = int(dias if dias is not None else parametro('CRM_DIAS_CLIENTE_INACTIVO'))
    if dias <= 0:
        return []
    hoy = timezone.localdate()
    filas = (Venta.objects.filter(estado='REGISTRADO').exclude(tipo_comprobante='07').values('tercero')
             .annotate(ultima=Max('fecha_emision')).filter(ultima__lt=hoy - timedelta(days=dias)))
    ids = {f['tercero']: f['ultima'] for f in filas}
    if usuario_ids is not None:
        propios = set(CarteraCliente.objects.filter(responsable_id__in=usuario_ids).values_list('tercero_id', flat=True))
        ids = {k: v for k, v in ids.items() if k in propios}
    from core.models import Tercero
    terceros = {t.pk: t for t in Tercero.objects.filter(pk__in=ids, activo=True)}
    return sorted([(terceros[k], v, (hoy - v).days) for k, v in ids.items() if k in terceros], key=lambda x: -x[2])


# ---------------------------------------------------------------- fecha de entrega factible (CRM-12, manufactura)
def fecha_entrega(items, hoy=None):
    """Fecha de entrega factible de los productos [(producto, cantidad)], como la calcula el MRP: lo que hay en stock
    sale hoy; lo que falta se fabrica con su receta vigente (insumos sin stock según su plazo de compra, más el plazo
    de fabricación del producto) o se compra (plazo del producto). Devuelve (fecha, [detalle])."""
    from produccion.models import ListaMateriales
    hoy = hoy or timezone.localdate()
    fecha, detalle = hoy, []
    for producto, cantidad in items:
        if producto is None or producto.tipo != 'BIEN':
            continue
        cantidad = Decimal(cantidad or 0)
        falta = cantidad - max(producto.stock or D0, D0)
        if falta <= 0:
            detalle.append(f'{producto.nombre}: en stock')
            continue
        receta = ListaMateriales.objects.filter(producto=producto, activa=True).prefetch_related(
            'componentes__producto').first()
        if receta is None:
            dias = producto.tiempo_entrega or 0
            detalle.append(f'{producto.nombre}: faltan {falta:g}, se compra en {dias} día(s)')
        else:
            lotes = falta / (receta.cantidad_base or 1)
            insumos = 0
            for c in receta.componentes.all():
                necesario = c.cantidad * lotes
                if (c.producto.stock or D0) < necesario:
                    insumos = max(insumos, c.producto.tiempo_entrega or 0)
            dias = insumos + (producto.tiempo_entrega or 1)
            detalle.append(f'{producto.nombre}: faltan {falta:g}, se fabrica en {dias} día(s)'
                           + (f' (insumos en {insumos})' if insumos else ''))
        fecha = max(fecha, hoy + timedelta(days=dias))
    return fecha, detalle


# ---------------------------------------------------------------- señales: la venta gana la oportunidad
def _al_guardar_cotizacion(sender, instance, created, **kwargs):
    if instance.estado in ('APROBADO', 'ATENDIDO') and instance.oportunidad_id:
        ganar_desde_cotizacion(instance)


def _al_guardar_venta(sender, instance, created, **kwargs):
    cot = getattr(instance, 'cotizacion', None)
    if instance.estado == 'REGISTRADO' and cot is not None and cot.oportunidad_id:
        ganar_desde_cotizacion(cot)


def conectar():
    from django.db.models.signals import post_save
    from .models import Cotizacion, Venta
    post_save.connect(_al_guardar_cotizacion, sender=Cotizacion, dispatch_uid='crm-cotizacion-gana')
    post_save.connect(_al_guardar_venta, sender=Venta, dispatch_uid='crm-venta-gana')
