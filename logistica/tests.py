"""Pruebas de v1.1: guías de remisión, almacenes, tipo de cambio, notas y facturación electrónica."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import sunat
from core.models import Almacen, Empresa, FacturacionConfig, Producto, StockAlmacen, Tercero, TipoCambio
from finanzas.models import Cuenta
from ventas.models import Venta

from .models import Conductor, GuiaRemision, Vehiculo

ITEMS_MGMT = {'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000'}


def stock_en(producto, almacen):
    return StockAlmacen.objects.filter(producto=producto, almacen=almacen).values_list('cantidad', flat=True).first() or 0


class NuevasFuncionesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450')))
        self.mock_tc = p.start()
        self.addCleanup(p.stop)
        self.principal = Almacen.principal()
        self.callao = Almacen.objects.get(codigo='ALM02')

    def _guia_remitente(self, **extra):
        p = Producto.objects.get(codigo='P003')
        data = {
            'serie': '', 'motivo_traslado': '04', 'descripcion_motivo': '', 'modalidad': '02',
            'venta': '', 'compra': '', 'transportista': '', 'efecto_stock': 'TRASLADO',
            'almacen_origen': self.principal.pk, 'almacen_destino': self.callao.pk,
            'fecha_emision': '2026-10-01', 'fecha_traslado': '2026-10-01',
            'destinatario': '',  # motivo 04: el destinatario es la propia empresa
            'partida_ubigeo': '150131', 'partida_direccion': 'Av. Javier Prado 123',
            'llegada_ubigeo': '070101', 'llegada_direccion': 'Av. Argentina 2450',
            'vehiculo': Vehiculo.objects.first().pk, 'conductor': Conductor.objects.first().pk,
            'peso_bruto': '10', 'unidad_peso': 'KGM', 'numero_bultos': '2', 'doc_relacionado': '',
            'observaciones': '', 'items-TOTAL_FORMS': '1', 'items-0-producto': p.pk,
            'items-0-descripcion': p.nombre, 'items-0-unidad': 'NIU', 'items-0-cantidad': '5', **ITEMS_MGMT,
        }
        data.update(extra)
        return p, self.client.post(reverse('logistica:nueva') + '?tipo=09', data)

    # ---------------------------------------------------------------- guías
    def test_guia_remitente_traslado_entre_almacenes(self):
        p = Producto.objects.get(codigo='P003')
        total0, origen0, destino0 = p.stock, stock_en(p, self.principal), stock_en(p, self.callao)
        p, r = self._guia_remitente()
        self.assertEqual(r.status_code, 302)
        guia = GuiaRemision.objects.latest('id')
        self.assertEqual((guia.tipo, guia.serie), ('09', 'T001'))
        self.assertEqual(guia.numero, '00000002')  # la demo ya emitió la 1
        self.assertEqual(guia.destinatario.numero_doc, Empresa.actual().ruc)  # BUG-03
        p.refresh_from_db()
        self.assertEqual(p.stock, total0)  # el traslado no cambia el total
        self.assertEqual(stock_en(p, self.principal), origen0 - 5)
        self.assertEqual(stock_en(p, self.callao), destino0 + 5)
        self.client.post(reverse('logistica:anular', args=[guia.pk]), {'motivo': 'Traslado no realizado'})
        self.assertEqual(stock_en(p, self.principal), origen0)
        self.assertEqual(stock_en(p, self.callao), destino0)

    def test_guia_validaciones(self):
        _, r = self._guia_remitente(vehiculo='', conductor='', partida_ubigeo='15')
        self.assertEqual(r.status_code, 200)
        errores = r.context['form'].errors
        self.assertIn('vehiculo', errores)
        self.assertIn('conductor', errores)
        self.assertIn('partida_ubigeo', errores)
        _, r = self._guia_remitente(almacen_destino=self.principal.pk)
        self.assertIn('almacen_destino', r.context['form'].errors)

    def test_guia_transportista(self):
        cli = Tercero.objects.filter(tipo='CLIENTE')
        p = Producto.objects.get(codigo='P001')
        data = {
            'serie': '', 'remitente': cli[0].pk, 'destinatario': cli[1].pk, 'fecha_emision': '2026-10-01',
            'fecha_traslado': '2026-10-02', 'partida_ubigeo': '150101', 'partida_direccion': 'Lima',
            'llegada_ubigeo': '040101', 'llegada_direccion': 'Arequipa', 'vehiculo': Vehiculo.objects.first().pk,
            'conductor': Conductor.objects.first().pk, 'peso_bruto': '100', 'unidad_peso': 'KGM',
            'numero_bultos': '3', 'doc_relacionado': '09 T001-00000015', 'observaciones': '',
            'items-TOTAL_FORMS': '1', 'items-0-producto': p.pk, 'items-0-descripcion': p.nombre,
            'items-0-unidad': 'NIU', 'items-0-cantidad': '3', **ITEMS_MGMT,
        }
        stock0 = p.stock
        r = self.client.post(reverse('logistica:nueva') + '?tipo=31', data)
        self.assertEqual(r.status_code, 302)
        guia = GuiaRemision.objects.latest('id')
        self.assertEqual((guia.tipo, guia.serie, guia.numero), ('31', 'V001', '00000001'))
        p.refresh_from_db()
        self.assertEqual(p.stock, stock0)  # la guía transportista no mueve almacén
        payload = sunat.payload_guia(guia)
        self.assertEqual(payload['tipo_de_comprobante'], 8)
        self.assertEqual(payload['documento_relacionado'][0]['serie'], 'T001')

    def test_guia_desde_venta_no_duplica_stock(self):
        venta = Venta.objects.filter(tipo_comprobante='01').last()
        r = self.client.get(reverse('logistica:nueva') + f'?tipo=09&venta={venta.pk}')
        self.assertEqual(r.context['form'].initial['efecto_stock'], 'NINGUNO')
        self.assertEqual(r.context['form'].initial['destinatario'], venta.tercero_id)

    # ---------------------------------------------------------------- inventario
    def test_ajuste_y_valorizacion(self):
        p = Producto.objects.get(codigo='P004')
        stock0 = p.stock
        r = self.client.post(reverse('inv_ajuste'), {'producto': p.pk, 'almacen': self.callao.pk, 'tipo': 'ENTRADA',
                                                     'cantidad': '4', 'costo_unitario': '500', 'fecha': '2026-10-01',
                                                     'motivo': 'Inventario inicial'})
        self.assertEqual(r.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.stock, stock0 + 4)
        self.assertEqual(stock_en(p, self.callao), 4)
        r = self.client.post(reverse('inv_ajuste'), {'producto': p.pk, 'almacen': self.callao.pk, 'tipo': 'SALIDA',
                                                     'cantidad': '9', 'fecha': '2026-10-01', 'motivo': 'Merma'})
        self.assertIn('cantidad', r.context['form'].errors)  # no hay 9 en Callao
        r = self.client.get(reverse('inv_valorizacion') + '?mes=2026-10')
        fila = next(f for f in r.context['filas'] if f['p'] == p)
        self.assertEqual(fila['cantidad'], stock0 + 4)
        self.assertEqual(self.client.get(reverse('inv_valorizacion') + '?mes=2026-10&formato=excel').status_code, 200)
        r = self.client.get(reverse('inv_kardex') + f'?producto={p.pk}&almacen={self.callao.pk}')
        self.assertEqual(r.context['final'], 4)

    # ---------------------------------------------------------------- tipo de cambio y dashboard
    def test_tipo_cambio_y_consolidado(self):
        r = self.client.get(reverse('tipo_cambio_api') + '?fecha=2026-09-30')
        self.assertEqual(r.json()['venta'], '3.450')
        self.assertTrue(TipoCambio.objects.filter(fecha='2026-09-30').exists())
        r = self.client.get(reverse('dashboard'))
        usd = sum(c.saldo for c in Cuenta.objects.filter(moneda='USD'))
        pen = sum(c.saldo for c in Cuenta.objects.filter(moneda='PEN'))
        self.assertEqual(r.context['saldo_consolidado'], pen + usd * Decimal('3.450'))

    def test_sin_internet_no_bloquea(self):
        self.mock_tc.side_effect = OSError('sin red')
        TipoCambio.objects.all().delete()
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['tc'])
        self.assertEqual(self.mock_tc.call_count, 1)  # no reintenta 7 veces sin conexión

    def test_registro_abre_ultimo_periodo_con_datos(self):
        Venta.objects.update(periodo='202608')
        r = self.client.get(reverse('ventas:registro'))
        self.assertEqual(r.context['periodo'], '202608')
        self.assertTrue(r.context['docs'])

    # ---------------------------------------------------------------- notas y SUNAT
    def test_asistente_notas(self):
        venta = Venta.objects.filter(tipo_comprobante='01').first()
        r = self.client.post(reverse('ventas:notas'), {'referencia': venta.pk, 'tipo': '07', 'motivo': '07',
                                                       'sustento': 'Devolución'})
        self.assertEqual(r.status_code, 302)
        r = self.client.get(r['Location'])
        self.assertEqual(r.context['form'].initial['doc_referencia'], venta.pk)
        self.assertEqual(r.context['form'].initial['motivo_nota'], '07')

    def test_envio_sunat_simulado(self):
        cfg = FacturacionConfig.actual()
        cfg.proveedor, cfg.ruta, cfg.token = 'NUBEFACT', 'https://demo.example/api', 'tok'
        cfg.save()
        venta = Venta.objects.filter(tipo_comprobante='01').first()
        payload = sunat.payload_comprobante(venta)
        self.assertEqual(payload['tipo_de_comprobante'], 1)
        self.assertEqual(payload['total'], float(venta.total))
        respuesta = {'aceptada_por_sunat': True, 'sunat_description': 'La Factura ha sido aceptada',
                     'enlace_del_pdf': 'https://demo.example/f.pdf', 'codigo_hash': 'abc',
                     'cadena_para_codigo_qr': 'qr'}
        with patch('core.sunat._post', return_value=respuesta) as post:
            r = self.client.post(reverse('ventas:enviar_sunat', args=[venta.pk]))
        self.assertEqual(r.status_code, 302)
        venta.refresh_from_db()
        self.assertEqual(venta.estado_sunat, 'ACEPTADO')
        self.assertEqual(post.call_args[0][0]['operacion'], 'generar_comprobante')
        # aceptada: ya no se puede editar
        r = self.client.get(reverse('ventas:editar', args=[venta.pk]))
        self.assertEqual(r.status_code, 302)
        with patch('core.sunat._post', side_effect=sunat.ErrorFacturacion('Token inválido')):
            otra = Venta.objects.filter(tipo_comprobante='01').exclude(pk=venta.pk).first()
            self.client.post(reverse('ventas:enviar_sunat', args=[otra.pk]))
        otra.refresh_from_db()
        self.assertEqual(otra.estado_sunat, 'ERROR')
        self.assertIn('Token', otra.sunat_descripcion)
