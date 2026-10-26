"""Bitácora de auditoría automática.

Un middleware recuerda el usuario e IP de la petición; las señales de los modelos auditados registran altas,
cambios (solo los campos que el usuario puede modificar, con valor anterior y nuevo) y eliminaciones.
Los recálculos internos (stock, totales en soles, estados de envío) no se registran.
"""
import threading
from datetime import date, datetime
from decimal import Decimal

from django.apps import apps
from django.db.models.signals import post_delete, post_save, pre_save

_local = threading.local()

AUDITADOS = [
    ('ventas', 'Venta'), ('compras', 'Compra'), ('compras', 'OrdenCompra'), ('finanzas', 'Movimiento'),
    ('finanzas', 'Cuenta'), ('contabilidad', 'Asiento'), ('contabilidad', 'CuentaContable'),
    ('contabilidad', 'CentroCosto'), ('contabilidad', 'PeriodoContable'), ('inventario', 'Operacion'),
    ('inventario', 'TipoOperacion'), ('inventario', 'CierreKardex'), ('logistica', 'GuiaRemision'),
    ('core', 'Producto'), ('core', 'Tercero'), ('core', 'Almacen'), ('core', 'Empresa'), ('core', 'Serie'),
    ('core', 'FacturacionConfig'), ('core', 'CorreoConfig'), ('auth', 'User'),
    ('proveedores', 'FacturaProveedor'), ('proveedores', 'AccesoProveedor'),
    ('produccion', 'OrdenProduccion'), ('produccion', 'ListaMateriales'), ('produccion', 'CentroTrabajo'),
    ('activos', 'ActivoFijo'), ('activos', 'CategoriaActivo'), ('core', 'PerfilUsuario'),
    ('planillas', 'Trabajador'), ('planillas', 'Planilla'), ('planillas', 'AFP'), ('planillas', 'Parametro'),
    ('contabilidad', 'CentroBeneficio'), ('inventario', 'RequerimientoInterno'),
    ('produccion', 'HojaRuta'), ('produccion', 'VersionFabricacion'),
    ('finanzas', 'Rendicion'), ('finanzas', 'LineaGasto'), ('finanzas', 'CategoriaGasto'),
    ('finanzas', 'PoliticaGasto'), ('finanzas', 'ReglaAprobacion'), ('finanzas', 'EntregaRendir'),
    ('finanzas', 'GastoRendicion'), ('finanzas', 'TarjetaCorporativa'), ('finanzas', 'Proyecto'),
    ('finanzas', 'ConfigRendicion'),
    # log de modificaciones de cada módulo (los que no existan se omiten al conectar)
    ('ventas', 'Cotizacion'), ('ventas', 'ListaPrecios'), ('ventas', 'PrecioLista'), ('ventas', 'Oportunidad'),
    ('planillas', 'Turno'), ('planillas', 'ConceptoPlanilla'), ('core', 'Ubicacion'),
    ('contabilidad', 'CuentaDefecto'), ('finanzas', 'Retencion'),
    # CRM (v1.81)
    ('ventas', 'ActividadCRM'), ('ventas', 'ContactoCliente'), ('ventas', 'CasoPostventa'), ('ventas', 'Campana'),
    ('ventas', 'EquipoVenta'), ('ventas', 'MetaVenta'), ('ventas', 'ContratoServicio'),
    ('ventas', 'ConsentimientoDatos'), ('ventas', 'ConfigCRM'),
    # servicios por proyecto (v1.81)
    ('obras', 'EntregableProyecto'), ('obras', 'ParteHoras'), ('obras', 'GarantiaContrato'),
    ('obras', 'AvancePartida'),
    # tesorería y finanzas: documentos que se registran a mano
    ('finanzas', 'Cheque'), ('finanzas', 'PagoMasivo'), ('finanzas', 'DepositoDetraccion'), ('finanzas', 'Letra'),
    ('finanzas', 'CanjeLetras'), ('finanzas', 'Prestamo'),
    # manufactura: maestros, recetas, planta, calidad y mantenimiento (log de modificaciones de Manufactura)
    ('produccion', 'ComponenteLista'), ('produccion', 'SubproductoLista'), ('produccion', 'OperacionRuta'),
    ('produccion', 'PlanDemanda'), ('produccion', 'AvanceOrden'),
    ('produccion', 'Parada'), ('produccion', 'MotivoParada'), ('produccion', 'InspeccionCalidad'),
    ('produccion', 'ParametroCalidad'), ('produccion', 'Equipo'), ('produccion', 'PlanMantenimiento'),
    ('produccion', 'OrdenMantenimiento'), ('produccion', 'CambioIngenieria'),
]
# campos que el sistema recalcula solo: no son cambios del usuario
IGNORAR = {
    'id', 'creado', 'last_login', 'password', 'stock', 'costo_promedio', 'stock_aplicado', 'total_pen', 'base_pen',
    'nograv_pen', 'igv_pen', 'icbper_pen', 'ret_pen', 'perc_pen', 'detr_pen', 'base_imponible', 'no_gravado', 'igv',
    'total', 'detraccion_monto', 'retencion_monto', 'percepcion_monto', 'fecha_envio', 'enlace_pdf', 'enlace_xml',
    'enlace_cdr', 'codigo_hash', 'cadena_qr', 'sunat_descripcion', 'monto_doc', 'monto_doc_pen', 'periodo',
    'pendiente', 'fecha_centralizacion', 'correlativo', 'fecha_ingreso', 'conformidad_enviada_en',
    'conformidad_enviada_a', 'pdf', 'xml', 'token', 'clave', 'token_tipo_cambio', 'sunat_client_secret',
    'ia_api_key', 'whatsapp_token', 'whatsapp_app_secret', 'correo_clave',
}
# asientos automáticos: se regeneran al centralizar, solo se auditan los manuales y extornos
SOLO_SI = {'Asiento': lambda o: o.origen == 'MANUAL'}


class AuditoriaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.usuario = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
        _local.ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                     or request.META.get('REMOTE_ADDR', ''))[:45]
        try:
            return self.get_response(request)
        finally:
            _local.usuario = _local.ip = None


def usuario_actual():
    return getattr(_local, 'usuario', None)


def _valor(v):
    if isinstance(v, (Decimal,)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if hasattr(v, 'pk'):
        return str(v)
    if isinstance(v, (dict, list)):
        return v  # se compara tal cual y se muestra resumido (_mostrar)
    return v if isinstance(v, (int, float, bool, str, type(None))) else str(v)


def _mostrar(v):
    """INV-30: un detalle interno (JSON) no se muestra en crudo en el historial."""
    if isinstance(v, (dict, list)):
        return f'{len(v)} dato(s)' if v else '—'
    return v


def mostrar_valor(v):
    """Como _mostrar, también para lo que versiones anteriores guardaron como texto («{'a': 1}», «[]»)."""
    if isinstance(v, str) and len(v) > 1 and (v[0], v[-1]) in (('{', '}'), ('[', ']')):
        import ast
        try:
            v = ast.literal_eval(v)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return 'detalle'
    return _mostrar(v)


def _campos(obj):
    datos = {}
    for f in obj._meta.concrete_fields:
        if f.name in IGNORAR:
            continue
        valor = getattr(obj, f.attname)
        if f.is_relation and valor is not None:
            rel = getattr(obj, f.name, None)
            valor = str(rel) if rel is not None else valor
        elif f.choices and valor not in (None, ''):
            valor = getattr(obj, f'get_{f.name}_display')()
        datos[str(f.verbose_name)] = _valor(valor)
    return datos


def _iguales(a, b):
    """AUD-01: «0.00» y «0» (o 0) son el mismo importe: no es un cambio."""
    if a == b:
        return True
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return False
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (ArithmeticError, ValueError):
        return False


def registrar(accion, obj, cambios=None, motivo=''):
    from .models import Bitacora
    Bitacora.objects.create(
        usuario=usuario_actual(), accion=accion, modelo=obj._meta.label, modelo_nombre=str(obj._meta.verbose_name),
        objeto_id=str(obj.pk), objeto=str(obj)[:200], cambios=cambios or {}, motivo=(motivo or '')[:300],
        ip=getattr(_local, 'ip', '') or '')


def _pre_save(sender, instance, **kwargs):
    instance._audit_antes = None
    if instance.pk and not kwargs.get('raw'):
        anterior = sender._base_manager.filter(pk=instance.pk).first()
        instance._audit_antes = _campos(anterior) if anterior is not None else None


def _post_save(sender, instance, created, **kwargs):
    if kwargs.get('raw') or (sender.__name__ in SOLO_SI and not SOLO_SI[sender.__name__](instance)):
        return
    if kwargs.get('update_fields') and not (set(kwargs['update_fields']) - IGNORAR):
        return  # recálculo interno
    ahora = _campos(instance)
    if created:
        registrar('CREAR', instance)
        return
    antes = getattr(instance, '_audit_antes', None) or {}
    cambios = {k: [_mostrar(antes.get(k)), _mostrar(v)] for k, v in ahora.items() if not _iguales(antes.get(k), v)}
    if not cambios:
        return
    accion = 'ANULAR' if any(str(v[1]).startswith('Anulad') for k, v in cambios.items() if k.lower() == 'estado') \
        else 'MODIFICAR'
    motivo = ''
    for clave in ('Motivo de anulación', 'motivo anulacion', 'Motivo anulacion'):
        if clave in cambios:
            motivo = cambios[clave][1] or ''
    registrar(accion, instance, cambios, motivo)


def _post_delete(sender, instance, **kwargs):
    if sender.__name__ in SOLO_SI and not SOLO_SI[sender.__name__](instance):
        return
    registrar('ELIMINAR', instance, {k: _mostrar(v) for k, v in _campos(instance).items()})


def conectar():
    for app, nombre in AUDITADOS:
        try:
            modelo = apps.get_model(app, nombre)
        except LookupError:
            continue
        uid = f'auditoria-{app}-{nombre}'
        pre_save.connect(_pre_save, sender=modelo, dispatch_uid=f'{uid}-pre', weak=False)
        post_save.connect(_post_save, sender=modelo, dispatch_uid=f'{uid}-post', weak=False)
        post_delete.connect(_post_delete, sender=modelo, dispatch_uid=f'{uid}-del', weak=False)
