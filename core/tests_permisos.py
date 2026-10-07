"""Pruebas de permisos por acción, almacén, serie y límite de aprobación (v1.14)."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import OrdenCompra, OrdenCompraItem
from core.models import Almacen, PerfilUsuario, Serie, Tercero
from core.modulos import GRUPOS
from core.permisos import Permisos, puede
from ventas.models import Venta

D = Decimal


class PermisosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@e.pe', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def usuario(self, nombre, modulos, acciones=None, **perfil):
        u = User.objects.create_user(nombre, password='x')
        for m in modulos:
            u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
        if acciones is not None:
            p = PerfilUsuario.objects.create(usuario=u, acciones=acciones,
                                             limite_aprobacion=perfil.get('limite', D('0')))
            p.almacenes.set(perfil.get('almacenes', []))
            p.series.set(perfil.get('series', []))
        return u

    def test_sin_perfil_conserva_todo_en_sus_modulos(self):
        u = self.usuario('antiguo', ['ventas'])
        self.assertTrue(puede(u, 'ventas.anular'))
        self.assertFalse(puede(u, 'compras.anular'))  # no tiene el módulo

    def test_vendedor_sin_permiso_de_anular(self):
        u = self.usuario('vendedor', ['ventas'], ['ventas.emitir'])
        self.client.force_login(u)
        v = Venta.objects.filter(estado='REGISTRADO').first()
        r = self.client.post(reverse('ventas:anular', args=[v.pk]), {'motivo': 'Prueba de anulación'})
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, 'anular comprobantes', status_code=403)
        self.assertEqual(Venta.objects.get(pk=v.pk).estado, 'REGISTRADO')
        # desde un botón de la pantalla del comprobante: vuelve a ella con el aviso (no pierde la pantalla)
        detalle = reverse('ventas:detalle', args=[v.pk])
        r = self.client.post(reverse('ventas:anular', args=[v.pk]), {'motivo': 'Prueba de anulación'},
                             HTTP_REFERER=f'http://testserver{detalle}', follow=True)
        self.assertRedirects(r, detalle)
        self.assertContains(r, 'Acción no permitida')
        self.assertEqual(Venta.objects.get(pk=v.pk).estado, 'REGISTRADO')
        # tampoco ve el botón; sí puede emitir, no notas de crédito
        self.assertNotContains(self.client.get(reverse('ventas:detalle', args=[v.pk])), 'data-bs-target="#form-anular"')
        self.assertEqual(self.client.get(reverse('ventas:nuevo')).status_code, 200)
        self.assertEqual(self.client.get(reverse('ventas:nuevo') + f'?ref={v.pk}&tipo=07').status_code, 403)
        self.assertFalse(Permisos(u).ventas_anular)

    def test_almacen_no_permitido(self):
        principal = Almacen.principal()
        otro = Almacen.objects.exclude(pk=principal.pk).first()
        u = self.usuario('almacenero', ['inventario'], ['inventario.operar', 'inventario.ajustar'], almacenes=[otro])
        self.client.force_login(u)
        r = self.client.post(reverse('inventario:nueva') + '?tipo=AJ_ING',
                             {'fecha': '2026-01-01', 'almacen_destino': principal.pk})
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, principal.nombre, status_code=403)
        # el formulario solo ofrece sus almacenes
        import re
        r = self.client.get(reverse('inventario:nueva') + '?tipo=AJ_ING')
        select = re.search(r'<select name="almacen_destino".*?</select>', r.content.decode(), re.S).group(0)
        self.assertNotIn(f'value="{principal.pk}"', select)
        self.assertIn(f'value="{otro.pk}"', select)

    def test_serie_asignada(self):
        f002 = Serie.objects.create(tipo='01', serie='F002')
        u = self.usuario('caja2', ['ventas'], ['ventas.emitir'], series=[f002])
        from core import auditoria
        auditoria._local.usuario = u
        self.addCleanup(setattr, auditoria._local, 'usuario', None)
        from core.permisos import validar_serie
        datos = {'serie': ''}
        self.assertEqual(validar_serie(datos, '01', 'F001'), '')
        self.assertEqual(datos['serie'], 'F002')
        self.assertIn('F002', validar_serie({'serie': 'F001'}, '01', 'F001'))
        self.assertIn('no tiene series', validar_serie({'serie': ''}, '03', 'B001'))

    def test_limite_de_aprobacion_de_oc(self):
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        oc = OrdenCompra.objects.create(numero='OC01-00000777', tercero=prov)
        OrdenCompraItem.objects.create(documento=oc, descripcion='Equipo', cantidad=1, precio_unitario=D('5000'))
        oc.calcular_totales()
        oc.save()
        u = self.usuario('jefecompras', ['compras'], ['compras.oc', 'compras.aprobar_oc'], limite=D('1000'))
        self.client.force_login(u)
        self.client.post(reverse('compras:oc_estado', args=[oc.pk]), {'estado': 'APROBADO'})
        self.assertEqual(OrdenCompra.objects.get(pk=oc.pk).estado, 'PENDIENTE')
        PerfilUsuario.objects.filter(usuario=u).update(limite_aprobacion=D('10000'))
        self.client.post(reverse('compras:oc_estado', args=[oc.pk]), {'estado': 'APROBADO'})
        self.assertEqual(OrdenCompra.objects.get(pk=oc.pk).estado, 'APROBADO')
        sin = self.usuario('asistente', ['compras'], ['compras.oc'])
        self.client.force_login(sin)
        r = self.client.post(reverse('compras:oc_estado', args=[oc.pk]), {'estado': 'ANULADO'})
        self.assertEqual(r.status_code, 403)

    def test_formulario_de_usuario_guarda_permisos(self):
        self.client.force_login(self.admin)
        almacen = Almacen.principal()
        r = self.client.post(reverse('usuario_nuevo'), {
            'username': 'nuevo', 'first_name': '', 'last_name': '', 'email': '', 'is_active': 'on',
            'modulos': ['ventas'], 'acciones': ['ventas.emitir'], 'almacenes': [almacen.pk],
            'limite_aprobacion': '', 'clave1': 'Clave-segura-9', 'clave2': 'Clave-segura-9'})
        self.assertRedirects(r, reverse('usuarios'))
        p = PerfilUsuario.objects.get(usuario__username='nuevo')
        self.assertEqual((p.acciones, list(p.almacenes.all())), (['ventas.emitir'], [almacen]))
        r = self.client.get(reverse('usuario_nuevo'))
        self.assertContains(r, 'Anular comprobantes')
