"""v1.23: segregación de funciones (permisos incompatibles y quien registra no aprueba)."""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from compras.models import OrdenCompra
from core.auditoria import registrar
from core.models import Empresa, PerfilUsuario, Tercero

from .segregacion import conflictos, error_aprobacion, propio, reporte


class SegregacionTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin1', 'a@t.com', 'x')
        self.comprador = User.objects.create_user('comprador', password='x')
        self.comprador.groups.add(Group.objects.get_or_create(name='Compras')[0],
                                  Group.objects.get_or_create(name='Tesorería')[0])
        PerfilUsuario.objects.create(usuario=self.comprador,
                                     acciones=['compras.oc', 'compras.aprobar_oc', 'compras.registrar'])

    def test_detecta_permisos_incompatibles(self):
        motivos = {c['motivo'] for c in conflictos(self.comprador)}
        self.assertIn('Crea y aprueba sus propias órdenes de compra', motivos)
        self.assertNotIn('Registra facturas de proveedores y también las paga', motivos)  # no tiene tesoreria.registrar
        filas, admins = reporte()
        self.assertEqual((filas[0]['u'], admins), (self.comprador, [self.admin]))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('segregacion')), 'Crea y aprueba')

    def _oc_creada_por(self, usuario, numero):
        prov = Tercero.objects.get_or_create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100000033',
                                             defaults={'nombre': 'P'})[0]
        oc = OrdenCompra.objects.create(numero=numero, tercero=prov)
        import core.auditoria as aud
        aud._local.usuario = usuario
        try:
            registrar('CREAR', oc)
        finally:
            aud._local.usuario = None
        return oc

    def test_quien_registra_no_aprueba_si_es_estricta(self):
        oc = self._oc_creada_por(self.comprador, 'OC01-9')
        self.assertEqual(error_aprobacion(self.comprador, oc), '')  # no estricta
        self.assertIn('no puede aprobarlo', propio(self.comprador, oc))  # regla fija
        Empresa.objects.update(segregacion_estricta=True)
        self.assertIn('no puede aprobarlo', error_aprobacion(self.comprador, oc))
        self.assertEqual(error_aprobacion(self.admin, oc), '')
        self.client.force_login(self.comprador)
        self.client.post(reverse('compras:oc_estado', args=[oc.pk]), {'estado': 'APROBADO'})
        oc.refresh_from_db()
        self.assertEqual(oc.estado, 'PENDIENTE')

    def test_el_administrador_aprueba_lo_suyo(self):
        """v1.117.11: la segregación de funciones no aplica al administrador (aun con la estricta)."""
        from compras.aprobacion_oc import aprobar
        from core.models import Producto
        Empresa.objects.update(segregacion_estricta=True)
        oc = self._oc_creada_por(self.admin, 'OC01-10')
        self.assertEqual((propio(self.admin, oc), error_aprobacion(self.admin, oc)), ('', ''))
        prod = Producto.objects.create(nombre='Transporte', tipo='SERVICIO')
        oc.items.create(producto=prod, cantidad=1, precio_unitario=100)
        oc.calcular_totales()
        oc.save()
        aprobar(oc, self.admin)
        oc.refresh_from_db()
        self.assertEqual(oc.estado, 'APROBADO')
