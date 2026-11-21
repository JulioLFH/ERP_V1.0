"""v1.117.0: tesorería — sustento obligatorio en cobranzas (salvo efectivo), cobranza múltiple por cliente y
vencimiento, movimientos masivos desde el extracto, reglas de aprobación de pagos y token de Decolecta compartido."""
import json
import os
import tempfile
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra
from core.models import Empresa, ReglaAprobacion, Tercero
from ventas.models import Venta

from . import propuesta_pagos as pp
from .models import Cuenta, Extracto, LineaExtracto, Movimiento

HOY = timezone.localdate()


def voucher():
    return SimpleUploadedFile('voucher.png', b'\x89PNG\r\n\x1a\n' + b'0' * 40, content_type='image/png')


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t1117', 't@t.com', 'Clave-segura-9')
        cls.banco = Cuenta.objects.filter(tipo='BANCO', moneda='PEN', es_detracciones=False).first()
        Cuenta.objects.filter(pk=cls.banco.pk).update(saldo_inicial=D('100000'))
        cls.caja = Cuenta.objects.filter(tipo='CAJA', moneda='PEN').first() or Cuenta.objects.create(
            nombre='Caja chica', tipo='CAJA', moneda='PEN')
        clis = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').order_by('nombre')[:2]
        cls.cli_a, cls.cli_b = clis[0], clis[1]
        cls.prov = Tercero.objects.filter(tipo='PROVEEDOR', tipo_doc='6').first()

    def setUp(self):
        Empresa.objects.update(exigir_orden_compra=False, bloquear_deuda_vencida=False)
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def venta(self, tercero, numero, vence, total=D('118')):
        return Venta.objects.create(tercero=tercero, tipo_comprobante='01', serie='F900', numero=numero,
                                    fecha_emision=HOY - timedelta(days=30), fecha_vencimiento=vence,
                                    base_imponible=total / D('1.18'), igv=total - total / D('1.18'), total=total,
                                    forma_pago='CREDITO')

    def compra(self, numero, total=D('1180')):
        return Compra.objects.create(tercero=self.prov, tipo_comprobante='01', serie='F901', numero=numero,
                                     fecha_emision=HOY - timedelta(days=5), fecha_vencimiento=HOY,
                                     base_imponible=total / D('1.18'), igv=total - total / D('1.18'), total=total)


class CobranzaSustentoTest(Base):
    def cobrar(self, venta, cuenta, **extra):
        return self.client.post(reverse('finanzas:cobranza') + f'?tercero={venta.tercero_id}', {
            'cuenta': cuenta.pk, 'fecha': HOY.isoformat(), 'medio_pago': 'TRANSFERENCIA', 'numero_operacion': '77',
            f'monto_{venta.pk}': '50', **extra})

    def test_sin_sustento_no_se_registra_salvo_efectivo_en_caja(self):
        v = self.venta(self.cli_a, '1', HOY)
        r = self.cobrar(v, self.banco)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Adjunte el sustento del cobro')
        self.assertFalse(Movimiento.objects.filter(venta=v).exists())
        # efectivo, pero a un banco: no
        r = self.cobrar(v, self.banco, en_efectivo='on')
        self.assertContains(r, 'el efectivo se registra en una cuenta de caja')
        self.assertFalse(Movimiento.objects.filter(venta=v).exists())
        # efectivo en caja: sí, sin sustento y con medio «Efectivo»
        r = self.cobrar(v, self.caja, en_efectivo='on')
        self.assertEqual(r.status_code, 302)
        m = Movimiento.objects.get(venta=v)
        self.assertEqual((m.medio_pago, m.monto), ('EFECTIVO', D('50')))

    def test_con_sustento_se_registra(self):
        v = self.venta(self.cli_a, '2', HOY)
        r = self.cobrar(v, self.banco, sustento=voucher())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Movimiento.objects.get(venta=v).medio_pago, 'TRANSFERENCIA')

    def test_los_pagos_no_piden_el_campo_efectivo(self):
        r = self.client.get(reverse('finanzas:pago') + f'?tercero={self.prov.pk}')
        self.assertNotContains(r, 'name="en_efectivo"')
        r = self.client.get(reverse('finanzas:cobranza') + f'?tercero={self.cli_a.pk}')
        self.assertContains(r, 'name="en_efectivo"')

    def test_cobranza_multiple_exige_sustento_y_ordena_por_cliente_y_vencimiento(self):
        b1 = self.venta(self.cli_b, '10', HOY - timedelta(days=1))
        a2 = self.venta(self.cli_a, '11', HOY + timedelta(days=9))
        a1 = self.venta(self.cli_a, '12', HOY - timedelta(days=9))
        url = reverse('finanzas:cobranza_multiple') + '?q=F900'
        html = self.client.get(url).content.decode()
        orden = [html.index(f'name="monto_{v.pk}"') for v in (a1, a2, b1)]
        self.assertEqual(orden, sorted(orden))  # cliente A (por vencimiento) y luego cliente B
        datos = {'cuenta': self.banco.pk, 'fecha': HOY.isoformat(), 'medio_pago': 'TRANSFERENCIA',
                 f'monto_{a1.pk}': '20', f'monto_{b1.pk}': '30'}
        r = self.client.post(url, datos)
        self.assertContains(r, 'Adjunte el sustento del cobro')
        self.assertFalse(Movimiento.objects.filter(venta__in=[a1, b1]).exists())
        r = self.client.post(url, {**datos, 'sustento': voucher()})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Movimiento.objects.filter(venta__in=[a1, b1], concepto='COBRANZA').count(), 2)


