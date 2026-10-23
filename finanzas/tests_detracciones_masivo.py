"""Pagos masivos sin la detracción por depositar y depósito masivo de detracciones con el archivo de SUNAT (v1.72.0)."""
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra
from core.models import Empresa, Producto, Tercero

from . import detracciones_masivo as dm
from .models import Cuenta, DepositoDetraccion, PagoMasivo


class DepositoMasivoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.servicio = Producto.objects.get(codigo='P003')
        cls.servicio.detraccion_codigo, cls.servicio.detraccion_pct = '18', D('12')  # 020 mantenimiento
        cls.servicio.save()
        cls.prov = Tercero.objects.filter(tipo='PROVEEDOR', tipo_doc='6').first()
        cls.prov.cuenta_detracciones = '00-068-111111'
        cls.prov.save()
        cls.banco = Cuenta.objects.filter(tipo='BANCO', moneda='PEN', es_detracciones=False).first()
        Cuenta.objects.filter(pk=cls.banco.pk).update(saldo_inicial=D('100000'))

    def setUp(self):
        Empresa.objects.update(exigir_orden_compra=False, bloquear_deuda_vencida=False)
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def compra(self, numero, precio):
        r = self.client.post(reverse('compras:nuevo'), {
            'tipo_comprobante': '01', 'serie': 'F700', 'numero': numero, 'tercero': self.prov.pk,
            'fecha_emision': '2026-10-01', 'fecha_vencimiento': '2026-10-01', 'periodo': '',
            'clasificacion': 'MERCADERIA', 'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1',
            'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
            'icbper': '0', 'glosa': '', 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000', 'items-0-producto': self.servicio.pk,
            'items-0-descripcion': 'Servicio', 'items-0-cantidad': '1', 'items-0-precio_unitario': precio})
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        return Compra.objects.get(serie='F700', numero=numero)

    def test_pago_masivo_no_incluye_la_detraccion(self):
        c = self.compra('1', '1000')  # 1180, detracción 141.60
        url = reverse('finanzas:pago_masivo_nuevo') + f'?cuenta={self.banco.pk}&hasta=2026-12-31'
        r = self.client.get(url)
        self.assertContains(r, 'data-saldo="1038.40"')
        self.assertContains(r, 'Detracción por depositar 141.60')
        self.client.post(url, {'fecha': '2026-10-05', f'monto_{c.pk}': '1180'})  # el total: no se permite
        self.assertFalse(PagoMasivo.objects.exists())
        self.client.post(url, {'fecha': '2026-10-05', f'monto_{c.pk}': '1038.40'})
        self.assertEqual(PagoMasivo.objects.get().total, D('1038.40'))
        # el pago individual también sugiere el saldo sin la detracción
        r = self.client.get(reverse('finanzas:pago') + f'?doc={c.pk}')
        self.assertContains(r, 'data-valor="1038.40"')

    def test_lote_archivo_sunat_pago_y_constancias(self):
        c1, c2 = self.compra('1', '1000'), self.compra('2', '2000')  # 141.60 -> 142 y 283.20 -> 283
        url = reverse('finanzas:detracciones_masivas_nuevo') + f'?cuenta={self.banco.pk}&hasta=2026-12-31'
        r = self.client.get(url)
        self.assertContains(r, 'data-importe="142"')
        self.assertContains(r, 'data-importe="283"')
        self.assertContains(r, '<option value="020" selected>')  # código del producto
        r = self.client.post(url, {'cuenta': self.banco.pk, 'fecha': '2026-10-05', f'codigo_{c1.pk}': '020',
                                   f'monto_{c1.pk}': '142', f'codigo_{c2.pk}': '020', f'monto_{c2.pk}': '283'})
        lote = DepositoDetraccion.objects.get()
        self.assertRedirects(r, reverse('finanzas:detraccion_masiva', args=[lote.pk]), fetch_redirect_response=False)
        self.assertEqual((lote.lote, lote.total), ('260001', D('425')))
        # en un lote por pagar no vuelven a salir para otro lote
        self.assertEqual([c for c in dm.compras_por_depositar() if c.serie == 'F700'], [])
        # archivo para SUNAT: cabecera de 68 caracteres y detalles de 107
        r = self.client.get(reverse('finanzas:detraccion_masiva', args=[lote.pk]), {'formato': 'txt'})
        ruc = Empresa.actual().ruc
        self.assertIn(f'filename="D{ruc}260001.txt"', r['Content-Disposition'])
        lineas = r.content.decode('ascii').split('\r\n')[:-1]
        self.assertEqual([len(l) for l in lineas], [68, 107, 107])
        cab, det = lineas[0], lineas[1]
        self.assertEqual((cab[0], cab[1:12], cab[47:53], cab[53:68]), ('*', ruc, '260001', '000000000042500'))
        self.assertEqual((det[0], det[1:12].strip(), det[47:56], det[56:59], det[59:70], det[70:85], det[85:87],
                          det[87:93], det[93:95], det[95:99], det[99:107]),
                         ('6', self.prov.numero_doc, '000000000', '020', '00068111111', '000000000014200', '01',
                          '202610', '01', 'F700', '00000001'))
        # pago con la constancia de la primera; la segunda queda depositada sin constancia
        l1, l2 = lote.lineas.order_by('id')
        self.client.post(reverse('finanzas:detraccion_masiva', args=[lote.pk]), {
            'accion': 'registrar', 'fecha': '2026-10-06', 'numero_operacion': 'NPS-1',
            f'constancia_{l1.pk}': '111222333', f'constancia_{l2.pk}': ''})
        lote.refresh_from_db()
        c1, c2 = Compra.objects.get(pk=c1.pk), Compra.objects.get(pk=c2.pk)
        self.assertEqual(lote.estado, 'PAGADO')
        self.assertEqual((c1.detraccion_estado[0], c1.detraccion_constancia), ('DEPOSITADA', '111222333'))
        self.assertEqual(c2.detraccion_estado[0], 'SIN_CONSTANCIA')
        self.assertEqual((c1.saldo, c1.detraccion_por_depositar), (D('1038.00'), 0))  # 1180 - 142 al BN
        # constancias desde el archivo de SUNAT (CSV con serie, número y constancia)
        csv = SimpleUploadedFile('constancias.csv', b'Serie;Numero;Constancia\nF700;00000002;444555666\n')
        self.client.post(reverse('finanzas:detraccion_masiva', args=[lote.pk]),
                         {'accion': 'constancias', 'archivo': csv})
        c2 = Compra.objects.get(pk=c2.pk)
        self.assertEqual((c2.detraccion_estado[0], c2.detraccion_constancia, str(c2.detraccion_fecha)),
                         ('DEPOSITADA', '444555666', '2026-10-06'))
        txt = self.client.get(reverse('compras:registro') + '?formato=ple&periodo=202610').content.decode()
        campos = next(l for l in txt.splitlines() if '|F700|' in l and '|2|' in l).split('|')
        self.assertEqual((campos[31], campos[32]), ('06/10/2026', '444555666'))

    def test_archivo_exige_cuenta_bn_del_proveedor(self):
        Tercero.objects.filter(pk=self.prov.pk).update(cuenta_detracciones='')
        c = self.compra('1', '1000')
        lote = dm.crear_lote(self.banco, c.fecha_emision, [(c, '020', D('142'))], self.user)
        self.assertTrue(any('cuenta de detracciones' in e for e in dm.errores_archivo(lote)))
        r = self.client.get(reverse('finanzas:detraccion_masiva', args=[lote.pk]), {'formato': 'txt'})
        self.assertEqual(r.status_code, 302)
        self.client.post(reverse('finanzas:detraccion_masiva', args=[lote.pk]), {'accion': 'anular'})
        lote.refresh_from_db()
        self.assertEqual(lote.estado, 'ANULADO')
        self.assertEqual([x.pk for x in dm.compras_por_depositar() if x.serie == 'F700'], [c.pk])  # vuelve a quedar
