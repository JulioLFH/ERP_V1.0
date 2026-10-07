"""v1.23: segregación de funciones (permisos incompatibles y quien registra no aprueba)."""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from compras.models import OrdenCompra
from core.auditoria import registrar
from core.models import Empresa, PerfilUsuario, Tercero

from .segregacion import conflictos, error_aprobacion, reporte


class SegregacionTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin1', 'a@t.com', 'x')
        self.comprador = User.objects.create_user('comprador', password='x')
        self.comprador.groups.add(Group.objects.get_or_create(name='Compras')[0],
                                  Group.objects.get_or_create(name='Finanzas')[0])
        PerfilUsuario.objects.create(usuario=self.comprador,
                                     acciones=['compras.oc', 'compras.aprobar_oc', 'compras.registrar'])

    def test_detecta_permisos_incompatibles(self):
        motivos = {c['motivo'] for c in conflictos(self.comprador)}
        self.assertIn('Crea y aprueba sus propias órdenes de compra', motivos)
        self.assertNotIn('Registra facturas de proveedores y también las paga', motivos)  # no tiene finanzas.registrar
        filas, admins = reporte()
        self.assertEqual((filas[0]['u'], admins), (self.comprador, [self.admin]))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('segregacion')), 'Crea y aprueba')

    def test_quien_registra_no_aprueba_si_es_estricta(self):
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100000033', nombre='P')
        oc = OrdenCompra.objects.create(numero='OC01-9', tercero=prov)
        import core.auditoria as aud
        aud._local.usuario = self.admin
        try:
            registrar('CREAR', oc)
        finally:
            aud._local.usuario = None
        self.assertEqual(error_aprobacion(self.admin, oc), '')  # no estricta
        Empresa.objects.update(segregacion_estricta=True)
        self.assertIn('no puede aprobarlo', error_aprobacion(self.admin, oc))
        self.assertEqual(error_aprobacion(self.comprador, oc), '')
        self.client.force_login(self.admin)
        self.client.post(reverse('compras:oc_estado', args=[oc.pk]), {'estado': 'APROBADO'})
        oc.refresh_from_db()
        self.assertEqual(oc.estado, 'PENDIENTE')