class ExtractoMasivoTest(Base):
    def test_crea_e_ignora_las_lineas_marcadas(self):
        ext = Extracto.objects.create(cuenta=self.banco, desde=HOY, hasta=HOY)
        l1 = LineaExtracto.objects.create(extracto=ext, fecha=HOY, descripcion='ABONO CLIENTE X', monto=D('500'))
        l2 = LineaExtracto.objects.create(extracto=ext, fecha=HOY, descripcion='COMISION MANTENIMIENTO',
                                          monto=D('-12'))
        l3 = LineaExtracto.objects.create(extracto=ext, fecha=HOY, descripcion='OTRO', monto=D('-40'))
        r = self.client.get(reverse('finanzas:extracto', args=[ext.pk]))
        self.assertContains(r, 'name="lineas"', count=3)
        url = reverse('finanzas:extracto_accion', args=[ext.pk])
        r = self.client.post(url, {'accion': 'crear_marcadas', 'lineas': [l1.pk, l2.pk], 'concepto': ''})
        self.assertRedirects(r, reverse('finanzas:extracto', args=[ext.pk]), fetch_redirect_response=False)
        l1.refresh_from_db(), l2.refresh_from_db()
        self.assertEqual((l1.estado, l2.estado), ('CREADA', 'CREADA'))
        self.assertEqual((l1.movimiento.tipo, l1.movimiento.monto), ('INGRESO', D('500')))
        self.assertEqual(l2.movimiento.concepto, 'GASTO_BANCARIO')
        self.client.post(url, {'accion': 'ignorar_marcadas', 'lineas': [l3.pk, l1.pk]})  # l1 ya no está pendiente
        l3.refresh_from_db()
        self.assertEqual(l3.estado, 'IGNORADA')
        self.assertEqual(Movimiento.objects.filter(lineas_extracto__extracto=ext).count(), 2)


