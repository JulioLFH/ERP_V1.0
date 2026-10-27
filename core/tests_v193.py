"""v1.93 (checklist del 27/10/2026 en v1.91): la declaración de la orden de arriba abajo con el historial a la derecha
(MAN-23), la fecha contable propuesta (MAN-24), el consumo estándar de lo declarado (MAN-25) y los botones «Nuevo»
solo para quien puede guardar (PLAT-17)."""
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import PerfilUsuario
from core.modulos import GRUPOS
from produccion.tests_reportes_produccion import Base as BaseProduccion

HOY = timezone.localdate()


def _usuario(nombre, modulos, acciones):
    u = User.objects.create_user(nombre, password='x')
    for m in modulos:
        u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
    PerfilUsuario.objects.create(usuario=u, acciones=acciones)
    return u


class DeclaracionOrdenTest(BaseProduccion):
    def test_flujo_cantidad_consumo_horas_e_historial_a_la_derecha(self):
        """MAN-23 a MAN-25: en una orden parcial se ve cantidad → consumo → horas; el consumo estándar es el de la
        receta para lo que se declara y lo que se descontará descuenta lo ya entregado."""
        from produccion import parciales
        o = self.orden()
        parciales.declarar(o, self.user, '40')  # se descuentan 20 kg de masa (0.5 × 40)
        r = self.client.get(reverse('manufactura:orden', args=[o.pk]))
        html = r.content.decode()
        self.assertLess(html.index('1. Cantidad producida'), html.index('2. Consumo de materiales'))
        self.assertLess(html.index('2. Consumo de materiales'), html.index('3. Horas y drivers'))
        # la fecha contable se propone con hoy (MAN-24)
        self.assertContains(r, f'name="fecha_contable" value="{HOY:%Y-%m-%d}"')
        # las declaraciones y la auditoría van en la columna derecha, después del costo estándar
        self.assertLess(html.index('col-lg-4'), html.index('Declaraciones de producción'))
        # «Agregar material» está junto al consumo y no se repite abajo
        self.assertEqual(html.count('Agregar material fuera de la receta'), 1)
        masa = next(m for m in r.context['consumo_std'] if m['consumo'].producto_id == self.masa.pk)
        self.assertEqual((masa['por_unidad'], masa['estandar'], masa['entregado'], masa['descuenta'], masa['valor']),
                         (D('0.5'), D('30'), D('20'), D('30'), D('180.00')))  # saldo 60 × 0.5 a S/ 6

    def test_orden_por_iniciar_terminar_en_un_paso_plegado(self):
        o = self.orden()
        r = self.client.get(reverse('manufactura:orden', args=[o.pk]))
        self.assertContains(r, 'id="terminar-paso"')
        self.assertContains(r, 'js-consumo-std')
        self.assertContains(r, 'data-sigue-fecha="fecha"', count=2)  # declarar y terminar

    def test_fecha_contable_vacia_toma_la_del_turno(self):
        from produccion import parciales
        o = self.orden()
        d = parciales.declarar(o, self.user, '10', fecha_contable=None)
        self.assertEqual(d.fecha, HOY)


class BotonesNuevoTest(TestCase):
    def test_nuevo_solo_para_quien_puede_guardar(self):
        """PLAT-17: el almacenero sin la configuración del inventario no ve «Nuevo almacén»; el jefe de planta sin
        «Órdenes de producción» no ve «Nueva orden»."""
        nuevo = reverse('almacen_nuevo')
        almacenero = _usuario('alm', ['inventario'], ['inventario.operaciones'])
        self.client.force_login(almacenero)
        r = self.client.get(reverse('almacenes'))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, f'href="{nuevo}"')
        admin = User.objects.create_superuser('admin', 'a@a.com', 'x')
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse('almacenes')), f'href="{nuevo}"')
        planta = _usuario('planta', ['manufactura'], ['manufactura.recetas'])
        self.client.force_login(planta)
        r = self.client.get(reverse('manufactura:ordenes'))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, reverse('manufactura:orden_nueva'))
        self.assertContains(self.client.get(reverse('manufactura:listas')), reverse('manufactura:lista_nueva'))
