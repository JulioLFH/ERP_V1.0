"""Prueba de integración de punta a punta (v1.75): Obras → Compras con detracción → Tesorería (lote de detracciones
con el archivo de SUNAT, pago múltiple, aporte, transferencia) → Ventas con detracción → Tesorería (cobranza
múltiple y constancias de clientes) → Contabilidad (asientos, auxiliares, balance, EFE NIC 7, centro de costo de la
obra). Cada paso lo hace el usuario de su módulo y se comprueba que los demás no lo pueden hacer."""
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra
from contabilidad import flujo_efectivo
from contabilidad.models import Asiento, AsientoLinea, CuentaContable
from finanzas.models import Cuenta, DepositoDetraccion, Movimiento
from obras.models import Obra, Presupuesto
from ventas.models import Venta

from .models import Empresa, Producto, Tercero
from .modulos import GRUPOS

HOY = timezone.localdate()
PERIODO = HOY.strftime('%Y%m')
TC = (D('3.441'), D('3.450'))


def pdf(nombre='sustento.pdf'):
    return SimpleUploadedFile(nombre, b'%PDF-1.4 sustento', content_type='application/pdf')


def saldo_contable(codigo):
    agg = AsientoLinea.objects.filter(cuenta__codigo=codigo).aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['d'] or 0) - (agg['h'] or 0)


