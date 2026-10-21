"""v1.63, checklist v1.56 (portal y servicios): factura desde las recepciones con OC + recepción + guía por línea
(PORT-03), factura con varias órdenes de compra (PORT-05) y conformidad de servicio (HES) con adjuntos obligatorios,
liberación por niveles y vistas individuales; el proveedor solo factura HES liberadas (PORT-04)."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras import hes
from compras.models import ConformidadServicio, OrdenCompra, OrdenCompraItem
from contabilidad.models import CentroCosto
from inventario import servicios as inv
from inventario.models import Operacion, TipoOperacion
from proveedores import servicios
from proveedores.models import AccesoProveedor, FacturaProveedor

from .models import Almacen, PerfilUsuario, Producto, ReglaAprobacion, Tercero
from .modulos import GRUPOS

D = Decimal
HOY = timezone.localdate()
TC = (D('3.441'), D('3.450'))
PDF = b'%PDF-1.4 informe firmado'
PNG = b'\x89PNG\r\n\x1a\nfoto'


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=TC):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v163', 'a@a.pe', 'Clave-segura-9')
        cls.prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        cls.cc = CentroCosto.objects.first()
        cls.p1 = Producto.objects.get(codigo='P001')
        u = User.objects.create_user('prov_v163', 'p@p.pe', 'Clave-segura-9')
        AccesoProveedor.objects.create(usuario=u, tercero=cls.prov)
        cls.portal = u

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=TC)
        p.start()
        self.addCleanup(p.stop)

    def oc(self, producto=None, cantidad=10, precio='100', moneda='PEN', descripcion='Laptop'):
        oc = OrdenCompra.objects.create(numero=f'OC01-{OrdenCompra.objects.count() + 700:08d}', tercero=self.prov,
                                        centro_costo=self.cc, estado='APROBADO', moneda=moneda)
        OrdenCompraItem.objects.create(documento=oc, producto=producto, descripcion=descripcion,
                                       cantidad=D(cantidad), precio_unitario=D(precio))
        oc.calcular_totales()
        oc.save()
        return oc

    def recibir(self, oc, cantidad, guia):
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo='REC_COMPRA'), orden_compra=oc,
                                      almacen_destino=Almacen.principal(), referencia=guia)
        op.items.create(producto=self.p1, cantidad=D(cantidad), costo_unitario=D('100'))
        inv.confirmar(op)
        return op

    def cabecera(self, numero, total):
        return {'serie': 'F777', 'numero': numero, 'fecha_emision': HOY, 'total': D(total)}


class FacturaDesdeRecepcionesTest(Base):
    def test_lineas_con_recepcion_y_guia_varias_oc_y_tope_por_recepcion(self):
        oc1, oc2 = self.oc(self.p1), self.oc(self.p1, cantidad=5)
        r1, r2 = self.recibir(oc1, 4, '09 EG01-11'), self.recibir(oc1, 3, '09 EG01-12')
        r3 = self.recibir(oc2, 5, '09 EG01-20')
        fuentes = {(f['recepcion'].pk, f['oc'].pk): f for f in servicios.fuentes_proveedor(self.prov)}
        self.assertEqual(fuentes[(r1.pk, oc1.pk)]['esperado'], D('4'))
        self.assertEqual(fuentes[(r2.pk, oc1.pk)]['guia'], '09 EG01-12')
        clave = {k: f['clave'] for k, f in fuentes.items()}
        # no se factura más de lo recibido en esa recepción
        with self.assertRaisesMessage(servicios.ErrorPortal, 'recibido en la recepción'):
            servicios.registrar_factura_entregas(self.prov, self.portal, self.cabecera('501', '590'),
                                                 {clave[(r1.pk, oc1.pk)]: (D('5'), D('100'))})
        # una factura con la recepción 1 de la OC 1 y la de la OC 2 (misma moneda y tipo de operación)
        f = servicios.registrar_factura_entregas(
            self.prov, self.portal, self.cabecera('502', '1062'),
            {clave[(r1.pk, oc1.pk)]: (D('4'), D('100')), clave[(r3.pk, oc2.pk)]: (D('5'), D('100'))})
        lineas = {i.recepcion_id: i for i in f.items.all()}
        self.assertEqual((lineas[r1.pk].orden_compra, lineas[r1.pk].guia), (oc1, '09 EG01-11'))
        self.assertEqual(lineas[r3.pk].orden_compra, oc2)
        # lo facturado ya no está pendiente; la recepción 2 sigue por facturar
        pendientes = {f['recepcion'].pk for f in servicios.fuentes_proveedor(self.prov)}
        self.assertEqual(pendientes, {r2.pk})
        # el ERP la registra como una compra y deja atendidas las dos órdenes
        c = servicios.registrar_compra(f, self.admin)
        self.assertEqual(c.items.count(), 2)
        self.assertEqual(set(OrdenCompra.objects.filter(pk__in=[oc1.pk, oc2.pk]).values_list('estado', flat=True)),
                         {'ATENDIDO'})
        # monedas distintas no se juntan
        oc3 = self.oc(self.p1, cantidad=2, moneda='USD')
        r4 = self.recibir(oc3, 2, '09 EG01-30')
        claves = {f['recepcion'].pk: f['clave'] for f in servicios.fuentes_proveedor(self.prov)}
        with self.assertRaisesMessage(servicios.ErrorPortal, 'distinta moneda'):
            servicios.registrar_factura_entregas(self.prov, self.portal, self.cabecera('503', '590'),
                                                 {claves[r2.pk]: (D('3'), D('100')), claves[r4.pk]: (D('2'), D('100'))})
        # la pantalla del portal muestra las recepciones con su guía
        self.client.force_login(self.portal)
        r = self.client.get(reverse('portal:facturar'))
        self.assertContains(r, '09 EG01-12')
        self.assertContains(r, r4.numero)


class ConformidadServicioTest(Base):
    def test_hes_con_adjuntos_liberacion_por_niveles_y_factura(self):
        oc = self.oc(None, cantidad=1, precio='3000', descripcion='Mantenimiento de calderas')
        area = User.objects.create_user('area_v163', password='x')
        jefe = User.objects.create_user('jefe_v163', password='x')
        for u in (area, jefe):
            u.groups.add(Group.objects.get_or_create(name=GRUPOS['compras'])[0])
            PerfilUsuario.objects.create(usuario=u, acciones=['compras.hes'])
        ReglaAprobacion.objects.create(documento='HES', nivel=1, tipo='USUARIO', aprobador=jefe)
        # sin HES liberada el proveedor no factura el servicio
        self.assertFalse(servicios.fuentes_oc(oc))
        self.client.force_login(area)
        self.client.post(reverse('compras:conformidad', args=[oc.pk]), {
            'fecha': HOY.isoformat(), 'descripcion': 'Mantenimiento de octubre', 'porcentaje': '100', 'cantidad': '',
            'lugar': 'Planta Lurín', 'responsable_proveedor': 'Ing. Ruiz', 'observaciones': '', 'enviar': '1'})
        h = ConformidadServicio.objects.get(orden_compra=oc)
        self.assertEqual((h.estado, h.cantidad), ('BORRADOR', D('1')))  # sin adjuntos no se envía
        with self.assertRaisesMessage(hes.ErrorHES, 'Adjunte'):
            hes.enviar(h, area)
        with self.assertRaisesMessage(hes.ErrorHES, 'PDF'):
            hes.adjuntar(h, 'INFORME', SimpleUploadedFile('informe.png', PNG), area)
        self.client.post(reverse('compras:hes', args=[h.pk]), {'accion': 'adjuntar', 'tipo': 'INFORME',
                                                               'archivos': SimpleUploadedFile('informe.pdf', PDF)})
        self.client.post(reverse('compras:hes', args=[h.pk]), {'accion': 'adjuntar', 'tipo': 'TRABAJO',
                                                               'archivos': [SimpleUploadedFile('antes.png', PNG),
                                                                            SimpleUploadedFile('despues.png', PNG)]})
        self.assertEqual(h.adjuntos.count(), 3)
        self.client.post(reverse('compras:hes', args=[h.pk]), {'accion': 'enviar'})
        h.refresh_from_db()
        self.assertEqual((h.estado, h.niveles[0]['usuario']), ('POR_LIBERAR', jefe.pk))
        # el registrador no la libera; el jefe la ve en «Por liberar» y la libera
        self.client.post(reverse('compras:hes', args=[h.pk]), {'accion': 'liberar'})
        h.refresh_from_db()
        self.assertEqual(h.estado, 'POR_LIBERAR')
        self.client.force_login(jefe)
        self.assertContains(self.client.get(reverse('compras:hes_lista') + '?vista=liberar'), h.numero)
        self.client.post(reverse('compras:hes', args=[h.pk]), {'accion': 'liberar'})
        h.refresh_from_db()
        self.assertEqual(h.estado, 'LIBERADA')
        self.assertContains(self.client.get(reverse('compras:hes', args=[h.pk])), 'ACTA DE CONFORMIDAD')
        # el proveedor factura contra la HES liberada, sin superar lo conformado
        fuentes = servicios.fuentes_oc(oc)
        self.assertEqual([f['hes'] for f in fuentes], [h])
        with self.assertRaisesMessage(servicios.ErrorPortal, 'conformado en la HES'):
            servicios.registrar_factura_entregas(self.prov, self.portal, self.cabecera('601', '7080'),
                                                 {fuentes[0]['clave']: (D('2'), D('3000'))})
        f = servicios.registrar_factura_entregas(self.prov, self.portal, self.cabecera('602', '3540'),
                                                 {fuentes[0]['clave']: (D('1'), D('3000'))})
        self.assertEqual(f.items.get().conformidad, h)
        self.assertEqual(hes.facturado(h), D('1'))
        with self.assertRaisesMessage(hes.ErrorHES, 'ya se facturó'):
            hes.anular(h, self.admin, 'Error de registro')
        self.client.force_login(self.portal)
        self.assertContains(self.client.get(reverse('portal:oc_detalle', args=[oc.pk])), h.numero)
