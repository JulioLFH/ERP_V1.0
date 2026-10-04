"""Pruebas de la Fase 2 (v1.7): OC con centro de costo, envío y aceptación por el proveedor, conformidad de
recepción, portal de proveedores (factura con tolerancias y validación SUNAT) y vencimiento desde el ingreso."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.forms import CompraForm, OrdenCompraForm
from compras.models import Compra, CompraItem, OrdenCompra, OrdenCompraItem
from contabilidad.models import CentroCosto
from core import sunat_consulta
from core.models import Almacen, CorreoConfig, Empresa, Producto, Tercero
from inventario import servicios as inv
from inventario.models import Operacion, TipoOperacion

from . import servicios
from .models import AccesoProveedor, FacturaProveedor

D = Decimal
HOY = date.today()
TC = (D('3.441'), D('3.450'))


def locmem(*args, **kwargs):
    return mail.get_connection('django.core.mail.backends.locmem.EmailBackend')


class Fase2Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=TC):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        cls.prov.email, cls.prov.dias_credito = 'ventas@proveedor.pe', 30
        cls.prov.save()
        cls.cc = CentroCosto.objects.first()
        cls.p1 = Producto.objects.get(codigo='P001')
        cfg = CorreoConfig.actual()
        cfg.servidor, cfg.usuario, cfg.remitente = 'smtp.ejemplo.pe', 'compras@miempresa.pe', 'compras@miempresa.pe'
        cfg.save()

    def setUp(self):
        self.client.force_login(self.admin)
        for p in (patch('core.tipo_cambio._consultar', return_value=TC), patch('core.correo.get_connection', locmem)):
            p.start()
            self.addCleanup(p.stop)

    def crear_oc(self, cantidad=10, precio='2000'):
        oc = OrdenCompra.objects.create(numero=f'OC01-{OrdenCompra.objects.count() + 100:08d}', tercero=self.prov,
                                        centro_costo=self.cc)
        OrdenCompraItem.objects.create(documento=oc, producto=self.p1, descripcion='Laptop', cantidad=cantidad,
                                       precio_unitario=D(precio))
        oc.calcular_totales()
        oc.save()
        return oc

    def recibir(self, oc, cantidad, fecha=HOY):
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo='REC_COMPRA'), fecha=fecha,
                                      orden_compra=oc, almacen_destino=Almacen.principal())
        op.items.create(producto=self.p1, cantidad=D(cantidad), costo_unitario=D('2000'))
        inv.confirmar(op)
        return op

    def acceso(self):
        u = User.objects.create_user('proveedor1', 'contacto@proveedor.pe', 'clave-segura-1')
        AccesoProveedor.objects.create(usuario=u, tercero=self.prov)
        return u

    # ---------------------------------------------------------------- 7. OC con centro de costo
    def test_orden_exige_centro_de_costo_y_hereda_dias_de_credito(self):
        datos = {'tercero': self.prov.pk, 'fecha': HOY, 'moneda': 'PEN', 'tipo_cambio': '1',
                 'tipo_operacion': 'GRAVADA', 'condicion_pago': '', 'glosa': ''}
        form = OrdenCompraForm(data=datos)
        self.assertFalse(form.is_valid())
        self.assertIn('centro_costo', form.errors)
        self.assertTrue(OrdenCompraForm(data={**datos, 'centro_costo': self.cc.pk}).is_valid())
        self.assertEqual(self.crear_oc().dias_credito, 30)

    def test_compra_de_mercaderia_exige_orden(self):
        datos = {'tipo_comprobante': '01', 'serie': 'F001', 'numero': '5', 'tercero': self.prov.pk,
                 'fecha_emision': HOY, 'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO', 'moneda': 'PEN',
                 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0',
                 'percepcion_pct': '0', 'icbper': '0'}
        form = CompraForm(data=datos)
        self.assertFalse(form.is_valid())
        self.assertIn('orden_compra', form.errors)
        oc = self.crear_oc()
        form = CompraForm(data={**datos, 'orden_compra': oc.pk})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['centro_costo'], self.cc)  # se hereda de la orden
        self.assertTrue(CompraForm(data={**datos, 'clasificacion': 'SERVICIO'}).is_valid())

    # ---------------------------------------------------------------- 8. envío y aceptación
    def test_envio_y_aceptacion_por_enlace(self):
        oc = self.crear_oc()
        r = self.client.post(reverse('compras:oc_enviar', args=[oc.pk]))
        self.assertRedirects(r, reverse('compras:oc_detalle', args=[oc.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ventas@proveedor.pe'])
        enlace = reverse('portal:oc_aceptacion', args=[oc.token_aceptacion()])
        self.assertIn(enlace, mail.outbox[0].alternatives[0][0])
        oc.refresh_from_db()
        self.assertEqual(oc.estado_proveedor, 'ENVIADA')
        self.client.logout()  # el proveedor abre el enlace sin usuario
        self.assertContains(self.client.get(enlace), 'Acepto la orden de compra')
        self.client.post(enlace, {'accion': 'aceptar', 'nombre': 'Ana Ruiz', 'comentario': 'Entrega el lunes'})
        oc.refresh_from_db()
        self.assertEqual((oc.estado_proveedor, oc.respondida_por), ('ACEPTADA', 'Ana Ruiz'))
        self.assertEqual(self.client.get(reverse('portal:oc_aceptacion', args=['falso'])).status_code, 404)

    # ---------------------------------------------------------------- 10. conformidad y vencimiento
    def test_conformidad_y_vencimiento_desde_el_ingreso(self):
        oc = self.crear_oc()
        compra = Compra.objects.create(tercero=self.prov, serie='F001', numero='88', fecha_emision=HOY - timedelta(5),
                                       orden_compra=oc, ingresar_almacen=False, forma_pago='CREDITO')
        CompraItem.objects.create(documento=compra, producto=self.p1, descripcion='Laptop', cantidad=10,
                                  precio_unitario=D('2000'))
        oc.actualizar_vencimientos()
        compra.refresh_from_db()
        self.assertEqual(compra.fecha_vencimiento, HOY - timedelta(5) + timedelta(30))  # aún sin ingreso
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo='REC_COMPRA'), fecha=HOY,
                                      orden_compra=oc, almacen_destino=Almacen.principal())
        op.items.create(producto=self.p1, cantidad=10, costo_unitario=D('2000'))
        r = self.client.post(reverse('inventario:confirmar', args=[op.pk]))
        self.assertRedirects(r, reverse('inventario:detalle', args=[op.pk]))
        compra.refresh_from_db()
        self.assertEqual((compra.fecha_ingreso, compra.fecha_vencimiento), (HOY, HOY + timedelta(30)))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Conformidad de recepción', mail.outbox[0].subject)
        op.refresh_from_db()
        self.assertIsNotNone(op.conformidad_enviada_en)

    # ---------------------------------------------------------------- 9. portal de proveedores
    def test_usuario_del_portal_no_entra_al_erp(self):
        u = self.acceso()
        self.client.force_login(u)
        self.assertRedirects(self.client.get(reverse('compras:lista')), reverse('portal:inicio'))
        self.assertRedirects(self.client.get(reverse('home')), reverse('portal:inicio'))
        self.assertEqual(self.client.get(reverse('portal:inicio')).status_code, 200)
        otro = Tercero.objects.filter(tipo='PROVEEDOR').exclude(pk=self.prov.pk).first()
        ajena = OrdenCompra.objects.create(numero='OC01-00000999', tercero=otro, centro_costo=self.cc,
                                           estado_proveedor='ENVIADA')
        self.assertEqual(self.client.get(reverse('portal:oc_detalle', args=[ajena.pk])).status_code, 404)

    def test_factura_del_portal_con_tolerancias(self):
        oc = self.crear_oc(cantidad=10)
        self.recibir(oc, 8)  # se recibieron 8 de 10
        self.client.force_login(self.acceso())
        lineas = servicios.lineas_por_facturar(oc)
        self.assertEqual(lineas[0]['esperado'], D('8'))
        item = oc.items.get()
        url = reverse('portal:factura_nueva', args=[oc.pk])
        base = {'serie': 'F001', 'numero': '1234', 'fecha_emision': HOY.isoformat(), 'observaciones': ''}
        r = self.client.post(url, {**base, f'cant_{item.pk}': '10', f'precio_{item.pk}': '2000'})
        self.assertContains(r, 'difiere de la recibida')  # 10 vs 8 recibidas, tolerancia ±1
        r = self.client.post(url, {**base, f'cant_{item.pk}': '8', f'precio_{item.pk}': '2001.50'})
        self.assertContains(r, 'difiere del de la orden')  # precio fuera de ±1
        r = self.client.post(url, {**base, f'cant_{item.pk}': '9', f'precio_{item.pk}': '2000.80'})
        f = FacturaProveedor.objects.get()
        self.assertRedirects(r, reverse('portal:factura', args=[f.pk]))
        self.assertEqual((f.estado, f.estado_sunat, f.total), ('ENVIADA', 'SIN_VALIDAR',
                                                                D('21248.50')))  # 9 x 2000.80 + IGV
        r = self.client.post(url, {**base, f'cant_{item.pk}': '1', f'precio_{item.pk}': '2000'})
        self.assertEqual(r.status_code, 200)  # ya no queda por facturar o es duplicada
        self.assertEqual(FacturaProveedor.objects.count(), 1)

    def test_validacion_sunat_al_registrar(self):
        oc = self.crear_oc(cantidad=2)
        Empresa.objects.update(sunat_client_id='id', sunat_client_secret='secreto')
        item = oc.items.get()
        cab = {'serie': 'F001', 'numero': '77', 'fecha_emision': HOY}
        no_existe = {'codigo': '0', 'estado': 'NO EXISTE', 'valido': False, 'detalle': 'Comprobante NO EXISTE'}
        with patch('core.sunat_consulta.validar', return_value=no_existe):
            with self.assertRaises(servicios.ErrorPortal):
                servicios.registrar_factura(oc, None, cab, {item.pk: (D('2'), D('2000'))})
        self.assertFalse(FacturaProveedor.objects.exists())
        ok = {'codigo': '1', 'estado': 'ACEPTADO', 'valido': True, 'detalle': 'Comprobante ACEPTADO · RUC ACTIVO'}
        with patch('core.sunat_consulta.validar', return_value=ok) as consulta:
            f = servicios.registrar_factura(oc, None, cab, {item.pk: (D('2'), D('2000'))})
        self.assertEqual(f.estado_sunat, 'VALIDO')
        self.assertEqual(consulta.call_args[0][:4], (self.prov.numero_doc, '01', 'F001', '77'))

    def test_consulta_sunat_formato_de_la_peticion(self):
        Empresa.objects.update(sunat_client_id='abc', sunat_client_secret='xyz')
        llamadas = []

        def falso(url, datos, cabeceras):
            llamadas.append((url, datos, cabeceras))
            if 'oauth2' in url:
                return {'access_token': 'TOKEN', 'expires_in': 3600}
            return {'success': True, 'data': {'estadoCp': '1', 'estadoRuc': '00', 'condDomiRuc': '00'}}
        with patch('core.sunat_consulta._post', side_effect=falso):
            r = sunat_consulta.validar('20555555556', '01', 'F001', '00000123', date(2026, 10, 1), D('118'))
        self.assertTrue(r['valido'])
        self.assertIn('clientesextranet/abc/oauth2/token', llamadas[0][0])
        self.assertIn(f'/contribuyentes/{Empresa.actual().ruc}/validarcomprobante', llamadas[1][0])
        self.assertIn(b'"numero": "123"', llamadas[1][1])
        self.assertIn(b'"fechaEmision": "01/10/2026"', llamadas[1][1])
        self.assertEqual(llamadas[1][2]['Authorization'], 'Bearer TOKEN')
        self.assertEqual(r['detalle'], 'Comprobante ACEPTADO · RUC ACTIVO · HABIDO')

    def test_aprobar_registra_la_compra_y_rechazar(self):
        oc = self.crear_oc(cantidad=5)
        self.recibir(oc, 5, fecha=HOY - timedelta(2))
        item = oc.items.get()
        f = servicios.registrar_factura(oc, None, {'serie': 'F002', 'numero': '10', 'fecha_emision': HOY},
                                        {item.pk: (D('5'), D('2000'))})
        stock = Producto.objects.get(pk=self.p1.pk).stock
        r = self.client.post(reverse('compras:portal_factura_aprobar', args=[f.pk]))
        c = Compra.objects.get(serie='F002', numero='10')
        self.assertRedirects(r, reverse('compras:detalle', args=[c.pk]))
        self.assertEqual((c.orden_compra, c.centro_costo, c.ingresar_almacen), (oc, self.cc, False))
        self.assertEqual(c.fecha_vencimiento, HOY - timedelta(2) + timedelta(30))
        self.assertEqual(Producto.objects.get(pk=self.p1.pk).stock, stock)  # ya ingresó con la recepción
        f.refresh_from_db()
        self.assertEqual((f.estado, f.compra), ('APROBADA', c))
        # otra factura, rechazada con motivo
        oc2 = self.crear_oc(cantidad=1)
        f2 = servicios.registrar_factura(oc2, None, {'serie': 'F002', 'numero': '11', 'fecha_emision': HOY},
                                         {oc2.items.get().pk: (D('1'), D('2000'))})
        self.client.post(reverse('compras:portal_factura_rechazar', args=[f2.pk]), {'motivo': 'Precio errado'})
        f2.refresh_from_db()
        self.assertEqual((f2.estado, f2.motivo_rechazo), ('RECHAZADA', 'Precio errado'))
        for nombre in ('compras:portal_facturas', 'compras:portal_accesos', 'compras:portal_acceso_nuevo', 'correo'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)
        self.assertEqual(self.client.get(reverse('compras:oc_detalle', args=[oc.pk])).status_code, 200)
