"""Depósito masivo de detracciones de compras (SUNAT Operaciones en Línea › pago masivo de detracciones).

Archivo plano D<RUC del adquirente><lote>.txt, según el instructivo de SUNAT (adquirente con varios proveedores, por
internet): una cabecera de 68 caracteres y un detalle de 107 por cada depósito; los textos se completan con espacios
a la derecha y los números con ceros a la izquierda; los importes van sin punto, con dos decimales.

Cabecera:  '*' | RUC (11) | razón social (35) | lote AANNNN (6) | importe total (15)
Detalle:   tipo doc. proveedor (1) | N° doc. (11) | nombre (35) | N° de proforma (9) | bien o servicio (3) |
           cuenta del BN del proveedor (11) | importe (15) | tipo de operación (2) | periodo AAAAMM (6) |
           tipo de comprobante (2) | serie (4) | número (8)

El importe de cada depósito va sin decimales (redondeo a la unidad: desde 0.50 sube), como lo permite SUNAT.
"""
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from core.models import Empresa

from .models import DepositoDetraccion, LineaDepositoDetraccion, Movimiento
from .operaciones import ErrorTesoreria, _periodo_abierto

TIPO_OPERACION = '01'  # venta de bienes o prestación de servicios gravada con el IGV


def codigos():
    """[(código SUNAT de 3 dígitos, descripción)] del catálogo 54."""
    from core.sunat import DETRACCION_TIPOS
    return [(texto[:3], texto) for _, texto in DETRACCION_TIPOS]


def codigo_de(compra):
    """Código SUNAT del bien o servicio de la compra: el del producto con mayor % de detracción."""
    from core.sunat import DETRACCION_TIPOS
    internos = dict(DETRACCION_TIPOS)
    sujetos = [i.producto for i in compra.items.select_related('producto')
               if i.producto_id and i.producto.detraccion_codigo]
    if not sujetos:
        return ''
    producto = max(sujetos, key=lambda p: p.detraccion_pct)
    return internos.get(producto.detraccion_codigo, '')[:3]


def importe_deposito(compra):
    """Detracción por depositar en soles, sin decimales."""
    pendiente = compra.detraccion_por_depositar * compra.tc_efectivo
    return pendiente.quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def compras_por_depositar(hasta=None, proveedor=None):
    """Compras con detracción pendiente de depósito (no las que ya están en un lote por pagar)."""
    from compras.models import Compra
    en_lote = LineaDepositoDetraccion.objects.filter(deposito__estado='BORRADOR').values('compra_id')
    qs = (Compra.objects.con_saldos().filter(detraccion_monto__gt=0, tipo_comprobante='01', estado='REGISTRADO',
                                             es_historico=False).exclude(pk__in=en_lote)
          .select_related('tercero').order_by('fecha_emision', 'id'))
    if hasta:
        qs = qs.filter(fecha_emision__lte=hasta)
    if proveedor:
        qs = qs.filter(tercero_id=proveedor)
    return [c for c in qs if c.detraccion_por_depositar > 0]


def cuenta_bn(tercero):
    return re.sub(r'\D', '', tercero.cuenta_detracciones or '')


@transaction.atomic
def crear_lote(cuenta, fecha, lineas, usuario, glosa=''):
    """lineas: [(compra, código SUNAT, importe en S/)] -> lote por pagar (el archivo sale de él)."""
    if not lineas:
        raise ErrorTesoreria('Elija al menos una compra.')
    if cuenta.moneda != 'PEN':
        raise ErrorTesoreria('Las detracciones se depositan en soles: elija una cuenta en soles.')
    lote = DepositoDetraccion.objects.create(cuenta=cuenta, fecha=fecha, glosa=glosa, creado_por=usuario)
    for compra, codigo, monto in lineas:
        if monto <= 0:
            raise ErrorTesoreria(f'{compra}: el importe debe ser mayor a cero.')
        if compra.detraccion_por_depositar <= 0:
            raise ErrorTesoreria(f'{compra}: su detracción ya está depositada.')
        if codigo not in dict(codigos()):
            raise ErrorTesoreria(f'{compra}: elija el bien o servicio sujeto a detracción.')
        LineaDepositoDetraccion.objects.create(deposito=lote, compra=compra, codigo=codigo, monto=monto)
    return lote


# ---------------------------------------------------------------- archivo para SUNAT
def _texto(valor, largo):
    valor = unicodedata.normalize('NFKD', str(valor or '')).encode('ascii', 'ignore').decode().upper()
    return re.sub(r'[^A-Z0-9 .,&\-/]', ' ', valor)[:largo].ljust(largo)


def _numero(valor, largo):
    return str(valor).rjust(largo, '0')[-largo:]


def _importe(monto):
    return _numero(int((Decimal(monto) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)), 15)


