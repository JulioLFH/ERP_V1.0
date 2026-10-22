"""v1.67, checklist v1.66.1 21:35: reversión de una entrada al costo con que entró y edición sin tocar el kardex
(INV-16), compra de servicios solo contra HES liberadas (PORT-09), casillas de operaciones del almacén (ALM-05) y
páginas de error propias sin rutas (SEG-04)."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra, ConformidadServicio, OrdenCompra, OrdenCompraItem

from .models import Almacen, Empresa, Kardex, Producto, Tercero

D = Decimal
HOY = timezone.localdate()
ITEMS = {'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000'}


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v167', 'a@a.pe', 'Clave-segura-9')
        cls.prov = Tercero.objects.filter(tipo='PROVEEDOR').first()

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)

    def compra(self, **extra):
        datos = {'tipo_comprobante': '01', 'serie': 'F900', 'numero': '77', 'tercero': self.prov.pk,
                 'fecha_emision': HOY.isoformat(), 'fecha_vencimiento': '', 'periodo': '',
                 'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1',
                 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
                 'icbper': '0', 'ingresar_almacen': 'on', 'glosa': '', 'items-TOTAL_FORMS': '1', **ITEMS}
        datos.update(extra)
        return datos


class ReversionAlCostoDeEntradaTest(Base):
    def test_editar_sin_cambiar_cantidades_no_toca_el_kardex_y_la_reversion_sale_al_costo_de_entrada(self):
        Empresa.objects.update(exigir_orden_compra=False)
        p = Producto.objects.get(codigo='P003')
        p.refresh_from_db()
        valor0, stock0 = p.stock * p.costo_promedio, p.stock
        datos = self.compra(**{'items-0-producto': p.pk, 'items-0-descripcion': 'x', 'items-0-cantidad': '10',
                               'items-0-precio_unitario': '999'})
        self.assertEqual(self.client.post(reverse('compras:nuevo'), datos).status_code, 302)
        compra = Compra.objects.get(serie='F900')
        movimientos = Kardex.objects.filter(producto=p).count()
        # solo cambia la serie (y la glosa): el kardex no se revierte, solo sigue al documento
        item = compra.items.get()
        edicion = dict(datos, **{'serie': 'F901', 'glosa': 'corregida', 'items-INITIAL_FORMS': '1',
                                 'items-0-id': item.pk, 'items-0-documento': compra.pk})
        self.assertEqual(self.client.post(reverse('compras:editar', args=[compra.pk]), edicion).status_code, 302)
        compra.refresh_from_db()
        self.assertEqual(Kardex.objects.filter(producto=p).count(), movimientos)
        self.assertTrue(Kardex.objects.filter(producto=p, referencia=str(compra)).exists())
        # cambia la cantidad: la entrada se revierte a 999 (no al promedio) y vuelve a entrar con 4
        edicion['items-0-cantidad'] = '4'
        self.client.post(reverse('compras:editar', args=[compra.pk]), edicion)
        reversion = Kardex.objects.get(producto=p, referencia=f'Reversión {compra}')
        self.assertEqual(reversion.costo_unitario, D('999.0000'))
        p.refresh_from_db()
        self.assertEqual(p.stock, stock0 + 4)
        self.assertAlmostEqual(float(p.stock * p.costo_promedio), float(valor0 + 4 * D('999')), delta=1.0)


class ServicioSoloConHESTest(Base):
    def test_compra_de_servicio_exige_hes_liberada_y_hasta_su_valor(self):
        from contabilidad.models import CentroCosto
        oc = OrdenCompra.objects.create(numero='OC01-00000967', tercero=self.prov, estado='APROBADO',
                                        centro_costo=CentroCosto.objects.filter(activo=True).first())
        linea = OrdenCompraItem.objects.create(documento=oc, producto=None, descripcion='Limpieza', cantidad=D('4'),
                                               precio_unitario=D('500'))
        oc.calcular_totales()
        oc.save()
        datos = self.compra(**{'orden_compra': oc.pk, 'clasificacion': 'SERVICIO', 'serie': 'F130', 'numero': '1',
                               'ingresar_almacen': '', 'items-0-producto': '', 'items-0-descripcion': 'Limpieza',
                               'items-0-cantidad': '4', 'items-0-precio_unitario': '500'})
        datos.pop('ingresar_almacen')
        r = self.client.post(reverse('compras:nuevo'), datos)
        self.assertContains(r, 'registre la conformidad del servicio (HES)')
        ConformidadServicio.objects.create(numero='HES-T1', orden_compra=oc, oc_item=linea, descripcion='Oct',
                                           cantidad=D('2'), porcentaje=D('50'), estado='LIBERADA',
                                           conforme_por=self.admin)
        r = self.client.post(reverse('compras:nuevo'), datos)
        self.assertContains(r, 'supera lo conformado')
        datos['items-0-cantidad'] = '2'
        self.assertEqual(self.client.post(reverse('compras:nuevo'), datos).status_code, 302)
        compra = Compra.objects.get(serie='F130')
        self.assertContains(self.client.get(reverse('compras:detalle', args=[compra.pk])), 'No aplica (servicio')


class PantallasTest(Base):
    def test_casillas_del_almacen_y_404_propio(self):
        r = self.client.get(reverse('almacen_editar', args=[Almacen.principal().pk]))
        contenido = r.content.decode()
        self.assertIn('class="form-check-input"', contenido.split('id="id_operaciones_0"')[0][-200:] +
                      contenido.split('id="id_operaciones_0"')[1][:200])
        self.assertLess(contenido.index('Operaciones permitidas'), contenido.index('id_operaciones_0'))
        with override_settings(DEBUG=True, DEBUG_TECNICO=False):
            r = self.client.get('/compras/solicitudes/no-existe/')
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, 'La página no existe', status_code=404)
        self.assertNotContains(r, 'URLconf', status_code=404)
