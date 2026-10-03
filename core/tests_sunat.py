"""Pruebas de la integración con Nubefact según su manual oficial (comprobantes y GRE)."""
import io
import json
import urllib.error
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import sunat
from core.models import FacturacionConfig, Producto, Tercero
from logistica.models import Conductor, GuiaRemision, Vehiculo
from ventas.models import Venta

ITEMS = {'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000'}


class RespuestaFalsa(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(codigo):
    cuerpo = json.dumps({'errors': 'error', 'codigo': codigo}).encode()
    return urllib.error.HTTPError('https://api.nubefact.com/x', 401, 'error', {}, io.BytesIO(cuerpo))


class NubefactTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cfg = FacturacionConfig.actual()
        cfg.proveedor, cfg.ruta, cfg.token = 'NUBEFACT', 'https://api.nubefact.com/api/v1/demo', 'abc123'
        cfg.save()

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def _venta(self, **extra):
        cli = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        p = Producto.objects.get(codigo='P002')
        data = {'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': cli.pk, 'fecha_emision': '2026-10-03',
                'fecha_vencimiento': '2026-11-02', 'forma_pago': 'CREDITO', 'moneda': 'PEN', 'tipo_cambio': '1',
                'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
                'icbper': '0', 'detraccion_codigo': '35', 'vendedor': '', 'descontar_stock': 'on', 'glosa': '',
                'items-TOTAL_FORMS': '1', 'items-0-producto': p.pk, 'items-0-descripcion': p.nombre,
                'items-0-cantidad': '2', 'items-0-precio_unitario': '500', **ITEMS}
        data.update(extra)
        return self.client.post(reverse('ventas:nuevo'), data)

    def test_factura_credito_con_detraccion(self):
        self._venta(detraccion_pct='12', detraccion_codigo='20')
        v = Venta.objects.latest('id')
        p = sunat.payload_comprobante(v)
        self.assertEqual(p['numero'], int(v.numero))  # sin ceros a la izquierda
        self.assertEqual(p['sunat_transaction'], 30)
        self.assertEqual(p['detraccion'], 'true')
        self.assertEqual(p['detraccion_tipo'], '20')  # 022 Otros servicios empresariales
        self.assertEqual(p['detraccion_total'], 141.6)
        self.assertEqual(p['total_gravada'], 1000.0)
        self.assertEqual(p['total_igv'], 180.0)
        self.assertEqual(p['medio_de_pago'], 'credito')
        self.assertEqual(p['venta_al_credito'][0]['importe'], 1038.4)  # total - detracción
        self.assertEqual(p['venta_al_credito'][0]['fecha_de_pago'], '02-11-2026')
        self.assertEqual(p['items'][0]['tipo_de_igv'], 1)
        self.assertEqual(p['items'][0]['precio_unitario'], 590.0)

    def test_exportacion_va_en_total_inafecta(self):
        self._venta(tipo_operacion='EXPORTACION', forma_pago='CONTADO')
        p = sunat.payload_comprobante(Venta.objects.latest('id'))
        self.assertEqual(p['sunat_transaction'], 2)
        self.assertEqual(p['total_inafecta'], 1000.0)
        self.assertEqual(p['items'][0]['tipo_de_igv'], 16)

    def test_nota_credito_de_boleta_usa_serie_B(self):
        cli = Tercero.objects.filter(tipo_doc='1').first()
        self._venta(tipo_comprobante='03', tercero=cli.pk, forma_pago='CONTADO')
        boleta = Venta.objects.latest('id')
        self.assertEqual(boleta.serie, 'B001')
        self._venta(tipo_comprobante='07', tercero=cli.pk, doc_referencia=boleta.pk, motivo_nota='06',
                    forma_pago='CONTADO')
        nc = Venta.objects.latest('id')
        self.assertEqual(nc.serie, 'BC01')
        p = sunat.payload_comprobante(nc)
        self.assertEqual((p['tipo_de_comprobante'], p['documento_que_se_modifica_tipo']), (3, 2))
        self.assertEqual(p['tipo_de_nota_de_credito'], 6)

    def test_rechaza_serie_incorrecta(self):
        r = self._venta(serie='B001')  # factura con serie de boleta
        self.assertIn('serie', r.context['form'].errors)

    def test_guia_transportista_cliente_es_remitente(self):
        cli = Tercero.objects.filter(tipo='CLIENTE')
        g = GuiaRemision.objects.create(
            tipo='31', serie='V001', numero='00000007', remitente=cli[0], destinatario=cli[1],
            vehiculo=Vehiculo.objects.first(), conductor=Conductor.objects.first(), partida_ubigeo='150101',
            partida_direccion='Lima', llegada_ubigeo='040101', llegada_direccion='Arequipa', peso_bruto=10)
        g.items.create(descripcion='Caja', cantidad=2, unidad='NIU')
        p = sunat.payload_guia(g)
        self.assertEqual(p['tipo_de_comprobante'], 8)
        self.assertEqual(p['numero'], '7')
        self.assertEqual(p['cliente_numero_de_documento'], cli[0].numero_doc)
        self.assertEqual(p['destinatario_documento_numero'], cli[1].numero_doc)
        for campo in ('motivo_de_traslado', 'tipo_de_transporte', 'numero_de_bultos'):
            self.assertNotIn(campo, p)  # solo existen en la guía remitente
        self.assertEqual(p['transportista_placa_numero'], 'ABC123')

    def test_guia_remitente_transporte_publico(self):
        venta = Venta.objects.filter(tipo_comprobante='01').first()
        transp = Tercero.objects.get(registro_mtc='1554321CNG')
        g = GuiaRemision.objects.create(
            tipo='09', serie='T001', numero='00000009', destinatario=venta.tercero, transportista=transp,
            modalidad='01', motivo_traslado='01', venta=venta, partida_ubigeo='150131',
            partida_direccion='Av. Javier Prado', llegada_ubigeo='060101', llegada_direccion='Cajamarca',
            peso_bruto=5)
        g.items.create(descripcion='Laptop', cantidad=1, unidad='NIU')
        p = sunat.payload_guia(g)
        self.assertEqual((p['tipo_de_comprobante'], p['tipo_de_transporte']), (7, '01'))
        self.assertEqual(p['transportista_documento_numero'], transp.numero_doc)
        self.assertIn('fecha_de_entrega_al_transportista', p)
        self.assertEqual(p['mtc'], '1554321CNG')
        self.assertNotIn('conductor_documento_numero', p)
        self.assertEqual(p['documento_relacionado'][0], {'tipo': '01', 'serie': venta.serie,
                                                          'numero': str(int(venta.numero))})
        self.assertNotIn('punto_de_partida_codigo_establecimiento_sunat', p)  # solo motivos 04 y 18

    def test_validaciones_previas_al_envio(self):
        g = GuiaRemision.objects.create(
            tipo='09', serie='G001', numero='1', destinatario=Tercero.objects.first(), modalidad='02',
            partida_ubigeo='15', partida_direccion='x', llegada_ubigeo='150101', llegada_direccion='y', peso_bruto=0)
        errores = ' '.join(sunat.validar_guia(g))
        for texto in ('empezar con "T"', 'peso bruto', 'ubigeo de partida', 'vehículo', 'conductor', 'no tiene bienes'):
            self.assertIn(texto, errores)

    def test_autorizacion_y_errores(self):
        ok = RespuestaFalsa(json.dumps({'aceptada_por_sunat': True}).encode())
        with patch('urllib.request.urlopen', side_effect=[http_error(10), ok]) as m:
            sunat._post({'operacion': 'x'})
        primera, segunda = (c.args[0].get_header('Authorization') for c in m.call_args_list)
        self.assertEqual(primera, 'abc123')  # formato del manual
        self.assertEqual(segunda, 'Token token="abc123"')  # formato alternativo
        with patch('urllib.request.urlopen', side_effect=http_error(11)):
            ok, msg = sunat.probar_conexion()
        self.assertFalse(ok)
        self.assertIn('RUTA', msg)
        with patch('urllib.request.urlopen', side_effect=http_error(24)):  # comprobante no existe: credenciales OK
            ok, msg = sunat.probar_conexion()
        self.assertTrue(ok)

    def test_guia_envio_en_dos_pasos(self):
        g = GuiaRemision.objects.filter(tipo='09').first()
        respuestas = [{'aceptada_por_sunat': False, 'sunat_responsecode': None, 'enlace': ''},
                      {'aceptada_por_sunat': True, 'enlace': 'https://www.nubefact.com/guia/abc',
                       'enlace_del_pdf': 'https://www.nubefact.com/guia/abc.pdf', 'cadena_para_codigo_qr': 'qr'}]
        with patch('core.sunat._post', side_effect=respuestas) as m:
            sunat.enviar_guia(g)
        operaciones = [c.args[0]['operacion'] for c in m.call_args_list]
        self.assertEqual(operaciones, ['generar_guia', 'consultar_guia'])
        g.refresh_from_db()
        self.assertEqual(g.estado_sunat, 'ACEPTADO')
        self.assertTrue(g.enlace_pdf.endswith('.pdf'))
