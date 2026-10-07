"""Migración inicial desde los Excel del sistema anterior (Odoo): maestros y saldos a una fecha de corte.

    python manage.py migrar_excels "<carpeta con los Excel>" --corte 2026-09-27 [--reporte observaciones.xlsx]

Lee los archivos tal como están (no se conecta a ningún sistema):
  Data_Productos_API.xlsx ............ maestro de productos
  Data_Contactos_API.xlsx ............ clientes y proveedores (y listas de precios asignadas)
  Data_Contable_2026.xlsx ............ plan de cuentas y centros de costo
  Data_Saldo(Valorado)_Scraping.xlsx . stock valorizado por almacén (saldo inicial)
  Data_Saldo(Valorado)xlote_Scraping.xlsx  reparto del stock en lotes
  Data_Kardex_API_2025.xlsx .......... nombres de los almacenes
  Data_FacturacionVentas_API_2025_2026.xlsx  comprobantes de venta sin pagar (saldo por cobrar)

Todo en una transacción: si algo falla no queda nada a medias. Lo que no se puede cargar (saldos negativos, RUC
inválidos, pagos parciales sin saldo, etc.) queda en el Excel de observaciones para revisarlo.
"""
import os
import re
import time
from collections import Counter, OrderedDict, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

D0 = Decimal('0')

UNIDADES = {'unidades': 'NIU', 'kg': 'KGM', 'm': 'MTR', 'gal': 'GLL', 'l': 'LTR', 'horas': 'HUR', 'm³': 'MTQ',
            'millares': 'MIL', 'rollo': 'RO', 'paquete': 'PK'}
TIPOS_DOC = {'RUC': '6', 'DNI': '1', 'Cédula Extranjera': '4', 'Pasaporte': '7'}
CLASES = [  # (prefijo de la categoría de Odoo, clase en Ceiba)
    ('PT ', 'PRODUCTO_TERMINADO'), ('PROD. EN PROCESO', 'SEMIELABORADO'), ('MATERIA PRIMA', 'MATERIA_PRIMA'),
    ('ENVASES Y EMBALAJES', 'MATERIA_PRIMA'), ('MERCADERIAS', 'MERCADERIA'), ('ACTIVO FIJO', 'ACTIVO'),
    ('SERVICIO', 'SERVICIO'), ('GASTOS', 'SERVICIO'), ('ACUERDOS', 'SERVICIO'), ('BONIFICACION', 'SERVICIO'),
    ('DESCUENTO', 'SERVICIO'), ('DIFERENCIA', 'SERVICIO'), ('BENEFICIO', 'SERVICIO'),
]
TIPO_CENTRO = [('921', 'PRODUCCION'), ('922', 'SERVICIO'), ('92', 'PRODUCCION'), ('94', 'ADMINISTRACION'),
               ('95', 'VENTAS'), ('97', 'FINANZAS')]
TIPO_VENTA = {'Factura': '01', 'Boleta': '03'}