def errores_archivo(lote):
    """Lo que impide generar un archivo que SUNAT acepte."""
    errores = []
    ruc = Empresa.actual().ruc or ''
    if not re.fullmatch(r'\d{11}', ruc):
        errores.append('El RUC de la empresa (Ajustes › Empresa) debe tener 11 dígitos.')
    for l in lote.lineas.select_related('compra__tercero'):
        c, t = l.compra, l.compra.tercero
        if len(cuenta_bn(t)) != 11:
            errores.append(f'{t.nombre}: registre su cuenta de detracciones del Banco de la Nación (11 dígitos) en '
                           f'su ficha de Contactos.')
        if not re.fullmatch(r'\d{1,8}', (c.numero or '').lstrip('0') or '0'):
            errores.append(f'{c}: el número del comprobante debe tener hasta 8 dígitos.')
        if len(t.numero_doc or '') > 11 or not t.numero_doc:
            errores.append(f'{t.nombre}: documento de identidad no válido.')
    return list(dict.fromkeys(errores))


def archivo_txt(lote):
    """(nombre, contenido) del archivo de pago masivo para SUNAT."""
    empresa = Empresa.actual()
    lineas = list(lote.lineas.select_related('compra__tercero'))
    cabecera = '*' + _numero(empresa.ruc, 11) + _texto(empresa.razon_social, 35) + lote.lote + _importe(lote.total)
    filas = [cabecera]
    for l in lineas:
        c, t = l.compra, l.compra.tercero
        filas.append(
            (t.tipo_doc or '6')[:1] + _texto(t.numero_doc, 11) + _texto(t.nombre, 35) + '0' * 9 + l.codigo
            + _numero(cuenta_bn(t), 11) + _importe(l.monto) + TIPO_OPERACION + c.fecha_emision.strftime('%Y%m')
            + c.tipo_comprobante + _texto(c.serie, 4) + _numero((c.numero or '').lstrip('0') or '0', 8))
    return f'D{empresa.ruc}{lote.lote}.txt', '\r\n'.join(filas) + '\r\n'


# ---------------------------------------------------------------- pago y constancias
@transaction.atomic
def registrar_pago(lote, fecha, numero_operacion, constancias, usuario):
    """Registra el depósito de cada compra del lote (concepto Detracción) desde la cuenta del lote.
    constancias: {id de línea: N° de constancia}; las que falten se registran después."""
    lote = DepositoDetraccion.objects.select_for_update().get(pk=lote.pk)
    if lote.estado != 'BORRADOR':
        raise ErrorTesoreria(f'{lote} ya está {lote.get_estado_display().lower()}.')
    _periodo_abierto(fecha)
    error = lote.cuenta.error_sobregiro(lote.total, fecha=fecha)
    if error:
        raise ErrorTesoreria(error)
    for l in lote.lineas.select_related('compra__tercero'):
        compra = type(l.compra).objects.con_saldos().get(pk=l.compra_id)
        if compra.detraccion_por_depositar <= 0:
            raise ErrorTesoreria(f'{compra}: su detracción ya fue depositada; quítela del lote (anule y genere otro).')
        l.constancia = (constancias.get(l.pk) or '').strip()[:40]
        l.movimiento = Movimiento.objects.create(
            cuenta=lote.cuenta, fecha=fecha, tipo='EGRESO', concepto='DETRACCION', medio_pago='DEPOSITO',
            numero_operacion=l.constancia, tercero=compra.tercero, compra=compra, monto=l.monto, creado_por=usuario,
            glosa=(f'Depósito masivo de detracciones lote {lote.lote}'
                   + (f' (op. {numero_operacion})' if numero_operacion else ''))[:250])
        l.save(update_fields=['constancia', 'movimiento'])
    lote.fecha, lote.numero_operacion, lote.estado = fecha, numero_operacion[:40], 'PAGADO'
    lote.save(update_fields=['fecha', 'numero_operacion', 'estado'])
    return lote


@transaction.atomic
def guardar_constancias(lote, constancias):
    """Constancias que SUNAT entregó después del pago: pasan al depósito y a la compra (PLE 8.1)."""
    if lote.estado != 'PAGADO':
        raise ErrorTesoreria('Primero registre el pago del lote.')
    from compras.models import Compra
    cambiadas = 0
    for l in lote.lineas.select_related('movimiento', 'compra'):
        nuevo = (constancias.get(l.pk) or '').strip()[:40]
        if not nuevo or nuevo == l.constancia or l.movimiento is None:
            continue
        anterior = l.constancia
        l.constancia = nuevo
        l.save(update_fields=['constancia'])
        Movimiento.todos.filter(pk=l.movimiento_id).update(numero_operacion=nuevo)
        # la compra toma la constancia si no tenía o si tenía la anterior de este depósito
        Compra.objects.filter(pk=l.compra_id, detraccion_constancia__in=['', anterior]).update(
            detraccion_constancia=nuevo, detraccion_fecha=l.movimiento.fecha)
        cambiadas += 1
    return cambiadas


def leer_constancias(archivo, lote):
    """({id de línea: constancia}, [no encontradas]) desde el reporte de constancias de SUNAT o una planilla con
    serie, número y constancia (ver finanzas/constancias.py)."""
    from .constancias import emparejar_lote, leer
    return emparejar_lote(leer(archivo), lote)


@transaction.atomic
def anular_lote(lote):
    if lote.estado != 'BORRADOR':
        raise ErrorTesoreria('Solo se anula un lote por pagar. Un lote pagado se corrige anulando sus depósitos '
                             'en Tesorería › Movimientos.')
    lote.estado = 'ANULADO'
    lote.save(update_fields=['estado'])
