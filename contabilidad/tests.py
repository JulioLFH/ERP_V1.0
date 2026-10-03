"""Pruebas del módulo de contabilidad: centralización, cuadre, destinos, libros y estados financieros."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra
from finanzas.models import Cuenta, Movimiento
from ventas.models import Venta

from . import reportes
from .centralizar import centralizar_periodo
from .models import Asiento, AsientoLinea, CentroCosto, CuentaContable, PeriodoContable


def lineas(asiento, codigo):
    return asiento.lineas.filter(cuenta__codigo=codigo)


class ContabilidadTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.periodos = sorted(set(Compra.objects.values_list('periodo', flat=True)) |
                               set(Venta.objects.values_list('periodo', flat=True)) |
                               {f.strftime('%Y%m') for f in Movimiento.objects.dates('fecha', 'month')})
        for p in self.periodos:
            r = centralizar_periodo(p)
            self.assertFalse([e for e in r['errores'] if 'no cuadra' in e], r['errores'])

    def test_pcge_cargado(self):
        self.assertTrue(CuentaContable.objects.filter(codigo='40111', imputable=True).exists())
        self.assertFalse(CuentaContable.objects.get(codigo='4011').imputable)
        c = CuentaContable.objects.get(codigo='6399')
        self.assertEqual((c.destino_debe.codigo, c.destino_haber.codigo), ('941', '7911'))

    def test_todos_los_asientos_cuadran(self):
        self.assertTrue(Asiento.objects.exists())
        for a in Asiento.objects.all():
            self.assertTrue(a.cuadrado, f'{a} no cuadra')

    def test_asiento_venta(self):
        v = Venta.objects.filter(tipo_comprobante='01').first()
        a = v.asientos.get()
        self.assertEqual(a.libro, '14')
        self.assertEqual(lineas(a, '1212').get().debe, v.total)
        self.assertEqual(lineas(a, '40111').get().haber, v.igv)
        ingresos = a.lineas.filter(cuenta__codigo__startswith='70').aggregate(h=Sum('haber'))['h']
        self.assertEqual(ingresos, v.base_imponible)
        # la primera factura de la demo incluye un servicio: se separa en la 7041
        if v.items.filter(producto__tipo='SERVICIO').exists():
            self.assertTrue(lineas(a, '7041').exists())

    def test_asiento_compra_mercaderia_con_destino(self):
        c = Compra.objects.filter(clasificacion='MERCADERIA').first()
        a = c.asientos.get()
        self.assertEqual(lineas(a, '6011').get().debe, c.base_imponible)
        self.assertEqual(lineas(a, '4212').get().haber, c.total)
        # la compra queda "por recibir"; el ingreso al almacén del kardex la pasa a 20111
        self.assertEqual(lineas(a, '2811').get().debe, c.base_imponible)
        self.assertEqual(lineas(a, '6111').get().haber, c.base_imponible)
        inv = Asiento.objects.get(origen='INVENTARIO', periodo=c.periodo)
        self.assertTrue(inv.lineas.filter(cuenta__codigo='2811', haber__gt=0).exists())

    def test_asiento_servicio_destino_94(self):
        c = Compra.objects.get(clasificacion='SERVICIO')
        a = c.asientos.get()
        self.assertTrue(lineas(a, '6399').exists())
        self.assertEqual(lineas(a, '941').get().debe, c.base_imponible)
        self.assertEqual(lineas(a, '7911').get().haber, c.base_imponible)

    def test_cobranza_y_pago(self):
        cobro = Movimiento.objects.filter(concepto='COBRANZA').first()
        a = cobro.asientos.get()
        # cada caja o banco tiene su propia subcuenta (ej. 10111 caja, 10411 BCP soles)
        sub = cobro.cuenta.cuenta_contable.codigo
        self.assertTrue(sub.startswith('1011' if cobro.cuenta.tipo == 'CAJA' else '1041') and len(sub) == 5)
        self.assertEqual(lineas(a, sub).get().debe, cobro.monto)
        self.assertEqual(lineas(a, '1212').get().haber, cobro.monto)
        pago = Movimiento.objects.filter(concepto='PAGO').first()
        a = pago.asientos.get()
        self.assertEqual(lineas(a, '4212').get().debe, pago.monto)
        gasto = Movimiento.objects.get(concepto='GASTO_BANCARIO')
        a = gasto.asientos.get()
        self.assertTrue(lineas(a, '6391').exists())
        self.assertTrue(lineas(a, '941').exists())  # destino automático

    def test_costo_de_ventas(self):
        p = Venta.objects.filter(stock_aplicado=True).first().periodo
        a = Asiento.objects.get(origen='INVENTARIO', periodo=p)
        self.assertTrue(a.cuadrado)
        self.assertGreater(lineas(a, '69111').get().debe, 0)

    def test_balance_y_estados_cuadran(self):
        desde, hasta = self.periodos[0], self.periodos[-1]
        filas, tot = reportes.balance_comprobacion(desde, hasta)
        self.assertEqual(tot['d'], tot['h'])
        self.assertEqual(tot['sd'], tot['sa'])
        self.assertEqual(tot['res_bal'], tot['res_nat'])
        self.assertEqual(tot['res_bal'], tot['res_fun'])
        _, neta = reportes.estado_resultados(desde, hasta)
        self.assertEqual(neta, tot['res_fun'])
        esf = reportes.situacion_financiera(hasta, desde)
        self.assertEqual(esf['t']['diferencia'], 0)

    def test_recentralizar_conserva_manuales(self):
        p = self.periodos[-1]
        caja = CuentaContable.objects.get(codigo='1011')
        capital = CuentaContable.objects.get(codigo='5011')
        data = {
            'fecha': f'{p[:4]}-{p[4:]}-01', 'libro': '05', 'glosa': 'Asiento de apertura', 'moneda': 'PEN',
            'tipo_cambio': '1', 'lineas-TOTAL_FORMS': '2', 'lineas-INITIAL_FORMS': '0', 'lineas-MIN_NUM_FORMS': '0',
            'lineas-MAX_NUM_FORMS': '1000', 'lineas-0-cuenta': caja.pk, 'lineas-0-debe': '1000', 'lineas-0-haber': '',
            'lineas-1-cuenta': capital.pk, 'lineas-1-debe': '', 'lineas-1-haber': '1000',
        }
        r = self.client.post(reverse('contabilidad:asiento_nuevo'), data)
        self.assertEqual(r.status_code, 302)
        manual = Asiento.objects.get(origen='MANUAL')
        self.assertTrue(manual.numero.startswith(f'05-{p}-'))
        r = self.client.post(reverse('contabilidad:asiento_nuevo'), {**data, 'lineas-1-haber': '900'})
        self.assertContains(r, 'no cuadra')
        centralizar_periodo(p)
        self.assertTrue(Asiento.objects.filter(pk=manual.pk).exists())

    def test_periodo_cerrado_bloquea(self):
        p = self.periodos[-1]
        PeriodoContable.objects.update_or_create(periodo=p, defaults={'cerrado': True})
        with self.assertRaises(Exception):
            centralizar_periodo(p)
        venta = Venta.objects.filter(periodo=p).first()
        if venta:
            r = self.client.get(reverse('ventas:editar', args=[venta.pk]))
            self.assertEqual(r.status_code, 302)
        cuenta = Cuenta.objects.filter(tipo='CAJA').first()
        r = self.client.post(reverse('finanzas:movimiento_nuevo'), {
            'cuenta': cuenta.pk, 'fecha': f'{p[:4]}-{p[4:]}-02', 'tipo': 'EGRESO', 'concepto': 'OTRO',
            'medio_pago': 'EFECTIVO', 'monto': '10'})
        self.assertIn('fecha', r.context['form'].errors)

    def test_centro_de_costo(self):
        cc = CentroCosto.objects.create(codigo='ADM', nombre='Administración')
        c = Compra.objects.get(clasificacion='SERVICIO')
        Compra.objects.filter(pk=c.pk).update(centro_costo=cc)
        centralizar_periodo(c.periodo)
        r = self.client.get(reverse('contabilidad:centros_reporte') + f'?desde={c.periodo}&hasta={c.periodo}')
        self.assertIn('ADM', r.context['grupos'])
        self.assertEqual(r.context['grupos']['ADM']['total'], c.base_imponible)

    def test_paginas_y_exportaciones(self):
        p = self.periodos[-1]
        a = Asiento.objects.first()
        cuenta = CuentaContable.objects.get(codigo='1212')
        urls = [reverse(n) for n in ('contabilidad:periodos', 'contabilidad:asientos', 'contabilidad:asiento_nuevo',
                                     'contabilidad:diario', 'contabilidad:mayor', 'contabilidad:balance',
                                     'contabilidad:situacion', 'contabilidad:resultados', 'contabilidad:plan',
                                     'contabilidad:configuracion', 'contabilidad:centros',
                                     'contabilidad:centros_reporte', 'contabilidad:cuenta_nueva')]
        urls += [reverse('contabilidad:asiento_detalle', args=[a.pk]),
                 reverse('contabilidad:mayor') + f'?cuenta={cuenta.pk}',
                 reverse('contabilidad:balance') + '?nivel=2',
                 reverse('compras:detalle', args=[Compra.objects.first().pk])]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for url in (reverse('contabilidad:diario') + f'?periodo={p}&formato=excel',
                    reverse('contabilidad:diario') + f'?periodo={p}&formato=ple',
                    reverse('contabilidad:mayor') + f'?cuenta={cuenta.pk}&formato=excel',
                    reverse('contabilidad:balance') + '?formato=excel',
                    reverse('contabilidad:plan') + '?formato=ple'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn('attachment', r['Content-Disposition'])
        r = self.client.post(reverse('contabilidad:periodos'), {'periodo': p, 'accion': 'centralizar'})
        self.assertEqual(r.status_code, 302)
