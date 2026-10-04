"""Pruebas de las correcciones v1.8.1: bancos sin saldo negativo, facturación electrónica sin configurar, control
de crédito, Libro Mayor PLE 6.1, sugerencia de compra, saldos iniciales por cobrar/pagar, bloqueo por intentos
y respaldo."""
import gzip
import io
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from compras.models import OrdenCompra
from contabilidad.centralizar import centralizar_periodo
from core import sunat
from core.forms import digito_ruc
from core.inventario import sugerencias_compra
from core.models import Empresa, FacturacionConfig, IntentoAcceso, Producto, Tercero
from core.modulos import GRUPOS
from finanzas.forms import CuentaForm, MovimientoForm, TransferenciaForm
from finanzas.models import Cuenta, Movimiento
from ventas.models import Venta

D = Decimal
HOY = date.today()
TC = (D('3.441'), D('3.450'))


def excel(filas):
    wb = Workbook()
    for f in filas:
        wb.active.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile('carga.xlsx', buf.getvalue())


class CorreccionesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=TC):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=TC)
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)
        self.caja = Cuenta.objects.get(tipo='CAJA')

    def nueva_cuenta(self, saldo=D('0'), **extra):
        return Cuenta.objects.create(tipo='BANCO', nombre='Banco prueba', banco='BBVA', saldo_inicial=saldo, **extra)

    # ---------------------------------------------------------------- bancos sin negativo
    def test_egreso_con_fecha_anterior_al_ingreso_se_rechaza(self):
        c = self.nueva_cuenta()
        Movimiento.objects.create(cuenta=c, fecha=HOY, tipo='INGRESO', concepto='OTRO', monto=D('1000'))
        datos = {'cuenta': c.pk, 'fecha': HOY - timedelta(days=3), 'tipo': 'EGRESO', 'concepto': 'OTRO',
                 'medio_pago': 'TRANSFERENCIA', 'monto': '500', 'glosa': ''}
        form = MovimientoForm(data=datos)
        self.assertFalse(form.is_valid())  # hoy hay 1000, pero hace 3 días la cuenta estaba en cero
        self.assertIn('quedaría en S/ -500.00', form.errors['monto'][0])
        self.assertTrue(MovimientoForm(data={**datos, 'fecha': HOY}).is_valid())
        # transferencia con fecha anterior al ingreso
        t = TransferenciaForm(data={'origen': c.pk, 'destino': Cuenta.objects.get(nombre__startswith='BCP Cta. Cte. S').pk,
                                    'fecha': HOY - timedelta(days=1), 'monto': '100'})
        self.assertFalse(t.is_valid())

    def test_caja_nunca_queda_negativa_aunque_marque_sobregiro(self):
        self.caja.permite_sobregiro = True
        self.caja.save()
        form = MovimientoForm(data={'cuenta': self.caja.pk, 'fecha': HOY, 'tipo': 'EGRESO', 'concepto': 'OTRO',
                                    'medio_pago': 'EFECTIVO', 'monto': str(self.caja.saldo + 1), 'glosa': ''})
        self.assertFalse(form.is_valid())
        banco = self.nueva_cuenta(permite_sobregiro=True)
        form = MovimientoForm(data={'cuenta': banco.pk, 'fecha': HOY, 'tipo': 'EGRESO', 'concepto': 'OTRO',
                                    'medio_pago': 'TRANSFERENCIA', 'monto': '50', 'glosa': ''})
        self.assertTrue(form.is_valid())  # banco con sobregiro autorizado

    def test_no_se_elimina_un_ingreso_ya_gastado(self):
        c = self.nueva_cuenta()
        ingreso = Movimiento.objects.create(cuenta=c, fecha=HOY, tipo='INGRESO', concepto='OTRO', monto=D('300'))
        Movimiento.objects.create(cuenta=c, fecha=HOY, tipo='EGRESO', concepto='OTRO', monto=D('200'))
        self.client.post(reverse('finanzas:movimiento_eliminar', args=[ingreso.pk]))
        self.assertTrue(Movimiento.objects.filter(pk=ingreso.pk).exists())

    def test_saldo_inicial_e_importacion_sin_negativos(self):
        c = self.nueva_cuenta(saldo=D('100'))
        Movimiento.objects.create(cuenta=c, fecha=HOY, tipo='EGRESO', concepto='OTRO', monto=D('80'))
        form = CuentaForm(data={'tipo': 'BANCO', 'nombre': c.nombre, 'banco': 'BBVA', 'numero': '', 'cci': '',
                                'moneda': 'PEN', 'saldo_inicial': '50', 'activo': 'on'}, instance=c)
        self.assertFalse(form.is_valid())
        archivo = excel([['fecha', 'descripcion', 'monto', 'operacion'],
                         [HOY.strftime('%d/%m/%Y'), 'PAGO PROVEEDOR', -500, '1']])
        self.client.post(reverse('finanzas:importar'), {'cuenta': c.pk, 'archivo': archivo})
        self.assertEqual(c.movimientos.count(), 1)  # no se importó nada

    def test_diagnostico_de_saldos_negativos_anteriores(self):
        c = self.nueva_cuenta()
        Movimiento.objects.create(cuenta=c, fecha=HOY - timedelta(days=2), tipo='EGRESO', concepto='OTRO',
                                  monto=D('70'))  # dato antiguo, registrado sin la validación
        Movimiento.objects.create(cuenta=c, fecha=HOY, tipo='INGRESO', concepto='OTRO', monto=D('100'))
        r = self.client.get(reverse('finanzas:cuentas'))
        self.assertContains(r, 'Saldos negativos registrados antes de la validación')

    # ---------------------------------------------------------------- facturación electrónica
    def test_sin_configuracion_queda_no_enviado(self):
        FacturacionConfig.objects.update(proveedor='NINGUNO')
        v = Venta.objects.filter(tipo_comprobante='01').first()
        with self.assertRaises(sunat.ErrorFacturacion):
            sunat.enviar_comprobante(v)
        v.refresh_from_db()
        self.assertEqual(v.estado_sunat, 'NO_ENVIADO')

    # ---------------------------------------------------------------- control de crédito
    def _venta_credito(self, cliente, precio='100'):
        return self.client.post(reverse('ventas:nuevo'), {
            'tipo_comprobante': '01', 'serie': 'F001', 'numero': '', 'tercero': cliente.pk, 'fecha_emision': HOY,
            'fecha_vencimiento': HOY + timedelta(days=30), 'forma_pago': 'CREDITO', 'moneda': 'PEN',
            'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0',
            'percepcion_pct': '0', 'icbper': '0', 'descontar_stock': '', 'vendedor': '', 'glosa': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': '', 'items-0-descripcion': 'Servicio',
            'items-0-cantidad': '1', 'items-0-precio_unitario': precio})

    def test_bloqueo_por_deuda_vencida_y_limite(self):
        vencida = next(v for v in Venta.objects.filter(tipo_comprobante='01', forma_pago='CREDITO')
                       if v.saldo > 0 and v.fecha_vencimiento < HOY)
        r = self._venta_credito(vencida.tercero)
        self.assertContains(r, 'vencido(s) e impago(s)')
        ruc = f'2061111111{digito_ruc("2061111111")}'
        nuevo = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc=ruc, nombre='CLIENTE NUEVO SAC',
                                       direccion='Av. 1', limite_credito=D('500'))
        r = self._venta_credito(nuevo, precio='600')
        self.assertContains(r, 'límite de crédito')
        r = self._venta_credito(nuevo, precio='400')  # 472 con IGV < 500
        self.assertEqual(r.status_code, 302)

    # ---------------------------------------------------------------- Libro Mayor PLE 6.1
    def test_libro_mayor_ple_6_1(self):
        periodo = HOY.strftime('%Y%m')
        centralizar_periodo(periodo)
        r = self.client.get(reverse('contabilidad:mayor') + f'?hasta={HOY:%Y-%m}&formato=ple')
        self.assertIn(f'00060100001', r['Content-Disposition'])
        lineas = r.content.decode().strip().splitlines()
        self.assertTrue(lineas)
        cuentas = [l.split('|')[3] for l in lineas]
        self.assertEqual(cuentas, sorted(cuentas))  # agrupado por cuenta
        self.assertEqual(len(lineas[0].split('|')), 22)

    # ---------------------------------------------------------------- sugerencia de compra
    def test_sugerencia_de_compra_y_orden(self):
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        p = Producto.objects.create(nombre='Tóner', clase='SUMINISTRO', punto_reorden=D('10'), stock_maximo=D('25'),
                                    lote_compra=D('6'), precio_compra=D('80'), proveedor=prov)
        fila = next(f for f in sugerencias_compra() if f['p'] == p)
        self.assertEqual(fila['cantidad'], D('30'))  # hasta 25, redondeado a lotes de 6
        r = self.client.get(reverse('compras:oc_nuevo') + f'?reponer={prov.pk}')
        self.assertContains(r, 'Tóner')
        self.assertEqual(self.client.get(reverse('inv_reposicion')).status_code, 200)
        oc = OrdenCompra.objects.create(numero='OC01-00000777', tercero=prov, estado='APROBADO')
        oc.items.create(producto=p, descripcion='Tóner', cantidad=30, precio_unitario=D('80'))
        self.assertFalse(any(f['p'] == p for f in sugerencias_compra()))  # ya está en camino

    # ---------------------------------------------------------------- saldos iniciales por cobrar / pagar
    def test_saldos_iniciales_por_cobrar(self):
        cli = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        url = reverse('carga_masiva')
        emision = HOY - timedelta(days=60)
        self.client.post(url, {'tipo': 'saldos_cxc', 'accion': 'validar', 'archivo': excel([
            ['numero_doc', 'tipo_comprobante', 'serie', 'numero', 'fecha_emision', 'fecha_vencimiento', 'saldo'],
            [cli.numero_doc, 1, 'F010', 99, emision.strftime('%d/%m/%Y'), (emision + timedelta(30)).strftime('%d/%m/%Y'),
             1180]])})
        self.client.post(url, {'tipo': 'saldos_cxc', 'accion': 'confirmar'})
        v = Venta.objects.get(serie='F010', numero='99')
        self.assertTrue(v.es_saldo_inicial)
        self.assertEqual((v.total, v.saldo, v.stock_aplicado), (D('1180.00'), D('1180.00'), False))
        r = self.client.get(reverse('ventas:registro') + f'?periodo={emision:%Y%m}')
        self.assertNotContains(r, 'F010-99')  # no va al registro de ventas
        centralizar_periodo(emision.strftime('%Y%m'))
        a = v.asientos.get()
        self.assertEqual(a.lineas.get(cuenta__codigo='1212').debe, D('1180.00'))
        self.assertEqual(a.lineas.get(cuenta__codigo='5911').haber, D('1180.00'))
        with self.assertRaises(sunat.ErrorFacturacion):
            sunat.enviar_comprobante(v)

    # ---------------------------------------------------------------- seguridad
    def test_bloqueo_por_intentos_fallidos(self):
        User.objects.create_user('cajero', 'c@c.com', 'clave-correcta-1')
        self.client.logout()
        for _ in range(5):
            self.client.post(reverse('login'), {'username': 'cajero', 'password': 'mala'})
        r = self.client.post(reverse('login'), {'username': 'cajero', 'password': 'clave-correcta-1'})
        self.assertContains(r, 'bloqueado temporalmente')
        IntentoAcceso.objects.all().delete()
        r = self.client.post(reverse('login'), {'username': 'cajero', 'password': 'clave-correcta-1'})
        self.assertEqual(r.status_code, 302)

    def test_respaldo_solo_administrador(self):
        r = self.client.post(reverse('respaldo'))
        datos = json.loads(gzip.decompress(r.content))
        self.assertTrue(any(o['model'] == 'ventas.venta' for o in datos))
        usuario = User.objects.create_user('ventas1', 'v@v.com', 'x')
        usuario.groups.add(Group.objects.get_or_create(name=GRUPOS['ajustes'])[0])
        self.client.force_login(usuario)
        self.assertEqual(self.client.post(reverse('respaldo')).status_code, 403)