class AprobacionPagosTest(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.jefe = User.objects.create_user('jefe1117', password='Clave-segura-9')
        cls.gerente = User.objects.create_user('gerente1117', password='Clave-segura-9')

    def reglas(self):
        ReglaAprobacion.objects.create(documento='PAGO', nivel=1, tipo='USUARIO', aprobador=self.jefe)
        ReglaAprobacion.objects.create(documento='PAGO', nivel=2, tipo='USUARIO', aprobador=self.gerente,
                                       monto_desde=D('1000'))

    def test_propuesta_pasa_por_los_niveles_de_las_reglas(self):
        self.reglas()
        self.compra('1')
        p = pp.armar(self.banco, HOY, HOY + timedelta(days=10), D('0'), self.user)
        pp.enviar(p, self.user)
        p.refresh_from_db()
        self.assertEqual([n['usuario'] for n in p.niveles], [self.jefe.pk, self.gerente.pk])
        self.assertFalse(pp.puede_aprobar(self.gerente, p))  # todavía le toca al nivel 1
        self.assertFalse(pp.puede_aprobar(self.user, p))  # quien la armó no la aprueba
        from core.aprobaciones import pendientes_de
        self.assertEqual(pendientes_de(self.jefe).get('PAGO'), 1)
        pp.aprobar(p, self.jefe)
        p.refresh_from_db()
        self.assertEqual((p.estado, p.nivel_aprobado), ('ENVIADA', 1))
        with self.assertRaises(pp.ErrorPropuesta):
            pp.aprobar(p, self.jefe)
        pp.aprobar(p, self.gerente)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'APROBADA')
        self.assertEqual([r['estado'] for r in pp.ruta(p)], ['APROBADO', 'APROBADO'])
        r = self.client.get(reverse('finanzas:propuesta', args=[p.pk]))
        self.assertContains(r, 'Ruta de aprobación')

    def test_monto_menor_al_segundo_nivel_solo_pide_el_primero(self):
        self.reglas()
        c = self.compra('2', total=D('590'))
        p = pp.armar(self.banco, HOY, HOY + timedelta(days=10), D('0'), self.user)
        pp.cambiar(p, set(p.lineas.filter(compra=c).values_list('pk', flat=True)))  # solo esta compra
        pp.enviar(p, self.user)
        self.assertEqual([n['usuario'] for n in p.niveles], [self.jefe.pk])

    def test_sin_reglas_sigue_como_antes(self):
        self.compra('3')
        p = pp.armar(self.banco, HOY, HOY + timedelta(days=10), D('0'), self.user)
        pp.enviar(p, self.user)
        self.assertEqual(p.niveles, [])
        self.assertEqual(pp.error_pago_directo([]), '')

    def test_con_reglas_el_pago_directo_va_por_la_propuesta(self):
        c = self.compra('4')
        datos = {'cuenta': self.banco.pk, 'fecha': HOY.isoformat(), 'medio_pago': 'TRANSFERENCIA',
                 'numero_operacion': '9', f'monto_{c.pk}': '100'}
        url = reverse('finanzas:pago') + f'?tercero={self.prov.pk}'
        self.reglas()
        r = self.client.post(url, datos, follow=True)
        self.assertContains(r, 'necesitan aprobación')
        self.assertFalse(Movimiento.objects.filter(compra=c).exists())
        ReglaAprobacion.objects.filter(documento='PAGO').update(monto_desde=D('5000'))
        r = self.client.post(url, datos)
        self.assertEqual(r.status_code, 302)  # bajo el umbral se paga directo
        self.assertTrue(Movimiento.objects.filter(compra=c, concepto='PAGO').exists())


class TokenCompartidoTest(Base):
    def setUp(self):
        super().setUp()
        from core import token_decolecta
        token_decolecta._memo.update(ruta=None, mtime=None, datos={})
        d = tempfile.mkdtemp()
        self.ruta = os.path.join(d, 'compartido.json')

    def escribir(self, token):
        with open(self.ruta, 'w', encoding='utf-8') as f:
            json.dump({'decolecta_token': token}, f)
        os.utime(self.ruta, None)

    def test_el_token_del_panel_tiene_prioridad_y_sin_el_queda_el_de_la_empresa(self):
        from core import tipo_cambio, token_decolecta
        Empresa.objects.update(token_tipo_cambio='de-la-empresa')
        with override_settings(TOKENS_COMPARTIDOS=self.ruta):
            self.assertEqual(token_decolecta.token(), 'de-la-empresa')  # aún no hay archivo
            self.escribir('compartido-del-panel-123456')
            self.assertEqual(tipo_cambio._token(), 'compartido-del-panel-123456')
            self.assertEqual(token_decolecta.oculto(token_decolecta.compartido()), '••••3456')
            Empresa.objects.update(fuente_tipo_cambio='SBS', token_tipo_cambio='')
            self.assertEqual(tipo_cambio.fuente_actual(), ('SBS', 'compartido-del-panel-123456'))
        self.assertEqual(token_decolecta.compartido(), '')  # en las pruebas no se lee el archivo del servidor

    def test_padron_ruc_usa_el_token_compartido(self):
        from core import padron_ruc
        Empresa.objects.update(token_tipo_cambio='')
        self.escribir('compartido-del-panel-abcdef')
        with override_settings(TOKENS_COMPARTIDOS=self.ruta), \
                patch('core.padron_ruc._pedir', return_value={'razon_social': 'ACME SAC'}) as pedir:
            padron_ruc.consultar('20100070970')
        self.assertEqual(pedir.call_args[0][1], 'compartido-del-panel-abcdef')
