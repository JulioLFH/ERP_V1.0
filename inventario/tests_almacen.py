"""v1.23: picking por olas e inventario cíclico ABC."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Almacen, Producto, StockAlmacen, Tercero, Ubicacion
from core.sustentos import tiene
from ventas.models import Cotizacion

from . import almacen as alm
from .models import ConteoCiclico, OlaPicking

D = Decimal
HOY = date.today()


class AlmacenAvanzadoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('alm', 'a@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.almacen = Almacen.principal()
        self.cliente = Tercero.objects.filter(tipo__in=['CLIENTE', 'AMBOS']).first()
        self.p1 = Producto.objects.get(codigo='P003')
        self.p2 = Producto.objects.filter(tipo='BIEN').exclude(pk=self.p1.pk).first()

    def _pedido(self, numero, lineas):
        ped = Cotizacion.objects.create(tipo='PED', numero=numero, tercero=self.cliente)
        for prod, cant in lineas:
            ped.items.create(producto=prod, descripcion=prod.nombre, cantidad=D(cant), precio_unitario=D('1'))
        return ped

    def test_ola_junta_pedidos_por_ubicacion(self):
        u = Ubicacion.objects.create(almacen=self.almacen, codigo='B-02-01')
        StockAlmacen.objects.filter(almacen=self.almacen, producto=self.p2).update(ubicacion=u)
        a = self._pedido('PED-1', [(self.p1, '2'), (self.p2, '1')])
        b = self._pedido('PED-2', [(self.p1, '3')])
        r = self.client.post(reverse('inventario:ola_nueva'), {'almacen': self.almacen.pk, 'pedido': [a.pk, b.pk]})
        ola = OlaPicking.objects.get()
        self.assertRedirects(r, reverse('inventario:ola', args=[ola.pk]))
        linea = ola.lineas.get(producto=self.p1)
        self.assertEqual((linea.cantidad, linea.detalle), (D('5'), {'PED-1': '2', 'PED-2': '3'}))
        filas = alm.lineas_con_stock(ola)
        self.assertEqual(filas[0]['l'].producto, self.p2)  # lo ubicado primero en el recorrido
        self.assertEqual(alm.pedidos_pendientes().filter(pk=a.pk).count(), 0)  # ya está en una ola
        with self.assertRaises(alm.ErrorAlmacen):
            alm.crear_ola(self.almacen, [a], self.user)
        self.client.post(reverse('inventario:ola', args=[ola.pk]), {'accion': 'preparar', f'prep_{linea.pk}': '4'})
        ola.refresh_from_db()
        linea.refresh_from_db()
        self.assertEqual((ola.estado, linea.preparada), ('PREPARADA', D('4')))
        self.assertEqual(self.client.get(reverse('inventario:ola', args=[ola.pk]) + '?formato=excel').status_code, 200)

    def test_conteo_ciclico_ajusta_con_acta(self):
        self.p1.mover_stock(-1, 'venta de prueba', fecha=HOY, almacen=self.almacen, origen='AJUSTE', concepto='MERMA')
        clases, valor = alm.clasificar_abc(self.almacen)
        mayor = max(valor, key=valor.get)
        self.assertEqual(clases[mayor], 'A')  # el de mayor valor que sale siempre es A
        r = self.client.post(reverse('inventario:ciclico'), {'almacen': self.almacen.pk, 'clase': [clases[self.p1.pk]],
                                                            'maximo': '500'})
        conteo = ConteoCiclico.objects.get()
        self.assertRedirects(r, reverse('inventario:conteo', args=[conteo.pk]))
        item = conteo.items.get(producto=self.p1)
        antes = self.p1.stock_en(self.almacen)
        self.client.post(reverse('inventario:conteo', args=[conteo.pk]), {
            'accion': 'cerrar', f'contado_{item.pk}': str(antes - 2)})
        conteo.refresh_from_db()
        self.assertEqual(conteo.estado, 'CERRADO')
        self.assertEqual(self.p1.stock_en(self.almacen), antes - 2)
        self.assertTrue(tiene(conteo.ajuste_salida))
        # recién contado: ya no le toca
        fila = next(f for f in alm.programa(self.almacen) if f['p'] == self.p1)
        self.assertEqual((fila['ultimo'], fila['vencido']), (HOY, False))
        self.assertEqual(alm.exactitud(self.almacen, HOY)['pct'], D('0.0'))

    def test_pantallas(self):
        for nombre in ('olas', 'ola_nueva', 'ciclico'):
            self.assertEqual(self.client.get(reverse(f'inventario:{nombre}')).status_code, 200, nombre)