def _txt(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    texto = str(v).replace('\xa0', ' ').strip()
    return '' if texto in ('None', '-') else texto


def _dec(v):
    if v in (None, ''):
        return D0
    try:
        return Decimal(str(v).replace(',', '.') if isinstance(v, str) and v.count(',') == 1 and '.' not in v
                       else str(v).replace(',', ''))
    except InvalidOperation:
        return D0


def _fecha(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    texto = _txt(v)[:10]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    return None


class Command(BaseCommand):
    help = 'Migra maestros y saldos iniciales desde los Excel del sistema anterior (Odoo).'

    def add_arguments(self, parser):
        parser.add_argument('carpeta')
        parser.add_argument('--corte', default='', help='Fecha de los saldos (AAAA-MM-DD). Vacío = hoy')
        parser.add_argument('--reporte', default='observaciones_migracion.xlsx')
        parser.add_argument('--simular', action='store_true', help='Valida y genera el reporte sin grabar')
        parser.add_argument('--ruc', default='', help='RUC de la empresa (vacío = el registrado en Ajustes › Empresa)')
        parser.add_argument('--razon-social', default='', help='Vacío = la registrada en Ajustes › Empresa')

    # ---------------------------------------------------------------- utilidades
    def _filas(self, nombre, columnas=None):
        from openpyxl import load_workbook
        ruta = os.path.join(self.carpeta, nombre)
        if not os.path.exists(ruta):
            raise CommandError(f'No existe {ruta}')
        wb = load_workbook(ruta, read_only=True, data_only=True)
        it = wb.worksheets[0].iter_rows(values_only=True)
        enc = [_txt(c) for c in next(it)]
        if columnas:
            faltan = [c for c in columnas if c not in enc]
            if faltan:
                raise CommandError(f'{nombre}: faltan las columnas {faltan}')
            ix = [enc.index(c) for c in columnas]
            for f in it:
                yield dict(zip(columnas, (f[i] if i < len(f) else None for i in ix)))
        else:
            for f in it:
                yield dict(zip(enc, f))
        wb.close()

    def observar(self, hoja, *fila):
        self.obs[hoja].append(fila)

    def paso(self, texto):
        self.stdout.write(f'[{time.time() - self.inicio:6.0f}s] {texto}')

    # ---------------------------------------------------------------- principal
    def handle(self, carpeta, corte, reporte, simular, ruc, razon_social, **_):
        self.carpeta, self.inicio = carpeta, time.time()
        from core.models import Empresa
        actual = Empresa.actual()  # los datos de la empresa no van en el código: parámetros o los ya registrados
        self.ruc, self.razon_social = ruc or actual.ruc, razon_social or actual.razon_social
        self.corte = date.fromisoformat(corte) if corte else date.today()
        if self.corte > date.today():
            raise CommandError('La fecha de corte no puede ser futura.')
        self.obs = defaultdict(list)
        self.resumen = OrderedDict()
        from django.contrib.auth.models import User
        self.usuario = User.objects.filter(is_superuser=True).first()
        try:
            with transaction.atomic():
                self.empresa()
                self.almacenes()
                self.cuentas_y_centros()
                self.productos()
                self.terceros()
                self.stock()
                self.por_cobrar()
                if simular:
                    raise _Simulacion()
        except _Simulacion:
            self.paso('Simulación: no se grabó nada.')
        self.escribir_reporte(reporte)
        for k, v in self.resumen.items():
            self.stdout.write(f'  {k}: {v}')
        self.stdout.write(self.style.SUCCESS(f'Reporte de observaciones: {os.path.abspath(reporte)}'))

    # ---------------------------------------------------------------- pasos
    def empresa(self):
        from core.models import Empresa
        e = Empresa.actual()
        e.ruc, e.razon_social = self.ruc, self.razon_social
        e.save()
        self.resumen['Empresa'] = f'{self.razon_social} (RUC {self.ruc})'

    def almacenes(self):
        """Ubicaciones de Odoo ('LPROD/Existencias', 'LPROD/TRANSITO') -> almacén por código; nombre desde el kardex."""
        from core.models import Almacen
        ubicaciones = set()
        self.saldos = list(self._filas('Data_Saldo(Valorado)_Scraping.xlsx'))
        self.lotes = list(self._filas('Data_Saldo(Valorado)xlote_Scraping.xlsx'))
        ubicaciones |= {_txt(r['Almacen']) for r in self.saldos} | {_txt(r['N.Almacén']) for r in self.lotes}
        nombres, sin_novedad = {}, 0
        self.paso('Leyendo nombres de almacenes del kardex…')
        for r in self._filas('Data_Kardex_API_2025.xlsx', ['sede', 'almacen']):
            sede = _txt(r['sede']).split('/')[0]
            if sede and sede not in nombres:
                nombres[sede], sin_novedad = _txt(r['almacen']), 0
            else:
                sin_novedad += 1
                if sin_novedad > 150000:
                    break
        self.almacen_de = {}
        creados = 0
        for ubic in sorted(u for u in ubicaciones if u):
            sede, _, tipo = ubic.partition('/')
            transito = tipo.upper() == 'TRANSITO'
            codigo = (f'{sede}-TR' if transito else sede)[:10]
            nombre = nombres.get(sede) or sede
            alm, nuevo = Almacen.objects.get_or_create(codigo=codigo, defaults={
                'nombre': f'{nombre} (tránsito)' if transito else nombre})
            creados += nuevo
            self.almacen_de[ubic] = alm
        if not Almacen.objects.filter(es_principal=True).exists():
            principal = self.almacen_de.get('LPTER/Existencias') or next(iter(self.almacen_de.values()))
            Almacen.objects.filter(pk=principal.pk).update(es_principal=True)
        self.resumen['Almacenes'] = f'{creados} creados ({len(self.almacen_de)} ubicaciones)'
        self.paso(self.resumen['Almacenes'])

    def cuentas_y_centros(self):
        from contabilidad.models import CentroCosto, CuentaContable
        import json
        origen = os.path.join(self.carpeta, 'Data_Contable_2026.xlsx')
        cache = os.path.join('migracion_local', f'cache_contable_{int(os.path.getmtime(origen))}.json')
        if os.path.exists(cache):
            with open(cache, encoding='utf-8') as fh:
                cuentas, centros = json.load(fh)
        else:
            self.paso('Leyendo plan de cuentas y centros de costo de la data contable…')
            cuentas, centros = {}, {}
            for r in self._filas('Data_Contable_2026.xlsx', ['CtaCont', 'DescCtaCont', 'Centro_costo']):
                cta = _txt(r['CtaCont'])
                if cta.isdigit():
                    cuentas.setdefault(cta, _txt(r['DescCtaCont']))
                for cc in _txt(r['Centro_costo']).split(', '):
                    m = re.match(r'\[(\d+)\]\s*(.+)', cc)
                    if m:
                        centros.setdefault(m.group(1), ' '.join(m.group(2).split()))
            if os.path.isdir('migracion_local') and os.path.getsize(origen) > 10_000_000:  # solo archivos grandes
                with open(cache, 'w', encoding='utf-8') as fh:
                    json.dump([cuentas, centros], fh, ensure_ascii=False)
        for r in self._filas('Data_Cuenta_Planillas.xlsx'):  # cuentas de planilla y su centro de costo
            cta, cc = _txt(r.get('CTA')), _txt(r.get('CECO'))
            if cta.isdigit():
                cuentas.setdefault(cta, _txt(r.get('Subcategoria')) or _txt(r.get('CATEGORIA')))
            if cc.isdigit():
                centros.setdefault(cc, _txt(r.get('CATEGORIA')) or cc)
        nuevas = 0
        for codigo, nombre in sorted(cuentas.items()):
            _, creada = CuentaContable.objects.get_or_create(codigo=codigo[:12], defaults={
                'nombre': (nombre or codigo)[:200],
                'naturaleza': 'ACREEDORA' if codigo[0] in '45' or codigo[:2] in ('39', '49', '59') or
                codigo[0] == '7' and codigo[:2] != '79' else 'DEUDORA'})
            nuevas += creada
        nuevos_cc = 0
        for codigo, nombre in sorted(centros.items()):
            tipo = next((t for p, t in TIPO_CENTRO if codigo.startswith(p)), 'ADMINISTRACION')
            _, creado = CentroCosto.objects.get_or_create(codigo=codigo[:10], defaults={'nombre': nombre[:100],
                                                                                        'tipo': tipo})
            nuevos_cc += creado
        self.resumen['Plan de cuentas'] = f'{nuevas} cuentas nuevas de {len(cuentas)} usadas en Odoo'
        self.resumen['Centros de costo'] = f'{nuevos_cc} creados'
        self.paso(f"{self.resumen['Plan de cuentas']} · {self.resumen['Centros de costo']}")

    def _clase(self, categoria, tipo_producto):
        if tipo_producto == 'Servicio':
            return 'SERVICIO'
        cat = categoria.upper()
        for prefijo, clase in CLASES:
            if cat.startswith(prefijo.upper()):
                return clase
        return 'SUMINISTRO'  # repuestos, suministros, herramientas, máquinas, chatarra…

    def productos(self):
        from core.models import Producto
        self.paso('Productos…')
        precio_venta = {}
        for r in self.saldos:
            cod = _txt(r['Codigo Producto']).upper()
            precio_venta[cod] = max(precio_venta.get(cod, D0), _dec(r['Precio De Venta']))
        vistos, nuevos, actualizados = set(), 0, 0
        filas = list(self._filas('Data_Productos_API.xlsx'))
        for r in filas:
            codigo = _txt(r['codigo_producto']).upper()
            nombre = ' '.join(_txt(r['nombre_producto']).split())
            if not codigo:
                self.observar('Productos omitidos', '', nombre, 'Sin código en Odoo')
                continue
            if codigo in vistos:
                self.observar('Productos omitidos', codigo, nombre, 'Código repetido: se tomó la primera fila')
                continue
            vistos.add(codigo)
            categoria = _txt(r['categoria_producto'])
            clase = self._clase(categoria, _txt(r['tipo_producto']))
            unidad = UNIDADES.get(_txt(r['unidad_de_producto']).lower(), 'NIU')
            if clase == 'SERVICIO':
                unidad = 'ZZ'
            p = Producto.objects.filter(codigo=codigo).first()
            if p is None:
                p, nuevos = Producto(codigo=codigo, clase=clase), nuevos + 1
            else:
                actualizados += 1
            p.nombre = (nombre or codigo)[:200]
            p.unidad = unidad
            p.unidad_compra = UNIDADES.get(_txt(r['unidad_de_compra']).lower(), unidad) if clase != 'SERVICIO' \
                else unidad
            p.marca = _txt(r['marca_producto'])[:80]
            p.codigo_barras = _txt(r['codigo_de_barras'])[:40]
            p.peso = _dec(r['peso_kg'])
            p.puede_venderse = _txt(r['puede_vender']).upper() == 'SI' and clase != 'ACTIVO'
            p.puede_comprarse = _txt(r['puede_comprar']).upper() == 'SI'
            seguimiento = _txt(r['trazabilidad_seguimiento'])
            p.control = 'LOTE' if seguimiento == 'por lotes' else 'SERIE' if 'serie' in seguimiento else ''
            if clase in ('SERVICIO', 'ACTIVO'):
                p.control = ''
            p.precio_venta = precio_venta.get(codigo, p.precio_venta or D0)
            p.descripcion = (_txt(r['descripcion_venta']) or _txt(r['descripcion_compra']))[:1000]
            p.save()
        # productos con stock que no figuran en el maestro
        for r in self.saldos:
            codigo = _txt(r['Codigo Producto']).upper()
            if codigo and not Producto.objects.filter(codigo=codigo).exists():
                Producto.objects.create(codigo=codigo, nombre=_txt(r['Producto'])[:200] or codigo,
                                        clase=self._clase(_txt(r['Categoría de Producto N1']), ''),
                                        unidad=UNIDADES.get(_txt(r['Unidad']).lower(), 'NIU'))
                nuevos += 1
                self.observar('Productos omitidos', codigo, _txt(r['Producto']),
                              'No estaba en el maestro: se creó desde el archivo de saldos (revise el tipo)')
        self.resumen['Productos'] = f'{nuevos} nuevos, {actualizados} actualizados'
        self.paso(self.resumen['Productos'])

    def terceros(self):
        from core.forms import error_ruc
        from core.models import Tercero
        from core import ubigeo
        from ventas.models import ListaPrecios
        self.paso('Clientes y proveedores…')
        listas, self.empleados = {}, {}
        por_doc = OrderedDict()
        for r in self._filas('Data_Contactos_API.xlsx'):
            if _txt(r['tipo_contacto']) != 'Contacto':
                continue  # direcciones de entrega o factura de un contacto
            cliente, proveedor = _txt(r['es_cliente']) == 'SI', _txt(r['es_proveedor']) == 'SI'
            numero = re.sub(r'\s', '', _txt(r['numero_documento']))
            nombre = ' '.join((_txt(r['nombre_completo']) or _txt(r['nombre'])).split())
            if numero and _txt(r['es_empleado']) == 'SI' and _txt(r['tipo_documento']) in ('DNI', 'Cédula Extranjera'):
                self.empleados.setdefault(numero, (nombre, _txt(r['tipo_documento']), _txt(r['email'])))
            if not (cliente or proveedor):
                if _txt(r['es_empleado']) != 'SI':
                    self.observar('Contactos omitidos', numero, nombre, 'Ni cliente ni proveedor')
                continue  # los trabajadores van al módulo de planillas
            if not numero:
                self.observar('Contactos omitidos', '', nombre, 'Sin número de documento')
                continue
            previo = por_doc.get(numero)
            if previo:
                previo['cliente'] |= cliente
                previo['proveedor'] |= proveedor
                continue
            por_doc[numero] = {'r': r, 'cliente': cliente, 'proveedor': proveedor, 'nombre': nombre}
        nuevos = actualizados = 0
        for numero, d in por_doc.items():
            r = d['r']
            tipo_doc = TIPOS_DOC.get(_txt(r['tipo_documento']), '0')
            if tipo_doc == '6' and error_ruc(numero):
                self.observar('Contactos con observación', numero, d['nombre'], f'Cargado, pero: {error_ruc(numero)}')
            if tipo_doc == '1' and (len(numero) != 8 or not numero.isdigit()):
                self.observar('Contactos con observación', numero, d['nombre'], 'Cargado, pero el DNI no tiene 8 dígitos')
            ubi = _txt(r['codigo_postal'])
            ubi = ubi.zfill(6) if ubi.isdigit() and ubigeo.existe(ubi.zfill(6)) else ''
            m = re.match(r'(\d+)', _txt(r['termino_de_pago']))
            email = _txt(r['email']).split(';')[0].split(',')[0].strip()
            t = Tercero.objects.filter(numero_doc=numero[:15]).first()
            if t is None:
                t, nuevos = Tercero(numero_doc=numero[:15]), nuevos + 1
            else:
                actualizados += 1
            tipo = 'AMBOS' if d['cliente'] and d['proveedor'] else 'CLIENTE' if d['cliente'] else 'PROVEEDOR'
            t.tipo = tipo if not t.pk or t.tipo == tipo else 'AMBOS'  # al volver a migrar no se le quita un rol
            t.tipo_doc = tipo_doc
            t.nombre = d['nombre'][:200] or numero
            t.direccion = (_txt(r['direccion_completa']) or _txt(r['calle']))[:250]
            t.ubigeo = ubi
            t.zona = _txt(r['equipo_de_venta'])[:80]
            t.email = email[:254] if '@' in email else ''
            t.telefono = ' / '.join(x for x in (_txt(r['telefono']), _txt(r['movil'])) if x)[:50]
            t.dias_credito = int(m.group(1)) if m else 0
            lista = _txt(r['lista_de_precios'])
            if d['cliente'] and lista and not lista.startswith('Tarifa base'):
                if lista not in listas:
                    listas[lista], _ = ListaPrecios.objects.get_or_create(
                        nombre=lista[:100], defaults={'codigo': f'LP{len(listas) + 1:02d}'})
                t.lista_precios = listas[lista]
            t.save()
        self.resumen['Clientes y proveedores'] = f'{nuevos} nuevos, {actualizados} actualizados'
        self.trabajadores()
        self.resumen['Listas de precios'] = f'{len(listas)} creadas (sin precios: cárguelos en Ventas › Listas de precios)'
        self.paso(self.resumen['Clientes y proveedores'])

    def trabajadores(self):
        """Contactos marcados como empleados en Odoo -> trabajadores 'por completar' (sin sueldo ni ingreso: los
        datos laborales se cargan con Carga masiva › Trabajadores)."""
        from planillas.models import Trabajador
        nuevos = 0
        for numero, (nombre, tipo, email) in self.empleados.items():
            numero = numero.rstrip('.')
            if tipo == 'DNI' and (len(numero) != 8 or not numero.isdigit()):
                self.observar('Contactos con observación', numero, nombre, 'Trabajador no creado: DNI inválido')
                continue
            if Trabajador.objects.filter(numero_doc=numero).exists():
                continue
            partes = nombre.replace(',', ' ').split()
            paterno, materno = (partes[0], partes[1]) if len(partes) >= 3 else (partes[0] if partes else numero, '')
            nombres = ' '.join(partes[2:] if len(partes) >= 3 else partes[1:]) or '-'
            Trabajador.objects.create(tipo_doc='01' if tipo == 'DNI' else '04', numero_doc=numero,
                                      apellido_paterno=paterno[:60], apellido_materno=materno[:60],
                                      nombres=nombres[:80], email=email[:254] if '@' in email else '')
            nuevos += 1
        self.resumen['Trabajadores'] = (f'{nuevos} creados como "por completar" (cargue sueldo, ingreso y AFP con '
                                        f'Carga masiva › Trabajadores)')

    def stock(self):
        from core.models import Producto
        from inventario import servicios
        from inventario.models import Operacion, TipoOperacion
        self.paso('Saldos de inventario…')
        tipo_op = TipoOperacion.objects.get(codigo='SALDO_INI')
        lotes = defaultdict(list)  # (ubicación, código) -> [(lote, cantidad)]
        for r in self.lotes:
            cant = _dec(r['Stock'])
            if cant > 0:
                lotes[(_txt(r['N.Almacén']), _txt(r['Cod. Producto']).upper())].append((_txt(r['Lote']), cant))
        respaldo = {}  # costo unitario de respaldo para saldos sin valor en Odoo
        if os.path.exists(os.path.join(self.carpeta, 'Costo Productos Starsoft.xlsx')):
            from openpyxl import load_workbook
            wb = load_workbook(os.path.join(self.carpeta, 'Costo Productos Starsoft.xlsx'), read_only=True,
                               data_only=True)
            if 'Hoja1' in wb.sheetnames:
                it = wb['Hoja1'].iter_rows(values_only=True)
                enc = [_txt(c) for c in next(it)]
                for f in it:
                    r = dict(zip(enc, f))
                    costo = _dec(r.get('COSTO UNIT FINAL ODOO')) or _dec(r.get('Ultimo Costo Starsoft'))
                    if costo > 0:
                        respaldo[_txt(r.get('data.codigo_producto')).upper()] = costo
            wb.close()
        por_almacen = defaultdict(list)
        valor = D0
        for r in self.saldos:
            codigo, ubic = _txt(r['Codigo Producto']).upper(), _txt(r['Almacen'])
            cant, soles = _dec(r['Saldo Cantidad']), _dec(r['Saldo Soles'])
            cant = cant.quantize(Decimal('0.01'))  # Odoo deja residuos de coma flotante (ej. -4.5e-13)
            if cant == 0:
                continue
            if cant < 0:
                self.observar('Stock no cargado', ubic, codigo, _txt(r['Producto']), float(cant), float(soles),
                              'Saldo negativo en Odoo: regularícelo con un ajuste' if cant < 0 else 'Saldo cero')
                continue
            p = Producto.objects.get(codigo=codigo)
            if not p.es_inventariable:
                self.observar('Stock no cargado', ubic, codigo, p.nombre, float(cant), float(soles),
                              f'{p.get_clase_display()}: no lleva inventario')
                continue
            costo = (soles / cant).quantize(Decimal('0.0001')) if soles > 0 else D0
            if costo > 0:
                pass
            elif codigo in respaldo:
                costo = respaldo[codigo]
                soles = (cant * costo).quantize(Decimal('0.01'))
                self.observar('Stock con observación', ubic, codigo, p.nombre, float(cant), float(soles),
                              f'Sin valor en Odoo: costeado a S/ {costo} (archivo Costo Productos Starsoft)')
            else:
                self.observar('Stock no cargado', ubic, codigo, p.nombre, float(cant), float(soles),
                              'Sin valor en Odoo ni costo de respaldo: cárguelo con su costo en Carga masiva')
                continue
            por_almacen[self.almacen_de[ubic]].append((p, cant, costo, lotes.get((ubic, codigo), []), soles))
        from core.sustentos import guardar_archivo, vincular
        nombre = 'Data_Saldo(Valorado)_Scraping.xlsx'
        with open(os.path.join(self.carpeta, nombre), 'rb') as fh:
            sustento = guardar_archivo(None, self.usuario, datos=fh.read(), nombre=nombre,
                                       tipo='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        lineas, fallidos = 0, 0
        for alm, items in por_almacen.items():
            try:
                with transaction.atomic():
                    op = Operacion.objects.create(
                        tipo=tipo_op, fecha=self.corte, almacen_destino=alm, referencia='Migración Odoo',
                        creado_por=self.usuario,
                        glosa=f'Saldo inicial migrado del sistema anterior al {self.corte:%d/%m/%Y}')
                    n = 0
                    for p, cant, costo, reparto, _ in items:
                        for lote, vence, c in self._repartir(p, cant, reparto):
                            op.items.create(producto=p, cantidad=c, costo_unitario=costo, lote=lote,
                                            vencimiento=vence)
                            n += 1
                    vincular(op, sustento, self.usuario, 'Saldos valorados del sistema anterior (Odoo)')
                    servicios.confirmar(op, self.usuario)
                    lineas += n
                    valor += sum((i[4] for i in items), D0)
            except servicios.ErrorOperacion as exc:
                fallidos += 1
                self.observar('Stock no cargado', alm.codigo, '', '', 0, 0, f'Almacén completo no cargado: {exc}')
        self.resumen['Stock inicial'] = (f'{lineas} líneas en {len(por_almacen) - fallidos} almacenes, '
                                         f'valorizado S/ {valor:,.2f}' +
                                         (f' · {fallidos} almacenes con error (ver observaciones)' if fallidos else ''))
        self.paso(self.resumen['Stock inicial'])

    def _repartir(self, p, cant, reparto):
        """[(lote, vencimiento, cantidad)] para la línea de stock según el control del producto."""
        if not p.control:
            return [('', None, cant)]
        if p.control == 'SERIE':
            self.observar('Stock con observación', '', p.codigo, p.nombre, float(cant), 0,
                          'Por número de serie: cargado como lote "INICIAL" (registre las series al usarlas)')
            p.control = 'LOTE'
            p.save(update_fields=['control'])
        salida, resto = [], cant
        for lote, c in reparto:
            if resto <= 0:
                break
            usar = min(c, resto)
            m = re.search(r'FV\s*(\d{2})(\d{2})(\d{2,4})', lote.upper())
            vence = None
            if m:
                anio = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
                try:
                    vence = date(anio, int(m.group(2)), int(m.group(1)))
                except ValueError:
                    pass
            lote = ' '.join(re.sub(r'[,;\n]+', ' ', lote).split()).upper()  # la coma separa lotes en Ceiba
            salida.append(((lote or 'INICIAL')[:40], vence, usar))
            resto -= usar
        if resto > 0:
            salida.append(('INICIAL', None, resto))
        # mismo lote dos veces (filas repetidas): se suman
        juntos = OrderedDict()
        for lote, vence, c in salida:
            previo = juntos.get(lote)
            juntos[lote] = (vence or (previo[0] if previo else None), (previo[1] if previo else D0) + c)
        return [(lote, v, c) for lote, (v, c) in juntos.items()]

    def por_cobrar(self):
        from core.carga_masiva import ErrorFila, _crear_documentos, _fila_documento
        from core.models import Tercero
        self.paso('Saldos por cobrar…')
        docs = OrderedDict()
        for r in self._filas('Data_FacturacionVentas_API_2025_2026.xlsx', [
                'nro_documento_completo', 'tipo_de_documento', 'fecha_de_factura', 'fecha_vencimiento', 'estado',
                'estado_pago', 'contacto_factura', 'tipo_documento', 'numero_documento', 'moneda', 'tipo_de_cambio',
                'monto_total_factura']):
            clave = (_txt(r['tipo_de_documento']), _txt(r['nro_documento_completo']))
            docs.setdefault(clave, r)
        self.series(docs)
        filas, total = [], D0
        for (tipo, numero), r in docs.items():
            if _txt(r['estado']) != 'Publicado' or _txt(r['estado_pago']) in ('Pagado', 'Revertido'):
                continue
            importe = _dec(r['monto_total_factura'])
            fila_obs = (tipo, numero, _txt(r['numero_documento']), _txt(r['contacto_factura']), _txt(r['moneda']),
                        float(importe))
            if _txt(r['estado_pago']) != 'Sin Pagar':
                self.observar('Por cobrar no cargado', *fila_obs,
                              f'{_txt(r["estado_pago"])}: indique el saldo pendiente y cárguelo en Carga masiva')
                continue
            if tipo not in TIPO_VENTA:
                self.observar('Por cobrar no cargado', *fila_obs,
                              'Nota sin aplicar: regístrela como anticipo o aplíquela a una factura')
                continue
            doc = _txt(r['numero_documento'])
            if not Tercero.objects.filter(numero_doc=doc).exists():
                tipo_doc = '6' if len(doc) == 11 else '1' if len(doc) == 8 else '0'
                Tercero.objects.create(tipo='CLIENTE', tipo_doc=tipo_doc, numero_doc=doc[:15] or '-',
                                       nombre=_txt(r['contacto_factura'])[:200] or doc)
            else:
                Tercero.objects.filter(numero_doc=doc, tipo='PROVEEDOR').update(tipo='AMBOS')
            serie, _, correlativo = numero.partition('-')
            emision = _fecha(r['fecha_de_factura'])
            vence = _fecha(r['fecha_vencimiento']) or emision
            try:
                f = _fila_documento({'numero_doc': doc, 'tipo_comprobante': TIPO_VENTA[tipo], 'serie': serie,
                                     'numero': correlativo.lstrip('0') or '0', 'fecha_emision': emision,
                                     'fecha_vencimiento': max(vence, emision), 'moneda': _txt(r['moneda']),
                                     'saldo': importe, 'tipo_cambio': _dec(r['tipo_de_cambio']) or None}, True)
            except ErrorFila as exc:
                self.observar('Por cobrar no cargado', *fila_obs, str(exc))
                continue
            filas.append(f)
        total = _crear_documentos(filas, True) if filas else D0
        self.resumen['Por cobrar'] = f'{len(filas)} comprobantes sin pagar, S/ {total:,.2f}'
        self.resumen['Por pagar'] = ('No cargado: los Excel no traen las facturas de proveedor pendientes con su '
                                     'saldo (ver observaciones)')
        self.observar('Por pagar', 'Los Excel no incluyen un listado de facturas de proveedor por pagar con su saldo '
                                   'a la fecha de corte; la data contable llega solo a julio 2026. Cárguelo con '
                                   'Carga masiva › Saldos iniciales por pagar.')
        self.paso(self.resumen['Por cobrar'])

    def series(self, docs):
        """Correlativos: Ceiba continúa después del último número que emitió Odoo en cada serie."""
        from core.models import Serie
        tipos = {'Factura': '01', 'Boleta': '03', 'Nota de crédito': '07', 'Nota de Débito': '08'}
        ultimos = defaultdict(int)
        for tipo, numero in docs:
            serie, _, correlativo = numero.partition('-')
            if tipo in tipos and correlativo.isdigit() and len(serie) == 4:
                clave = (tipos[tipo], serie.upper())
                ultimos[clave] = max(ultimos[clave], int(correlativo))
        for (tipo, serie), ultimo in ultimos.items():
            s, _ = Serie.objects.get_or_create(tipo=tipo, serie=serie)
            if s.correlativo < ultimo:
                s.correlativo = ultimo
                s.save(update_fields=['correlativo'])
        self.resumen['Series de venta'] = ', '.join(f'{s} → {n}' for (_, s), n in sorted(ultimos.items()))

    # ---------------------------------------------------------------- reporte
    def escribir_reporte(self, ruta):
        from openpyxl import Workbook
        from openpyxl.styles import Font
        encabezados = {
            'Productos omitidos': ['Código', 'Producto', 'Motivo'],
            'Contactos omitidos': ['Documento', 'Nombre', 'Motivo'],
            'Contactos con observación': ['Documento', 'Nombre', 'Observación'],
            'Stock no cargado': ['Ubicación Odoo', 'Código', 'Producto', 'Cantidad', 'Saldo S/', 'Motivo'],
            'Stock con observación': ['Ubicación Odoo', 'Código', 'Producto', 'Cantidad', 'Saldo S/', 'Observación'],
            'Por cobrar no cargado': ['Tipo', 'Comprobante', 'RUC/DNI', 'Cliente', 'Moneda', 'Total', 'Motivo'],
            'Por pagar': ['Observación'],
        }
        wb = Workbook()
        ws = wb.active
        ws.title = 'Resumen'
        ws.append(['Migración desde Odoo', f'Corte {self.corte:%d/%m/%Y}'])
        ws['A1'].font = Font(bold=True, size=13)
        for k, v in self.resumen.items():
            ws.append([k, v])
        ws.append([])
        for hoja, filas in self.obs.items():
            ws.append([f'Observaciones: {hoja}', len(filas)])
        for hoja, filas in self.obs.items():
            h = wb.create_sheet(hoja[:31])
            h.append(encabezados.get(hoja, []))
            for c in h[1]:
                c.font = Font(bold=True)
            for f in filas:
                h.append(list(f))
        wb.save(ruta)


class _Simulacion(Exception):
    pass
