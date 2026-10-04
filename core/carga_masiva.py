"""Carga masiva desde Excel: maestros (productos, clientes/proveedores) y saldos iniciales de inventario.

Flujo: se descarga la plantilla, se sube el archivo, se valida fila por fila sin grabar nada y, si no hay
errores, se confirma la carga (todo o nada).
"""
import unicodedata
from collections import OrderedDict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import Almacen, Producto, Tercero

D0 = Decimal('0')


class ErrorFila(Exception):
    pass


def normalizar(texto):
    texto = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()
    return ' '.join(texto.replace('_', ' ').replace('*', '').upper().split())


def _txt(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # códigos y documentos que Excel guarda como número
    return str(v).strip()


def _dec(v, campo, requerido=False, minimo=None):
    if v in (None, ''):
        if requerido:
            raise ErrorFila(f'{campo}: obligatorio.')
        return None
    try:
        d = Decimal(str(v).replace(',', '').strip())
    except InvalidOperation:
        raise ErrorFila(f'{campo}: "{v}" no es un número.')
    if minimo is not None and d < minimo:
        raise ErrorFila(f'{campo}: debe ser mayor o igual a {minimo}.')
    return d


def _elegir(v, opciones, campo, requerido=True, defecto=''):
    """opciones: {texto normalizado: valor}."""
    t = normalizar(v)
    if not t:
        if requerido and not defecto:
            raise ErrorFila(f'{campo}: obligatorio.')
        return defecto
    if t not in opciones:
        validos = ', '.join(sorted({k for k in opciones if len(k) > 2})[:10])
        raise ErrorFila(f'{campo}: "{v}" no es válido (use: {validos}).')
    return opciones[t]


def _opciones(choices, extras=None):
    ops = {}
    for valor, etiqueta in choices:
        ops[normalizar(valor)] = valor
        ops[normalizar(etiqueta)] = valor
    ops.update(extras or {})
    return ops


CLASES = _opciones(Producto.CLASES, {'SUMINISTRO': 'SUMINISTRO', 'SUMINISTROS': 'SUMINISTRO', 'ACTIVO': 'ACTIVO',
                                     'ACTIVO FIJO': 'ACTIVO', 'SEMIELABORADO': 'SEMIELABORADO'})
UNIDADES = _opciones(Producto.UNIDADES, {'UND': 'NIU', 'UNIDADES': 'NIU', 'KG': 'KGM', 'LT': 'LTR', 'M': 'MTR',
                                         'CAJA': 'BX'})
TIPOS_TERCERO = _opciones(Tercero.TIPOS)
TIPOS_DOC = _opciones(Tercero.TIPOS_DOC, {'CE': '4', 'PASAPORTE': '7'})


# ---------------------------------------------------------------- definiciones
# columna: (clave, título, obligatorio, ejemplo, ayuda)
DEFINICIONES = OrderedDict([
    ('productos', {
        'titulo': 'Productos y servicios', 'icono': 'bi-box-seam', 'modulos': ['inventario', 'compras', 'ventas'],
        'descripcion': 'Maestro de productos. Si el código se deja vacío se genera según el tipo.',
        'columnas': [
            ('codigo', 'Código', False, '', 'Vacío = automático (MP000001...). Si ya existe se actualiza.'),
            ('nombre', 'Nombre', True, 'Harina de trigo x 50 kg', ''),
            ('tipo', 'Tipo de producto', True, 'Materia prima',
             'Mercadería, Materia prima, Semi elaborado, Producto terminado, Suministros, Activo fijo o Servicio'),
            ('unidad', 'Unidad', False, 'KGM', 'NIU (unidad), KGM, LTR, MTR, BX, PK, GLL, ZZ. Vacío = NIU'),
            ('marca', 'Marca', False, '', ''),
            ('codigo_barras', 'Código de barras', False, '', ''),
            ('precio_venta', 'Precio de venta sin IGV', False, '0', ''),
            ('precio_compra', 'Precio de compra sin IGV', False, '95.50', ''),
            ('stock_minimo', 'Stock mínimo', False, '10', ''),
            ('punto_reorden', 'Punto de reorden', False, '20', ''),
            ('stock_maximo', 'Stock máximo', False, '100', ''),
        ]}),
    ('terceros', {
        'titulo': 'Clientes y proveedores', 'icono': 'bi-people', 'modulos': ['contactos', 'compras', 'ventas'],
        'descripcion': 'Se valida el RUC (dígito verificador), el DNI y el ubigeo. Si el documento ya existe se '
                       'actualiza.',
        'columnas': [
            ('tipo', 'Tipo', True, 'Proveedor', 'Cliente, Proveedor o Ambos'),
            ('tipo_doc', 'Tipo de documento', True, 'RUC', 'RUC, DNI, CE, Pasaporte u Otros'),
            ('numero_doc', 'N° documento', True, '20555555556', ''),
            ('nombre', 'Nombre / razón social', True, 'DISTRIBUIDORA EJEMPLO S.A.C.', ''),
            ('direccion', 'Dirección', False, 'Av. Principal 123', ''),
            ('ubigeo', 'Ubigeo', False, '150131', '6 dígitos INEI'),
            ('email', 'Correo', False, '', ''),
            ('telefono', 'Teléfono', False, '', ''),
            ('dias_credito', 'Días de crédito', False, '30', ''),
        ]}),
    ('saldos_cxc', {
        'titulo': 'Saldos iniciales por cobrar', 'icono': 'bi-person-down', 'modulos': ['ventas', 'finanzas'],
        'descripcion': 'Comprobantes de venta pendientes de cobro emitidos antes de usar el sistema. Se cobran '
                       'normalmente; no van al registro de ventas ni a SUNAT y se contabilizan 12 contra 5911 '
                       '(apertura). El cliente debe existir.',
        'columnas': [
            ('numero_doc', 'RUC/DNI del cliente', True, '20555555556', ''),
            ('tipo_comprobante', 'Tipo de comprobante', True, '01', '01 factura, 03 boleta, 12 ticket, 00 otros'),
            ('serie', 'Serie', True, 'F001', ''),
            ('numero', 'Número', True, '1234', ''),
            ('fecha_emision', 'Fecha de emisión', True, '15/09/2026', 'dd/mm/aaaa'),
            ('fecha_vencimiento', 'Fecha de vencimiento', False, '15/10/2026', 'Vacío = la de emisión'),
            ('moneda', 'Moneda', False, 'PEN', 'PEN o USD. Vacío = PEN'),
            ('saldo', 'Saldo pendiente', True, '1180.00', 'Importe que falta cobrar (con IGV), en la moneda'),
            ('tipo_cambio', 'Tipo de cambio', False, '', 'Solo USD. Vacío = T.C. venta de la fecha de emisión'),
        ]}),
    ('saldos_cxp', {
        'titulo': 'Saldos iniciales por pagar', 'icono': 'bi-person-up', 'modulos': ['compras', 'finanzas'],
        'descripcion': 'Comprobantes de compra pendientes de pago recibidos antes de usar el sistema. Se pagan '
                       'normalmente; no van al registro de compras y se contabilizan 5911 (apertura) contra 42. El '
                       'proveedor debe existir.',
        'columnas': [
            ('numero_doc', 'RUC/DNI del proveedor', True, '20555555556', ''),
            ('tipo_comprobante', 'Tipo de comprobante', True, '01', '01 factura, 02 recibo por honorarios, 03 boleta, '
                                                                     '12 ticket, 00 otros'),
            ('serie', 'Serie', True, 'F001', ''),
            ('numero', 'Número', True, '5678', ''),
            ('fecha_emision', 'Fecha de emisión', True, '10/09/2026', 'dd/mm/aaaa'),
            ('fecha_vencimiento', 'Fecha de vencimiento', False, '10/10/2026', 'Vacío = la de emisión'),
            ('moneda', 'Moneda', False, 'PEN', 'PEN o USD. Vacío = PEN'),
            ('saldo', 'Saldo pendiente', True, '2360.00', 'Importe que falta pagar (con IGV), en la moneda'),
            ('tipo_cambio', 'Tipo de cambio', False, '', 'Solo USD. Vacío = T.C. venta de la fecha de emisión'),
        ]}),
    ('saldos', {
        'titulo': 'Saldos iniciales de inventario', 'icono': 'bi-flag', 'modulos': ['inventario'], 'costos': True,
        'descripcion': 'Crea y confirma una operación "Saldo inicial" por almacén y fecha (contrapartida 5911).',
        'columnas': [
            ('codigo', 'Código del producto', True, 'MP000001', 'El producto debe existir y ser inventariable'),
            ('almacen', 'Código del almacén', False, 'ALM01', 'Vacío = almacén principal'),
            ('cantidad', 'Cantidad', True, '150', ''),
            ('costo_unitario', 'Costo unitario S/ sin IGV', True, '92.30', ''),
            ('fecha', 'Fecha', False, '', 'dd/mm/aaaa. Vacío = hoy'),
        ]}),
])


# ---------------------------------------------------------------- validación por tipo
def _fila_producto(d, actualizar):
    nombre = _txt(d.get('nombre'))
    if not nombre:
        raise ErrorFila('Nombre: obligatorio.')
    clase = _elegir(d.get('tipo'), CLASES, 'Tipo de producto')
    codigo = _txt(d.get('codigo')).upper()
    existente = Producto.objects.filter(codigo=codigo).first() if codigo else None
    if existente and not actualizar:
        raise ErrorFila(f'El código {codigo} ya existe (marque "Actualizar existentes" para modificarlo).')
    if existente and existente.clase != clase:
        raise ErrorFila(f'El producto {codigo} es {existente.get_clase_display()}: el tipo no se puede cambiar.')
    datos = {'nombre': nombre[:200], 'clase': clase,
             'unidad': _elegir(d.get('unidad'), UNIDADES, 'Unidad', requerido=False, defecto='NIU'),
             'marca': _txt(d.get('marca'))[:80], 'codigo_barras': _txt(d.get('codigo_barras'))[:40]}
    for campo, titulo in (('precio_venta', 'Precio de venta'), ('precio_compra', 'Precio de compra'),
                          ('stock_minimo', 'Stock mínimo'), ('punto_reorden', 'Punto de reorden'),
                          ('stock_maximo', 'Stock máximo')):
        valor = _dec(d.get(campo), titulo, minimo=D0)
        if valor is not None:
            datos[campo] = str(valor)
    return {'accion': 'Actualizar' if existente else 'Nuevo', 'codigo': codigo, 'datos': datos,
            'resumen': f'{codigo or "(automático)"} · {nombre} · {dict(Producto.CLASES)[clase]}'}


def _fila_tercero(d, actualizar):
    from .forms import error_ruc
    from . import ubigeo
    tipo = _elegir(d.get('tipo'), TIPOS_TERCERO, 'Tipo')
    tipo_doc = _elegir(d.get('tipo_doc'), TIPOS_DOC, 'Tipo de documento')
    numero = _txt(d.get('numero_doc'))
    if not numero:
        raise ErrorFila('N° documento: obligatorio.')
    if tipo_doc == '6' and error_ruc(numero):
        raise ErrorFila(error_ruc(numero))
    if tipo_doc == '1' and (len(numero) != 8 or not numero.isdigit()):
        raise ErrorFila('El DNI debe tener 8 dígitos.')
    nombre = _txt(d.get('nombre'))
    if not nombre:
        raise ErrorFila('Nombre: obligatorio.')
    ubi = _txt(d.get('ubigeo')).zfill(6) if _txt(d.get('ubigeo')) else ''
    if ubi and not ubigeo.existe(ubi):
        raise ErrorFila(f'Ubigeo {ubi} no existe.')
    email = _txt(d.get('email'))
    if email and '@' not in email:
        raise ErrorFila(f'Correo inválido: {email}.')
    existente = Tercero.objects.filter(numero_doc=numero).first()
    if existente and not actualizar:
        raise ErrorFila(f'El documento {numero} ya existe (marque "Actualizar existentes").')
    dias = _dec(d.get('dias_credito'), 'Días de crédito', minimo=D0)
    datos = {'tipo': tipo, 'tipo_doc': tipo_doc, 'numero_doc': numero, 'nombre': nombre[:200],
             'direccion': _txt(d.get('direccion'))[:250], 'ubigeo': ubi, 'email': email[:254],
             'telefono': _txt(d.get('telefono'))[:50]}
    if dias is not None:
        datos['dias_credito'] = int(dias)
    if existente and existente.tipo != tipo and 'AMBOS' not in (tipo, existente.tipo):
        datos['tipo'] = 'AMBOS'  # era cliente y ahora también proveedor (o al revés)
    return {'accion': 'Actualizar' if existente else 'Nuevo', 'codigo': numero, 'datos': datos,
            'resumen': f'{numero} · {nombre}'}


def _fila_saldo(d, actualizar):
    from inventario.cierre import error_cierre
    from .utils import a_fecha
    codigo = _txt(d.get('codigo')).upper()
    p = Producto.objects.filter(codigo=codigo).first()
    if not p:
        raise ErrorFila(f'Producto {codigo or "(vacío)"} no existe: cárguelo primero en el maestro de productos.')
    if not p.es_inventariable:
        raise ErrorFila(f'{codigo} es {p.get_clase_display()}: no lleva inventario.')
    alm_cod = _txt(d.get('almacen')).upper()
    alm = Almacen.objects.filter(codigo__iexact=alm_cod).first() if alm_cod else Almacen.principal()
    if not alm:
        raise ErrorFila(f'Almacén {alm_cod} no existe.')
    cantidad = _dec(d.get('cantidad'), 'Cantidad', requerido=True)
    if cantidad <= 0:
        raise ErrorFila('Cantidad: debe ser mayor a cero.')
    costo = _dec(d.get('costo_unitario'), 'Costo unitario', requerido=True, minimo=D0)
    try:
        fecha = a_fecha(d.get('fecha')) if d.get('fecha') not in (None, '') else date.today()
    except ValueError as exc:
        raise ErrorFila(str(exc))
    if fecha > date.today():
        raise ErrorFila('La fecha no puede ser futura.')
    if error_cierre(fecha):
        raise ErrorFila(error_cierre(fecha))
    return {'accion': 'Saldo inicial', 'codigo': codigo,
            'datos': {'producto': p.pk, 'almacen': alm.pk, 'cantidad': str(cantidad), 'costo': str(costo),
                      'fecha': fecha.isoformat()},
            'resumen': f'{codigo} · {p.nombre} · {alm.nombre} · {cantidad:,.2f} × S/ {costo:,.4f}'}


def _fila_documento(d, es_venta):
    from .utils import a_fecha
    numero_doc = _txt(d.get('numero_doc'))
    tipos_tercero = ['CLIENTE', 'AMBOS'] if es_venta else ['PROVEEDOR', 'AMBOS']
    tercero = Tercero.objects.filter(numero_doc=numero_doc).first()
    if not tercero:
        raise ErrorFila(f'{"Cliente" if es_venta else "Proveedor"} {numero_doc or "(vacío)"} no existe: cárguelo '
                        f'primero en clientes y proveedores.')
    if tercero.tipo not in tipos_tercero:
        raise ErrorFila(f'{tercero.nombre} está registrado como {tercero.get_tipo_display().lower()}.')
    tipo = _txt(d.get('tipo_comprobante')).zfill(2)
    permitidos = ('01', '03', '12', '00') if es_venta else ('01', '02', '03', '12', '14', '00')
    if tipo not in permitidos:
        raise ErrorFila(f'Tipo de comprobante "{tipo}" no válido (use {", ".join(permitidos)}).')
    serie, numero = _txt(d.get('serie')).upper(), _txt(d.get('numero'))
    if not serie or not numero:
        raise ErrorFila('Serie y número son obligatorios.')
    try:
        emision = a_fecha(d.get('fecha_emision'))
        vence = a_fecha(d.get('fecha_vencimiento')) if d.get('fecha_vencimiento') not in (None, '') else emision
    except ValueError as exc:
        raise ErrorFila(str(exc))
    if emision > date.today():
        raise ErrorFila('La fecha de emisión no puede ser futura.')
    moneda = (_txt(d.get('moneda')) or 'PEN').upper()
    if moneda not in ('PEN', 'USD'):
        raise ErrorFila('Moneda: PEN o USD.')
    saldo = _dec(d.get('saldo'), 'Saldo pendiente', requerido=True)
    if saldo <= 0:
        raise ErrorFila('Saldo pendiente: debe ser mayor a cero.')
    tc = _dec(d.get('tipo_cambio'), 'Tipo de cambio', minimo=Decimal('0.001')) if moneda == 'USD' else Decimal('1')
    if tc is None:
        from .tipo_cambio import venta_del_dia
        tc = venta_del_dia(emision)
    from compras.models import Compra
    from ventas.models import Venta
    modelo = Venta if es_venta else Compra
    filtro = {'tipo_comprobante': tipo, 'serie': serie, 'numero': numero}
    if not es_venta:
        filtro['tercero'] = tercero
    if modelo.objects.filter(**filtro).exists():
        raise ErrorFila(f'El comprobante {serie}-{numero} ya está registrado.')
    return {'accion': 'Saldo inicial', 'codigo': f'{tercero.numero_doc}-{tipo}-{serie}-{numero}',
            'datos': {'tercero': tercero.pk, 'tipo': tipo, 'serie': serie, 'numero': numero,
                      'emision': emision.isoformat(), 'vence': vence.isoformat(), 'moneda': moneda,
                      'saldo': str(saldo), 'tc': str(tc)},
            'resumen': f'{tercero.nombre} · {tipo} {serie}-{numero} · vence {vence:%d/%m/%Y} · '
                       f'{"US$" if moneda == "USD" else "S/"} {saldo:,.2f}'}


VALIDADORES = {'productos': _fila_producto, 'terceros': _fila_tercero, 'saldos': _fila_saldo,
               'saldos_cxc': lambda d, a: _fila_documento(d, True), 'saldos_cxp': lambda d, a: _fila_documento(d, False)}


def _crear_documentos(filas, es_venta):
    from compras.models import Compra, CompraItem
    from ventas.models import Venta, VentaItem
    modelo, item_modelo = (Venta, VentaItem) if es_venta else (Compra, CompraItem)
    total = Decimal('0')
    for f in filas:
        d = f['datos']
        saldo = Decimal(d['saldo'])
        extra = {'descontar_stock': False} if es_venta else {'ingresar_almacen': False, 'clasificacion': 'GASTO'}
        doc = modelo(tercero_id=d['tercero'], tipo_comprobante=d['tipo'], serie=d['serie'], numero=d['numero'],
                     fecha_emision=date.fromisoformat(d['emision']), fecha_vencimiento=date.fromisoformat(d['vence']),
                     moneda=d['moneda'], tipo_cambio=Decimal(d['tc']), tipo_operacion='INAFECTA',
                     forma_pago='CREDITO' if d['vence'] > d['emision'] else 'CONTADO', es_saldo_inicial=True,
                     glosa='Saldo inicial cargado desde Excel', **extra)
        if es_venta:
            doc.estado_sunat = 'NO_ENVIADO'
            doc.sunat_descripcion = 'Saldo inicial: no se envía a SUNAT.'
        doc.save()
        item_modelo.objects.create(documento=doc, descripcion='Saldo inicial pendiente', cantidad=1,
                                   precio_unitario=saldo)
        doc.calcular_totales()
        doc.retencion_monto = doc.percepcion_monto = doc.detraccion_monto = Decimal('0')  # el saldo ya es neto
        doc.save()
        total += doc.total_pen
    return total


def leer_archivo(archivo, tipo):
    """[{'n': fila excel, 'datos': {clave: valor}}] con encabezados normalizados (acepta clave o título)."""
    from openpyxl import load_workbook
    definicion = DEFINICIONES[tipo]
    alias = {}
    for clave, titulo, *_ in definicion['columnas']:
        alias[normalizar(clave)] = clave
        alias[normalizar(titulo)] = clave
    try:
        wb = load_workbook(archivo, data_only=True, read_only=True)
    except Exception as exc:
        raise ErrorFila(f'No se pudo leer el Excel (.xlsx): {exc}')
    ws = wb.worksheets[0]
    filas = list(ws.iter_rows(values_only=True))
    if not filas:
        raise ErrorFila('El archivo está vacío.')
    enc = [alias.get(normalizar(c)) for c in filas[0]]
    faltan = [titulo for clave, titulo, req, *_ in definicion['columnas'] if req and clave not in enc]
    if faltan:
        raise ErrorFila(f'Faltan las columnas: {", ".join(faltan)}. Use la plantilla.')
    salida = []
    for n, valores in enumerate(filas[1:], start=2):
        if not any(v not in (None, '') for v in valores):
            continue
        salida.append({'n': n, 'datos': {c: v for c, v in zip(enc, valores) if c}})
    if len(salida) > 5000:
        raise ErrorFila('Máximo 5,000 filas por archivo.')
    return salida


def validar(archivo, tipo, actualizar=False):
    """(filas, errores): cada fila con accion/resumen/datos o error. No graba nada."""
    validador = VALIDADORES[tipo]
    resultado, errores, vistos = [], 0, {}
    for fila in leer_archivo(archivo, tipo):
        try:
            r = validador(fila['datos'], actualizar)
            clave = r['codigo']
            if tipo != 'saldos' and clave and clave in vistos:
                raise ErrorFila(f'Repetido en el archivo (fila {vistos[clave]}).')
            if clave:
                vistos[clave] = fila['n']
            resultado.append({'n': fila['n'], **r})
        except ErrorFila as exc:
            errores += 1
            resultado.append({'n': fila['n'], 'error': str(exc), 'resumen': ' · '.join(
                _txt(v) for v in fila['datos'].values() if v not in (None, ''))[:120]})
    return resultado, errores


# ---------------------------------------------------------------- grabación
def cargar(tipo, filas, usuario):
    """Graba las filas validadas (todo o nada). Devuelve un texto con el resumen."""
    with transaction.atomic():
        if tipo == 'productos':
            nuevos = actualizados = 0
            for f in filas:
                p = Producto.objects.filter(codigo=f['codigo']).first() if f['codigo'] else None
                if p is None:
                    p, nuevos = Producto(codigo=f['codigo']), nuevos + 1
                else:
                    actualizados += 1
                for campo, valor in f['datos'].items():
                    setattr(p, campo, Decimal(valor) if campo in ('precio_venta', 'precio_compra', 'stock_minimo',
                                                                   'punto_reorden', 'stock_maximo') else valor)
                p.save()
            return f'{nuevos} productos nuevos y {actualizados} actualizados.'
        if tipo == 'terceros':
            nuevos = actualizados = 0
            for f in filas:
                t = Tercero.objects.filter(numero_doc=f['codigo']).first()
                if t is None:
                    t, nuevos = Tercero(), nuevos + 1
                else:
                    actualizados += 1
                for campo, valor in f['datos'].items():
                    setattr(t, campo, valor)
                t.save()
            return f'{nuevos} clientes/proveedores nuevos y {actualizados} actualizados.'
        if tipo in ('saldos_cxc', 'saldos_cxp'):
            total = _crear_documentos(filas, tipo == 'saldos_cxc')
            return (f'{len(filas)} documentos por {"cobrar" if tipo == "saldos_cxc" else "pagar"} cargados '
                    f'(S/ {total:,.2f}).')
        if tipo == 'saldos':
            from inventario import servicios
            from inventario.models import Operacion, TipoOperacion
            tipo_op = TipoOperacion.objects.get(codigo='SALDO_INI')
            grupos = OrderedDict()
            for f in filas:
                grupos.setdefault((f['datos']['almacen'], f['datos']['fecha']), []).append(f['datos'])
            numeros = []
            for (almacen, fecha), lineas in grupos.items():
                op = Operacion.objects.create(tipo=tipo_op, fecha=date.fromisoformat(fecha), almacen_destino_id=almacen,
                                              referencia='Carga masiva', creado_por=usuario,
                                              glosa='Saldos iniciales cargados desde Excel')
                for d in lineas:
                    op.items.create(producto_id=d['producto'], cantidad=Decimal(d['cantidad']),
                                    costo_unitario=Decimal(d['costo']))
                servicios.confirmar(op, usuario)
                numeros.append(op.numero)
            return f'{len(filas)} saldos cargados en {len(numeros)} operación(es): {", ".join(numeros)}.'
    raise ValueError(tipo)


def plantilla(tipo):
    """Libro Excel con la hoja de datos (encabezados y ejemplo) e instrucciones."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    definicion = DEFINICIONES[tipo]
    wb = Workbook()
    ws = wb.active
    ws.title = 'Datos'
    ws.append([c[0] for c in definicion['columnas']])
    ws.append([c[3] for c in definicion['columnas']])
    for celda, col in zip(ws[1], definicion['columnas']):
        celda.font = Font(bold=True, color='FFFFFF')
        celda.fill = PatternFill('solid', fgColor='0F3D2E' if col[2] else '1F7A52')
        ws.column_dimensions[celda.column_letter].width = max(14, len(col[1]) + 4)
    ayuda = wb.create_sheet('Instrucciones')
    ayuda.append([definicion['titulo']])
    ayuda['A1'].font = Font(bold=True, size=13)
    ayuda.append([definicion['descripcion']])
    ayuda.append(['Borre la fila de ejemplo. Las columnas en verde oscuro son obligatorias.'])
    ayuda.append([])
    ayuda.append(['Columna', 'Descripción', 'Obligatoria', 'Notas'])
    for c in ayuda[5]:
        c.font = Font(bold=True)
    for clave, titulo, req, _, nota in definicion['columnas']:
        ayuda.append([clave, titulo, 'Sí' if req else 'No', nota])
    for col, ancho in zip('ABCD', (18, 30, 12, 80)):
        ayuda.column_dimensions[col].width = ancho
    return wb
