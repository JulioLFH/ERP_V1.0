"""v1.68, checklist v1.66.1 21:35 (importaciones): factura comercial de no domiciliado (IMP-01), DUA tipo 50 (IMP-02),
reparto por concepto con avisos (IMP-03), expediente desde la OC con gastos estimados y tránsito (IMP-04), FOB en la
cabecera (IMP-05), valor del ajuste en el kardex (IMP-06) y costo puesto en almacén (IMP-07)."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras import importaciones as imp_srv
from compras.forms import CompraForm
from compras.models import Compra, CompraItem, Importacion, OrdenCompra, OrdenCompraItem

from .models import Producto, Tercero

D = Decimal
HOY = timezone.localdate()


class ImportacionesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.436'), D('3.436'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v168', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.436'), D('3.436')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)
        self.exterior = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='0', numero_doc='EXT0000001',
                                               nombre='PROVEEDOR DEMO EXTERIOR LTD', pais='China', incoterm='FOB')
        self.agente = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100049008',
                                             nombre='AGENCIA DE ADUANAS SAC')
        self.martillo = Producto.objects.create(codigo='IMP-M', nombre='Martillo', clase='MERCADERIA', peso=D('1'))
        self.clavo = Producto.objects.create(codigo='IMP-C', nombre='Clavo', clase='MERCADERIA', unidad='KGM')

    def compra(self, tercero, tipo, numero, items, moneda='PEN', tc='1', ingresar=True, **extra):
        c = Compra.objects.create(tercero=tercero, tipo_comprobante=tipo, serie='E001', numero=numero,
                                  fecha_emision=HOY, tipo_operacion='INAFECTA', moneda=moneda, tipo_cambio=D(tc),
                                  ingresar_almacen=ingresar, clasificacion='MERCADERIA' if ingresar else 'SERVICIO',
                                  **extra)
        for producto, cant, precio in items:
            CompraItem.objects.create(documento=c, producto=producto, descripcion=str(producto or 'Gasto'),
                                      cantidad=D(cant), precio_unitario=D(precio))
        c.calcular_totales()
        c.save()
        if ingresar:
            c.aplicar_stock()
        return c

    def test_no_domiciliado_dua_y_avisos(self):
        # IMP-01: una factura 01 exige RUC; el proveedor del exterior usa 91 y aparece como factura comercial
        datos = {'tipo_comprobante': '01', 'serie': 'INV1', 'numero': '1001', 'tercero': self.exterior.pk,
                 'fecha_emision': HOY.isoformat(), 'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO',
                 'moneda': 'USD', 'tipo_cambio': '3.436', 'tipo_operacion': 'INAFECTA', 'detraccion_pct': '0',
                 'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0'}
        self.assertIn('tipo_comprobante', CompraForm(data=datos).errors)
        self.assertNotIn('tipo_comprobante', CompraForm(data={**datos, 'tipo_comprobante': '91'}).errors)
        factura = self.compra(self.exterior, '91', 'INV-1001', [(self.martillo, '100', '3'), (self.clavo, '200', '1')],
                              moneda='USD', tc='3.436')
        from core.opciones import _facturas_comerciales
        self.assertIn(factura.pk, [f[0] for f in _facturas_comerciales()])
        # IMP-02: DUA: ad valorem al costo; IGV + IPM sobre (CIF + ad valorem)
        sunat = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20131312955', nombre='SUNAT')
        dua = self.compra(sunat, '50', '000123', [(None, '1', '100')], ingresar=False, valor_aduana=D('1718'))
        self.assertEqual((dua.igv, dua.total), (D('327.24'), D('427.24')))
        from compras.views import ComprasViews
        self.assertIn('|1818.00|327.24|', ComprasViews().linea_ple('202610', 1, dua))
        imp = imp_srv.crear(Importacion(descripcion='Herramientas', compra=factura, metodo='VALOR'))
        self.assertEqual(imp.fob[:2], (D('500.00'), 'USD'))  # IMP-05: FOB en la moneda y en soles
        self.assertEqual(imp.fob[2], D('1718.00'))
        self.assertContains(self.client.get(reverse('compras:importacion', args=[imp.pk])), '1,718.00')
        g = imp_srv.agregar_gasto(imp, 'AD_VALOREM', None, compra=dua)
        self.assertEqual(g.monto, D('100.00'))  # el IGV de la DUA es crédito fiscal: no va al costo
        # IMP-03: flete por peso (el clavo no tiene peso: aviso) y por cantidad con unidades distintas (aviso)
        flete = self.compra(self.agente, '01', 'F-1201', [(None, '1', '340')], ingresar=False)
        imp_srv.agregar_gasto(imp, 'FLETE', None, compra=flete, metodo='PESO')
        avisos = [a[0] for a in imp_srv.avisos(imp)]
        self.assertIn('PESO', avisos)
        filas = {f['producto'].pk: f['gasto'] for f in imp_srv.prorrateo(imp)}
        self.assertEqual(filas[self.martillo.pk], D('340.00') + D('60.00'))  # todo el flete + 60 % del ad valorem
        imp.metodo = 'CANTIDAD'
        imp.save()
        self.assertIn('CANTIDAD', [a[0] for a in imp_srv.avisos(imp)])
        self.martillo.peso = D('0')
        self.martillo.save()
        with self.assertRaisesMessage(imp_srv.ErrorImportacion, 'Flete internacional'):
            imp_srv.prorrateo(imp)
        r = self.client.get(reverse('compras:importacion', args=[imp.pk]))
        self.assertContains(r, 'IMP-C')  # la tabla sigue mostrando los productos
        self.assertNotContains(r, 'Agregue gastos')

    def test_expediente_desde_la_oc_con_estimados_transito_y_costo_puesto(self):
        oc = OrdenCompra.objects.create(numero='OC01-00000968', tercero=self.exterior, estado='APROBADO', moneda='USD',
                                        tipo_cambio=D('3.436'), tipo_operacion='INAFECTA')
        OrdenCompraItem.objects.create(documento=oc, producto=self.martillo, descripcion='Martillo', cantidad=D('100'),
                                       precio_unitario=D('3'))
        oc.calcular_totales()
        oc.save()
        r = self.client.post(reverse('compras:importacion_nueva'), {'descripcion': 'Martillos', 'orden_compra': oc.pk,
                                                                    'metodo': 'VALOR'})
        imp = Importacion.objects.get(orden_compra=oc)
        self.assertRedirects(r, reverse('compras:importacion', args=[imp.pk]))
        self.client.post(reverse('compras:importacion', args=[imp.pk]), {'accion': 'estimado', 'concepto': 'FLETE',
                                                                         'monto': '200'})
        self.assertEqual(imp_srv.prorrateo(imp)[0]['estimado'], D('200'))  # con la OC, antes de la factura
        factura = self.compra(self.exterior, '91', 'INV-2001', [(self.martillo, '100', '3')], moneda='USD',
                              tc='3.436', ingresar=False, orden_compra=oc)
        self.client.post(reverse('compras:importacion', args=[imp.pk]), {'accion': 'factura', 'compra': factura.pk})
        imp.refresh_from_db()
        self.assertTrue(imp.en_transito)
        with self.assertRaisesMessage(imp_srv.ErrorImportacion, 'en tránsito'):
            imp_srv.liquidar(imp)
        factura.ingresar_almacen = True
        factura.save()
        factura.aplicar_stock()  # levante: ingresa al almacén
        imp_srv.liquidar(imp)  # el estimado valora el ingreso
        self.assertEqual(sum(a.diferencia for a in imp.ajustes.all()), D('200'))
        flete = self.compra(self.agente, '01', 'F-1301', [(None, '1', '260')], ingresar=False)
        imp_srv.agregar_gasto(imp, 'FLETE', None, compra=flete)
        imp_srv.liquidar(imp)  # llega el real: solo la diferencia
        self.assertEqual(sum(a.diferencia for a in imp.ajustes.all()), D('260'))
        # IMP-07: costo puesto en almacén = factura (3 × 3.436) + 2.60 de flete por unidad
        from core.views import _costo_puesto
        puesto = _costo_puesto(self.martillo)
        self.assertEqual((puesto['precio'], puesto['gasto'], puesto['costo']), (D('10.3100'), D('2.6000'), D('12.9100')))
        # IMP-06: el ajuste de valor muestra su importe en el kardex
        r = self.client.get(reverse('inv_kardex') + f'?producto={self.martillo.pk}&desde={HOY}&hasta={HOY}')
        ajustes = [f['valor'] for f in r.context['filas'] if not f['k'].cantidad]
        self.assertEqual(sum(ajustes), D('260.00'))
