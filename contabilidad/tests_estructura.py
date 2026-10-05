"""Estructura organizacional (v1.16): centros de beneficio, jerarquía y tipo de centros de costo, destino analítico
según el tipo, ingresos y costo de ventas por línea de negocio y estado de resultados por línea."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra, CompraItem
from core.models import Producto, Tercero
from ventas.models import Venta, VentaItem

from .centralizar import centralizar_periodo
from .forms import CentroCostoForm
from .models import AsientoLinea, CentroBeneficio, CentroCosto

D = Decimal
HOY = date.today()
PERIODO = HOY.strftime('%Y%m')


class EstructuraTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@e.pe', 'x')

    def setUp(self):
        self.client.force_login(self.admin)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.galletas = CentroBeneficio.objects.create(codigo='GAL', nombre='Galletas')
        self.planta = CentroCosto.objects.create(codigo='PL', nombre='Planta', tipo='PRODUCCION',
                                                 centro_beneficio=self.galletas)
        self.horno = CentroCosto.objects.create(codigo='PL-HOR', nombre='Hornos', tipo='PRODUCCION',
                                                padre=self.planta)

    def test_jerarquia_hereda_linea_y_evita_ciclos(self):
        self.assertEqual(self.horno.beneficio, self.galletas)
        self.assertEqual(self.horno.nivel, 1)
        self.assertEqual(self.planta.descendientes_ids(), {self.planta.pk, self.horno.pk})
        form = CentroCostoForm(instance=self.planta)
        self.assertNotIn(self.horno, form.fields['padre'].queryset)  # no puede depender de su dependiente

    def test_gasto_de_planta_va_al_costo_de_produccion(self):
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        c = Compra.objects.create(tercero=prov, serie='F001', numero='777', fecha_emision=HOY, clasificacion='SERVICIO',
                                  centro_costo=self.horno, ingresar_almacen=False)
        CompraItem.objects.create(documento=c, descripcion='Energía hornos', cantidad=1, precio_unitario=D('480'))
        c.calcular_totales()
        c.save()
        centralizar_periodo(PERIODO)
        destino = AsientoLinea.objects.get(asiento__compra=c, es_destino=True, debe__gt=0)
        self.assertEqual((destino.cuenta.codigo, destino.debe), ('901', D('480')))  # antes iba a la 94
        gasto = AsientoLinea.objects.get(asiento__compra=c, es_destino=False, cuenta__codigo__startswith='6')
        self.assertEqual(gasto.centro_beneficio, self.galletas)

    def test_venta_y_costo_por_linea_de_negocio(self):
        prod = Producto.objects.get(codigo='P003')
        prod.centro_beneficio = self.galletas
        prod.save()
        cliente = Tercero.objects.filter(tipo_doc='6', tipo__in=['CLIENTE', 'AMBOS']).first()
        v = Venta.objects.create(tercero=cliente, serie='F001', numero='77777')
        VentaItem.objects.create(documento=v, producto=prod, descripcion=prod.nombre, cantidad=1,
                                 precio_unitario=D('100'))
        VentaItem.objects.create(documento=v, descripcion='Servicio sin línea', cantidad=1, precio_unitario=D('50'))
        v.calcular_totales()
        v.save()
        v.aplicar_stock()
        centralizar_periodo(PERIODO)
        ingresos = {l.centro_beneficio_id: l.haber for l in AsientoLinea.objects.filter(
            asiento__venta=v, cuenta__codigo__startswith='70')}
        self.assertEqual(ingresos[self.galletas.pk], D('100'))
        costo = AsientoLinea.objects.filter(asiento__origen='INVENTARIO', asiento__periodo=PERIODO,
                                            cuenta__codigo__startswith='69', centro_beneficio=self.galletas)
        self.assertTrue(costo.exists())
        r = self.client.get(reverse('contabilidad:resultados_linea'))
        self.assertContains(r, 'Galletas')
        self.assertContains(r, 'Margen bruto')
        fila = next(t for t in r.context['tabla'] if t['rubro'] == 'Ventas netas')
        self.assertGreaterEqual(fila['valores'][0], D('100'))

    def test_pantallas(self):
        for url in [reverse('contabilidad:beneficios'), reverse('contabilidad:cb_nuevo'),
                    reverse('contabilidad:centros'), reverse('contabilidad:cc_editar', args=[self.horno.pk]),
                    reverse('contabilidad:centros_reporte'), reverse('contabilidad:resultados_linea') + '?formato=excel']:
            self.assertEqual(self.client.get(url).status_code, 200, url)
