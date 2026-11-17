"""v1.115.7: carga masiva de XML de compras — completa los productos de las compras ya registradas (p. ej. desde la
propuesta SIRE) sin cambiar sus totales, registra las que faltan y no toca las que ya tienen productos."""
import io
import zipfile
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, Producto, Tercero

from .models import Compra, CompraItem
from .tests_xml_sire import HOY, xml_factura


def zip_de(*archivos, nombre='xmls.zip'):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for a in archivos:
            z.writestr(a.name, a.read())
        z.writestr('R-20100000001-01-F001-1.xml', b'<constancia/>')  # constancia de SUNAT: se omite
    return SimpleUploadedFile(nombre, buf.getvalue(), content_type='application/zip')


class CargaMasivaXMLTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('xmlmas', 'x@x.com', 'Clave-segura-9')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.ruc = Empresa.actual().ruc
        self.harina = Producto.objects.create(codigo='HAR-50', nombre='Harina 50 kg', clase='MERCADERIA')
        self.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000777',
                                           nombre='DISTRIBUIDORA ANDINA SAC')

    def cargar(self, *archivos):
        return self.client.post(reverse('compras:importar_xml_varios'), {'archivos': list(archivos),
                                                                         'clasificacion': 'GASTO',
                                                                         'forma_pago': 'CONTADO'})

    def test_completa_registra_y_respeta(self):
        # 1) compra creada desde la propuesta SIRE: solo totales, sin productos
        sire = Compra.objects.create(tercero=self.prov, tipo_comprobante='01', serie='F001', numero='77',
                                     fecha_emision=HOY, base_imponible=D('300'), igv=D('54'), total=D('354'),
                                     glosa='Creado desde la propuesta SIRE 202610')
        # 2) compra que ya tiene productos
        con = Compra.objects.create(tercero=self.prov, tipo_comprobante='01', serie='F001', numero='78',
                                    fecha_emision=HOY, base_imponible=D('100'), igv=D('18'), total=D('118'))
        CompraItem.objects.bulk_create([CompraItem(documento=con, descripcion='Algo', cantidad=1,
                                                   precio_unitario=D('100'))])
        lineas = [('HARINA 50KG', 'HAR-50', '2', '100', '10'), ('Flete local', 'FLT', '1', '100', '10')]
        x77 = xml_factura('20600000777', self.ruc, lineas, numero='F001-00000077')
        x78 = xml_factura('20600000777', self.ruc, [('Algo', 'X', '1', '100', '10')], numero='F001-00000078')
        # 3) compra que no existe: se registra
        x90 = xml_factura('20600000888', self.ruc, [('Caja de cartón', 'CJ', '10', '5', '10')],
                          numero='F002-00000090', nombre='ENVASES SAC')
        r = self.cargar(zip_de(x77, x78, x90))
        res = r.context['r']['resumen']
        self.assertEqual((res['completada'], res['creada'], res['ya_tenia'], res['error'], res['total']),
                         (1, 1, 1, 0, 3))  # la constancia R-…xml se omitió
        sire.refresh_from_db()
        self.assertEqual((sire.total, sire.base_imponible), (D('354'), D('300')))  # los totales no cambian
        items = list(sire.items.order_by('pk'))
        self.assertEqual([(i.descripcion, i.producto) for i in items], [('HARINA 50KG', self.harina),
                                                                        ('Flete local', None)])
        self.assertEqual(con.items.count(), 1)  # no se tocó
        nueva = Compra.objects.get(serie='F002', numero='90')
        self.assertEqual((nueva.tercero.numero_doc, nueva.items.count(), nueva.total), ('20600000888', 1, D('59')))
        self.assertFalse(nueva.stock_aplicado)  # la carga masiva no mueve almacén
        self.assertEqual(res['sin_producto'], 2)  # «Flete local» y «Caja de cartón»
        self.assertContains(r, 'Productos completados')

    def test_aviso_si_no_cuadra_y_error_de_xml(self):
        Compra.objects.create(tercero=self.prov, tipo_comprobante='01', serie='F001', numero='77',
                              fecha_emision=HOY, base_imponible=D('300'), igv=D('54'), total=D('500'))
        x77 = xml_factura('20600000777', self.ruc, [('HARINA 50KG', 'HAR-50', '2', '100', '10')],
                          numero='F001-00000077')
        malo = SimpleUploadedFile('roto.xml', b'<no-es-factura/>', content_type='text/xml')
        r = self.cargar(x77, malo)
        filas = {f['archivo']: f for f in r.context['r']['filas']}
        self.assertIn('no cuadra', filas[x77.name]['aviso'])
        self.assertEqual(filas['roto.xml']['accion'], 'error')