class CicloCompletoTest(TestCase):
    databases = {'default', 'empresa2'}  # la consolidación del grupo lee las dos empresas

    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=TC):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('gerente', 'g@x.pe', 'x')
        cls.usuarios = {}
        for nombre, modulo in (('comprador', 'compras'), ('vendedor', 'ventas'), ('tesorero', 'tesoreria'),
                               ('contador', 'contabilidad'), ('ingeniero', 'obras')):
            u = User.objects.create_user(nombre, password='Clave-segura-9')
            u.groups.add(Group.objects.get_or_create(name=GRUPOS[modulo])[0])  # sin perfil: todas sus acciones
            cls.usuarios[nombre] = u
        cls.servicio = Producto.objects.create(codigo='SRV-OBRA', nombre='Subcontrato de obra', clase='SERVICIO',
                                               unidad='ZZ', detraccion_codigo='18', detraccion_pct=D('12'))
        cls.prov1, cls.prov2 = Tercero.objects.filter(tipo='PROVEEDOR', tipo_doc='6')[:2]
        for n, p in enumerate((cls.prov1, cls.prov2), 1):
            p.cuenta_detracciones = f'00-068-00000{n}'
            p.save()
        cls.cli1, cls.cli2 = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6')[:2]
        cls.banco = Cuenta.objects.get(nombre='BCP Cta. Cte. Soles')
        cls.bn = Cuenta.objects.create(nombre='BN detracciones', banco='BN', es_detracciones=True,
                                       tipo_efectivo='RESTRINGIDO', numero='00-068-999999')
        cls.caja = Cuenta.objects.create(tipo='CAJA', nombre='Caja de obra', moneda='PEN')

    def setUp(self):
        Empresa.objects.update(exigir_orden_compra=False, bloquear_deuda_vencida=False)
        p = patch('core.tipo_cambio._consultar', return_value=TC)
        p.start()
        self.addCleanup(p.stop)

    def como(self, nombre):
        self.client.force_login(self.admin if nombre == 'gerente' else self.usuarios[nombre])

    # ------------------------------------------------------------ documentos
    def compra(self, tercero, numero, precio, centro):
        r = self.client.post(reverse('compras:nuevo'), {
            'tipo_comprobante': '01', 'serie': 'F900', 'numero': numero, 'tercero': tercero.pk,
            'fecha_emision': HOY.isoformat(), 'fecha_vencimiento': HOY.isoformat(), 'periodo': '',
            'clasificacion': 'SERVICIO', 'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1',
            'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
            'icbper': '0', 'glosa': 'Subcontrato de obra', 'centro_costo': centro.pk,
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk, 'items-0-descripcion': 'Servicio',
            'items-0-cantidad': '1', 'items-0-precio_unitario': precio})
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        return Compra.objects.get(serie='F900', numero=numero)

    def venta(self, tercero, precio):
        r = self.client.post(reverse('ventas:nuevo'), {
            'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': tercero.pk,
            'fecha_emision': HOY.isoformat(), 'fecha_vencimiento': '', 'forma_pago': 'CONTADO', 'moneda': 'PEN',
            'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0',
            'percepcion_pct': '0', 'icbper': '0', 'detraccion_codigo': '35', 'vendedor': '', 'glosa': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk, 'items-0-descripcion': 'Servicio',
            'items-0-cantidad': '1', 'items-0-precio_unitario': precio})
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        return Venta.objects.latest('id')

    def no_puede(self, nombre, *urls):
        self.como(nombre)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, f'{nombre}: {url}')

    # ------------------------------------------------------------ el ciclo
    def test_ciclo_completo_entre_modulos(self):
        # 1. Obras: el ingeniero presupuesta; aprobar es un permiso aparte (lo hace el gerente)
        obra = Obra.objects.create(nombre='Planta de tratamiento Ate', gastos_generales_pct=D('10'),
                                   utilidad_pct=D('8'))
        pres = Presupuesto.objects.create(obra=obra, nombre='Oferta')
        self.como('ingeniero')
        url = reverse('obras:presupuesto', args=[pres.pk])
        self.client.post(url, {'accion': 'agregar', 'item': '01', 'descripcion': 'OBRAS PROVISIONALES',
                               'es_titulo': 'on'})
        self.client.post(url, {'accion': 'agregar', 'item': '01.01', 'descripcion': 'CARTEL DE OBRA',
                               'unidad': 'und', 'metrado': '1', 'precio_fijo': 'on', 'precio': '3000'})
        pres.refresh_from_db()
        self.assertEqual((pres.costo_directo, pres.total), (D('3000.00'), D('4248.00')))  # (3000 + 10 % + 10 %) × 1.18
        self.client.post(url, {'accion': 'aprobar'})
        pres.refresh_from_db()
        self.assertEqual(pres.estado, 'BORRADOR')
        self.no_puede('ingeniero', reverse('compras:lista'), reverse('finanzas:cuentas'))
        self.como('gerente')
        self.client.post(url, {'accion': 'aprobar'})
        pres.refresh_from_db()
        self.assertEqual(pres.estado, 'APROBADO')

        # 2. Compras: dos subcontratos con detracción cargados a la obra
        self.como('comprador')
        c1 = self.compra(self.prov1, '1', '1000', obra.centro_costo)  # 1180, detracción 141.60 -> 142
        c2 = self.compra(self.prov2, '2', '2000', obra.centro_costo)  # 2360, detracción 283.20 -> 283
        self.assertEqual((c1.detraccion_monto, c2.detraccion_monto), (D('141.60'), D('283.20')))
        self.no_puede('comprador', reverse('finanzas:pago'), reverse('finanzas:detracciones_masivas'))

        # 3. Tesorería: aporte de capital, lote de detracciones con el archivo de SUNAT y pago múltiple del saldo
        self.como('tesorero')
        capital = CuentaContable.objects.filter(codigo__startswith='501', imputable=True).order_by('codigo').first()
        r = self.client.post(reverse('finanzas:movimiento_nuevo'), {
            'cuenta': self.banco.pk, 'fecha': HOY.isoformat(), 'tipo': 'INGRESO', 'concepto': 'OTRO',
            'medio_pago': 'TRANSFERENCIA', 'numero_operacion': 'APORTE-1', 'monto': '20000',
            'cuenta_contable': capital.pk, 'glosa': 'Aporte de capital', 'sustento': pdf('acta.pdf')})
        self.assertEqual(r.status_code, 302, r.context['form'].errors if r.status_code == 200 else '')
        url = reverse('finanzas:detracciones_masivas_nuevo') + f'?cuenta={self.banco.pk}&hasta={HOY.isoformat()}'
        self.client.post(url, {'cuenta': self.banco.pk, 'fecha': HOY.isoformat(), f'codigo_{c1.pk}': '020',
                               f'monto_{c1.pk}': '142', f'codigo_{c2.pk}': '020', f'monto_{c2.pk}': '283'})
        lote = DepositoDetraccion.objects.get()
        txt = self.client.get(reverse('finanzas:detraccion_masiva', args=[lote.pk]), {'formato': 'txt'})
        self.assertEqual([len(x) for x in txt.content.decode('ascii').split('\r\n')[:-1]], [68, 107, 107])
        l1, l2 = lote.lineas.order_by('id')
        self.client.post(reverse('finanzas:detraccion_masiva', args=[lote.pk]), {
            'accion': 'registrar', 'fecha': HOY.isoformat(), 'numero_operacion': 'NPS-9',
            f'constancia_{l1.pk}': '700000001', f'constancia_{l2.pk}': '700000002'})
        c1, c2 = Compra.objects.get(pk=c1.pk), Compra.objects.get(pk=c2.pk)
        self.assertEqual((c1.detraccion_estado[0], c2.detraccion_constancia), ('DEPOSITADA', '700000002'))
        self.assertEqual((c1.saldo, c2.saldo), (D('1038.00'), D('2077.00')))
        r = self.client.post(reverse('finanzas:pago_multiple') + '?q=F900', {
            'cuenta': self.banco.pk, 'cuenta_detraccion': self.banco.pk, 'fecha': HOY.isoformat(),
            'medio_pago': 'TRANSFERENCIA', 'numero_operacion': 'OP-901', f'monto_{c1.pk}': '1038',
            f'monto_{c2.pk}': '2077'})
        self.assertEqual(r.status_code, 302)
        salida = self.client.post(reverse('finanzas:transferencia'), {
            'origen': self.banco.pk, 'destino': self.caja.pk, 'fecha': HOY.isoformat(), 'monto': '500',
            'numero_operacion': 'TR-1', 'glosa': 'Fondo de la caja de obra'})
        self.assertEqual(salida.status_code, 302)

        # 4. Ventas: dos facturas de servicios con detracción (Ventas no cobra: eso es de Tesorería)
        self.como('vendedor')
        v1, v2 = self.venta(self.cli1, '1000'), self.venta(self.cli2, '2000')
        self.no_puede('vendedor', reverse('finanzas:cobranza_multiple'), reverse('finanzas:constancias_clientes'))

        # 5. Tesorería: cobranza múltiple del neto y constancias de los clientes desde el reporte de SUNAT
        self.como('tesorero')
        r = self.client.post(reverse('finanzas:cobranza_multiple'), {
            'cuenta': self.banco.pk, 'cuenta_detraccion': self.bn.pk, 'fecha': HOY.isoformat(),
            'medio_pago': 'TRANSFERENCIA', f'monto_{v1.pk}': '1038.40', f'monto_{v2.pk}': '2076.80'})
        self.assertEqual(r.status_code, 302)
        reporte = ('Número de Constancia|Fecha de Pago|Monto de Depósito|Tipo de Comprobante|Serie de Comprobante|'
                   'Número de Comprobante|Número de Documento del Adquiriente\n'
                   f'800000001|{HOY:%d/%m/%Y}|141.60|01|{v1.serie}|{int(v1.numero)}|{self.cli1.numero_doc}\n'
                   f'800000002|{HOY:%d/%m/%Y}|283.20|01|{v2.serie}|{int(v2.numero)}|{self.cli2.numero_doc}\n')
        self.client.post(reverse('finanzas:constancias_clientes'), {
            'accion': 'leer', 'archivo': SimpleUploadedFile('c.txt', reporte.encode('latin-1'))})
        self.client.post(reverse('finanzas:constancias_clientes'), {
            'accion': 'registrar', 'cuenta': self.bn.pk, 'constancia': ['800000001', '800000002']})
        for doc in (Compra.objects.get(pk=c1.pk), Compra.objects.get(pk=c2.pk),
                    Venta.objects.get(pk=v1.pk), Venta.objects.get(pk=v2.pk)):
            self.assertEqual((doc.saldo, doc.detraccion_estado[0]), (0, 'DEPOSITADA'), str(doc))
        self.assertEqual(self.bn.saldo, D('424.80'))  # lo que depositaron los clientes
        for nombre in ('finanzas:cuentas', 'finanzas:movimientos', 'finanzas:flujo', 'finanzas:conciliacion',
                       'finanzas:flujo_proyectado', 'finanzas:detracciones_masivas'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)
        self.no_puede('tesorero', reverse('contabilidad:asientos'), reverse('finanzas:letras'))

        # 6. Contabilidad: abrir el módulo centraliza; todo cuadra con los auxiliares y el EFE pasa sus validaciones
        self.como('contador')
        self.assertEqual(self.client.get(reverse('contabilidad:asientos')).status_code, 200)
        # la entrada de una transferencia se contabiliza con su salida (un solo asiento)
        movimientos = Movimiento.objects.filter(fecha=HOY, cuenta__in=[self.banco, self.bn, self.caja]).exclude(
            concepto='TRANSFERENCIA', tipo='INGRESO', transferencia_par__isnull=False)
        self.assertGreaterEqual(movimientos.count(), 8)
        self.assertEqual([(m.voucher, m.concepto, str(m.cuenta)) for m in movimientos if not m.asientos.exists()], [])
        self.assertEqual([str(a) for a in Asiento.objects.all() if not a.cuadrado], [])
        for c in Cuenta.objects.filter(moneda='PEN').select_related('cuenta_contable'):
            self.assertEqual(saldo_contable(c.cuenta_contable.codigo), c.saldo, c.nombre)  # tesorería = cuenta 10
        cxp = sum(c.saldo_pen for c in Compra.objects.con_saldos().filter(estado='REGISTRADO'))
        cxc = sum(v.saldo_pen for v in Venta.objects.con_saldos().filter(estado='REGISTRADO'))
        self.assertEqual(-sum(saldo_contable(x) for x in CuentaContable.objects.filter(
            codigo__startswith='42', imputable=True).values_list('codigo', flat=True)), cxp)
        self.assertEqual(sum(saldo_contable(x) for x in CuentaContable.objects.filter(
            codigo__startswith='121', imputable=True).values_list('codigo', flat=True)), cxc)
        # el costo de los subcontratos: 63 por naturaleza y su destino 9x, los dos con el centro de costo de la obra
        for es_destino in (False, True):
            lineas = AsientoLinea.objects.filter(centro_costo=obra.centro_costo, es_destino=es_destino)
            self.assertEqual(lineas.aggregate(s=Sum('debe'))['s'], D('3000.00'), es_destino)
            self.assertEqual({c[0] for c in lineas.values_list('cuenta__codigo', flat=True)}, {'9' if es_destino else '6'})
        e = flujo_efectivo.estado(PERIODO, PERIODO)
        validas = {v['codigo']: v for v in e['validaciones']}
        self.assertEqual([(c, v['detalle']) for c, v in validas.items() if not v['ok']], [])
        self.assertEqual(e['indirecto']['operacion'], e['total']['OPERACION'])
        rubros = {f['rubro'].codigo: f['importe'] for s in e['secciones'] for f in s['filas']}
        self.assertGreaterEqual(rubros.get('FI-E01', 0), D('20000'))  # el aporte de capital es financiamiento
        self.assertEqual(e['restringido'], self.bn.saldo)
        for nombre in ('balance', 'situacion', 'resultados', 'flujo_efectivo', 'diario', 'mayor', 'centros_reporte'):
            self.assertEqual(self.client.get(reverse(f'contabilidad:{nombre}')).status_code, 200, nombre)
        self.assertContains(self.client.get(reverse('contabilidad:centros_reporte')), obra.centro_costo.codigo)

        # 7. Registro de compras 8.1: la constancia y su fecha (campos 32 y 33)
        self.como('comprador')
        txt = self.client.get(reverse('compras:registro') + f'?formato=ple&periodo={PERIODO}').content.decode()
        campos = next(x for x in txt.splitlines() if '|F900|' in x and '|1|' in x).split('|')
        self.assertEqual((campos[31], campos[32]), (HOY.strftime('%d/%m/%Y'), '700000001'))

    def test_recorrido_de_todos_los_menus(self):
        """Cada opción del menú de cada módulo abre sin error (administrador y datos de demostración)."""
        from .modulos import MODULOS, menu_de
        self.como('gerente')
        errores, vistas = [], set()
        for m in MODULOS:
            for item in menu_de(m):
                for enlace in item.get('hijos') or [item]:
                    if enlace['url'] in vistas:
                        continue
                    vistas.add(enlace['url'])
                    r = self.client.get(enlace['url'])
                    if r.status_code not in (200, 302):
                        errores.append(f'{m["clave"]}: {enlace["etiqueta"]} {enlace["url"]} → {r.status_code}')
        self.assertGreater(len(vistas), 150)
        self.assertEqual(errores, [])
