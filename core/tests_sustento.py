"""Pruebas v1.10 "todo con sustento": sustentos adjuntos, anulación de movimientos, comprobantes sin eliminar,
ajuste rápido como operación y bitácora de auditoría."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra
from core.models import Bitacora, Producto, Sustento
from core.modulos import GRUPOS
from finanzas.models import Cuenta, Movimiento
from inventario.models import Operacion

D = Decimal
HOY = date.today()
TC = (D('3.441'), D('3.450'))


def pdf(nombre='sustento.pdf'):
    return SimpleUploadedFile(nombre, b'%PDF-1.4 sustento', content_type='application/pdf')


class SustentoTest(TestCase):
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
        self.bcp = Cuenta.objects.get(nombre='BCP Cta. Cte. Soles')

    def test_movimiento_manual_exige_sustento_y_se_anula_con_motivo(self):
        datos = {'cuenta': self.bcp.pk, 'fecha': HOY.isoformat(), 'tipo': 'EGRESO', 'concepto': 'SERVICIOS',
                 'medio_pago': 'TRANSFERENCIA', 'monto': '150', 'glosa': 'Recibo de luz'}
        r = self.client.post(reverse('finanzas:movimiento_nuevo'), datos)
        self.assertIn('sustento', r.context['form'].errors)
        saldo0 = self.bcp.saldo
        self.client.post(reverse('finanzas:movimiento_nuevo'), {**datos, 'sustento': pdf('recibo_luz.pdf')})
        mov = Movimiento.objects.get(glosa='Recibo de luz')
        self.assertEqual(Sustento.objects.get(object_id=mov.pk).archivo.nombre, 'recibo_luz.pdf')
        self.assertEqual(self.bcp.saldo, saldo0 - 150)
        # anular sin motivo suficiente no hace nada
        self.client.post(reverse('finanzas:movimiento_eliminar', args=[mov.pk]), {'motivo': 'error'})
        self.assertTrue(Movimiento.objects.filter(pk=mov.pk).exists())
        self.client.post(reverse('finanzas:movimiento_eliminar', args=[mov.pk]), {'motivo': 'Recibo duplicado, ya pagado'})
        self.assertFalse(Movimiento.objects.filter(pk=mov.pk).exists())  # fuera de los vigentes
        anulado = Movimiento.todos.get(pk=mov.pk)  # pero no se borró
        self.assertEqual((anulado.estado, anulado.motivo_anulacion, anulado.anulado_por),
                         ('ANULADO', 'Recibo duplicado, ya pagado', self.admin))
        self.assertEqual(self.bcp.saldo, saldo0)
        r = self.client.get(reverse('finanzas:movimientos') + '?estado=ANULADO')
        self.assertContains(r, anulado.voucher)
        self.assertContains(self.client.get(reverse('finanzas:voucher', args=[mov.pk])), 'ANULADO')
        self.assertTrue(Bitacora.objects.filter(modelo='finanzas.Movimiento', objeto_id=str(mov.pk), accion='ANULAR',
                                                motivo='Recibo duplicado, ya pagado').exists())

    def test_transferencia_se_anula_completa(self):
        caja = Cuenta.objects.get(tipo='CAJA')
        self.client.post(reverse('finanzas:transferencia'), {'origen': self.bcp.pk, 'destino': caja.pk,
                                                             'fecha': HOY.isoformat(), 'monto': '100',
                                                             'numero_operacion': 'OP-77', 'glosa': ''})
        salida = Movimiento.objects.get(numero_operacion='OP-77', tipo='EGRESO')
        self.client.post(reverse('finanzas:movimiento_eliminar', args=[salida.pk]),
                         {'motivo': 'Transferencia no realizada por el banco'})
        self.assertEqual(Movimiento.todos.filter(numero_operacion='OP-77', estado='ANULADO').count(), 2)

    def test_comprobantes_no_se_eliminan(self):
        c = Compra.objects.filter(estado='REGISTRADO').first()
        self.client.post(reverse('compras:eliminar', args=[c.pk]))
        self.assertTrue(Compra.objects.filter(pk=c.pk).exists())
        self.assertNotContains(self.client.get(reverse('compras:detalle', args=[c.pk])), 'Eliminar definitivamente')

    def test_ajuste_rapido_es_una_operacion_con_sustento(self):
        p = Producto.objects.get(codigo='P003')
        stock0 = p.stock
        datos = {'producto': p.pk, 'almacen': p.stocks.first().almacen_id, 'tipo': 'SALIDA', 'concepto': 'MERMA',
                 'cantidad': '2', 'fecha': HOY.isoformat(), 'motivo': 'Rotura en almacén'}
        r = self.client.post(reverse('inv_ajuste'), datos)
        self.assertIn('sustento', r.context['form'].errors)
        r = self.client.post(reverse('inv_ajuste'), {**datos, 'sustento': pdf('acta_merma.pdf')})
        op = Operacion.objects.get(referencia='Rotura en almacén')
        self.assertRedirects(r, reverse('inventario:detalle', args=[op.pk]))
        self.assertEqual((op.tipo.codigo, op.estado), ('AJ_SAL', 'CONFIRMADO'))
        self.assertEqual(Producto.objects.get(pk=p.pk).stock, stock0 - 2)
        self.assertContains(self.client.get(reverse('inventario:detalle', args=[op.pk])), 'acta_merma.pdf')

    def test_sustentos_respetan_el_modulo(self):
        mov = Movimiento.objects.first()
        url = reverse('sustento_subir', args=[ContentType.objects.get_for_model(Movimiento).pk, mov.pk])
        vendedor = User.objects.create_user('vendedor', 'v@v.com', 'x')
        vendedor.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0])
        self.client.force_login(vendedor)
        self.client.post(url, {'archivo': pdf(), 'next': '/'})
        self.assertFalse(Sustento.objects.filter(object_id=mov.pk).exists())
        self.client.force_login(self.admin)
        self.client.post(url, {'archivo': pdf('voucher.pdf'), 'descripcion': 'Voucher BCP', 'next': '/'})
        s = Sustento.objects.get(object_id=mov.pk)
        self.assertEqual(self.client.get(reverse('sustento_ver', args=[s.pk])).content, b'%PDF-1.4 sustento')
        self.client.force_login(vendedor)
        self.assertEqual(self.client.get(reverse('sustento_ver', args=[s.pk])).status_code, 404)

    def test_bitacora_registra_quien_y_que_cambio(self):
        datos = {'clase': 'MERCADERIA', 'codigo': '', 'nombre': 'Mouse óptico', 'unidad': 'NIU', 'marca': '',
                 'codigo_barras': '', 'descripcion': '', 'activo': 'on', 'puede_comprarse': 'on', 'precio_compra': '10',
                 'proveedor': '', 'unidad_compra': '', 'puede_venderse': 'on', 'precio_venta': '20',
                 'cuenta_existencias': '', 'cuenta_compra': '', 'cuenta_venta': '', 'cuenta_costo': '',
                 'stock_minimo': '0', 'punto_reorden': '0', 'stock_maximo': '0', 'lote_compra': '0',
                 'tiempo_entrega': '0', 'almacen_defecto': ''}
        self.client.post(reverse('producto_nuevo'), datos)
        p = Producto.objects.get(nombre='Mouse óptico')
        self.client.post(reverse('producto_editar', args=[p.pk]), {**datos, 'codigo': p.codigo, 'precio_venta': '25'})
        registros = Bitacora.objects.filter(modelo='core.Producto', objeto_id=str(p.pk)).order_by('fecha', 'id')
        self.assertEqual([b.accion for b in registros], ['CREAR', 'MODIFICAR'])
        self.assertEqual(registros[1].usuario, self.admin)
        cambio = registros[1].cambios['Precio venta (sin IGV)']
        self.assertEqual((D(cambio[0]), D(cambio[1])), (D('20'), D('25')))
        # el movimiento de stock (recalculo interno) no se registra como cambio del usuario
        p.mover_stock(1, 'prueba', costo=D('10'))
        self.assertEqual(Bitacora.objects.filter(modelo='core.Producto', objeto_id=str(p.pk)).count(), 2)
        r = self.client.get(reverse('auditoria') + '?modelo=core.Producto')
        self.assertContains(r, 'Mouse óptico')
        self.assertEqual(self.client.get(reverse('auditoria') + '?formato=excel').status_code, 200)
        # el inicio de sesión también queda registrado
        self.client.logout()
        User.objects.create_user('auditado', 'a@a.com', 'clave-segura-9')
        self.client.post(reverse('login'), {'username': 'auditado', 'password': 'clave-segura-9'})
        self.assertTrue(Bitacora.objects.filter(accion='ACCESO', objeto='auditado').exists())
