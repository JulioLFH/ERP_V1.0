"""Estado de Flujos de Efectivo (NIC 7): casos de aceptación de docs/EFE_NIC7.md (v1.73.0)."""
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra, CompraItem
from core.models import Tercero
from finanzas.models import Cuenta, Movimiento
from ventas.models import Venta, VentaItem

from . import flujo_efectivo
from .centralizar import centralizar_periodo
from .models import Asiento, AsientoLinea, CuentaContable, PoliticaFlujo, RubroFlujo

P = '203103'  # un periodo sin otros datos
F = date(2031, 3, 10)


def cuenta(codigo):
    return CuentaContable.objects.filter(codigo=codigo).first() or CuentaContable.objects.create(
        codigo=codigo, nombre=f'Cuenta {codigo}', imputable=True)


class FlujoEfectivoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.cli = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20600000011', nombre='CLIENTE SAC')
        cls.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000022',
                                          nombre='PROVEEDOR SAC')
        cls.bcp = Cuenta.objects.create(nombre='BCP soles', banco='BCP', moneda='PEN', saldo_inicial=D('200000'))
        cls.usd = Cuenta.objects.create(nombre='BCP dólares', banco='BCP', moneda='USD')
        cls.bn = Cuenta.objects.create(nombre='BN detracciones', banco='BN', es_detracciones=True,
                                       tipo_efectivo='RESTRINGIDO', numero='00-068-123456')
        cls.caja = Cuenta.objects.create(tipo='CAJA', nombre='Caja chica', moneda='PEN')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    # ------------------------------------------------------------ datos
    def venta(self, numero, base, moneda='PEN', tc='1', detraccion='0'):
        v = Venta.objects.create(tipo_comprobante='01', serie='F001', numero=numero, fecha_emision=F, tercero=self.cli,
                                 moneda=moneda, tipo_cambio=D(tc), detraccion_pct=D(detraccion), descontar_stock=False)
        VentaItem.objects.create(documento=v, descripcion='Servicio', cantidad=1, precio_unitario=D(base))
        v.calcular_totales()
        v.save()
        return v

    def compra(self, numero, base, clasificacion='SERVICIO', detraccion='0'):
        c = Compra.objects.create(tipo_comprobante='01', serie='F001', numero=numero, fecha_emision=F,
                                  tercero=self.prov, clasificacion=clasificacion, ingresar_almacen=False,
                                  detraccion_pct=D(detraccion))
        CompraItem.objects.create(documento=c, descripcion='Bien o servicio', cantidad=1, precio_unitario=D(base))
        c.calcular_totales()
        c.save()
        return c

    def mov(self, cuenta_fin, tipo, concepto, monto, **extra):
        return Movimiento.objects.create(cuenta=cuenta_fin, fecha=F, tipo=tipo, concepto=concepto, monto=D(monto),
                                         **extra)

    def escenario(self):
        v1 = self.venta('1', '1000')                                    # 1180
        self.mov(self.bcp, 'INGRESO', 'COBRANZA', '1180', venta=v1)      # caso 1
        v2 = self.venta('2', '1000', detraccion='12')                   # 1180, detracción 141.60
        self.mov(self.bcp, 'INGRESO', 'COBRANZA', '1038.40', venta=v2)
        self.mov(self.bn, 'INGRESO', 'DETRACCION', '141.60', venta=v2, numero_operacion='500001')  # caso 2
        c1 = self.compra('10', '1000', detraccion='12')                 # 1180 al proveedor de servicios
        self.mov(self.bcp, 'EGRESO', 'PAGO', '1038.40', compra=c1)
        self.mov(self.bcp, 'EGRESO', 'DETRACCION', '141.60', compra=c1, numero_operacion='600001')  # caso 3
        c2 = self.compra('11', '4000', clasificacion='ACTIVO_FIJO')     # 4720, laptop
        self.mov(self.bcp, 'EGRESO', 'PAGO', '4720', compra=c2)          # caso 4
        self.mov(self.bcp, 'INGRESO', 'PRESTAMO', '50000', cuenta_contable=cuenta('4511'))  # caso 6
        self.mov(self.bcp, 'EGRESO', 'OTRO', '100', cuenta_contable=cuenta('6731'))         # intereses
        salida = self.mov(self.bcp, 'EGRESO', 'TRANSFERENCIA', '500')    # caso 7: banco → caja chica
        self.mov(self.caja, 'INGRESO', 'TRANSFERENCIA', '500', transferencia_par=salida)
        v3 = self.venta('3', '1000', moneda='USD', tc='3.70')            # US$ 1180 a 3.70
        self.mov(self.usd, 'INGRESO', 'COBRANZA', '1180', venta=v3)      # caso 8: cobrado a 3.45
        self.mov(self.bcp, 'INGRESO', 'OTRO', '100000', cuenta_contable=cuenta('5011'))      # caso 12
        self.mov(self.bcp, 'EGRESO', 'OTRO', '3000', cuenta_contable=cuenta('1681'),
                 rubro_flujo=RubroFlujo.objects.get(codigo='IN-S03'))   # R1: préstamo otorgado
        r = centralizar_periodo(P)
        self.assertFalse([e for e in r['errores'] if 'no cuadra' in e], r['errores'])
        # caso 11: depreciación del mes (asiento sin efectivo)
        a = Asiento.objects.create(fecha=F, glosa='Depreciación de prueba')
        AsientoLinea.objects.create(asiento=a, cuenta=cuenta('68141'), debe=D('2000'))
        AsientoLinea.objects.create(asiento=a, cuenta=cuenta('39131'), haber=D('2000'))
        return v3

    # ------------------------------------------------------------ casos
    def test_metodo_directo_indirecto_y_validaciones(self):
        self.escenario()
        e = flujo_efectivo.estado(P, P)
        rubros = {f['rubro'].codigo: f['importe'] for s in e['secciones'] for f in s['filas']}
        self.assertEqual(rubros['OP-E01'], D('1180') + D('1180') + D('4071.00'))  # 3: 1180 × 3.45 (DC absorbida)
        self.assertEqual(rubros['OP-S01'], D('-1180'))                             # pago + detracción al proveedor
        self.assertEqual(rubros['IN-S01'], D('-4720'))                             # laptop: inversión
        self.assertEqual(rubros['IN-S03'], D('-3000'))                             # rubro manual (R1)
        self.assertEqual(rubros['FI-E02'], D('50000'))
        self.assertEqual(rubros['FI-E01'], D('100000'))
        self.assertEqual(rubros['FI-S04'], D('-100'))                              # intereses: financiamiento
        self.assertNotIn('OP-S06', rubros)                                         # la transferencia no es flujo
        validas = {v['codigo']: v for v in e['validaciones']}
        for codigo in ('V1', 'V3', 'V4', 'V5', 'V6'):
            self.assertTrue(validas[codigo]['ok'], (codigo, validas[codigo]['detalle']))
        self.assertEqual(e['indirecto']['operacion'], e['total']['OPERACION'])
        self.assertIn(('Depreciación', D('2000.00')), e['indirecto']['ajustes'])
        self.assertEqual(e['inicial'] + e['neto'] + e['efecto_tc'], e['final'])
        self.assertEqual(e['restringido'], D('141.60'))
        # pantalla, detalle de un rubro y Excel
        url = reverse('contabilidad:flujo_efectivo') + f'?desde={P}&hasta={P}'
        r = self.client.get(url)
        self.assertContains(r, 'Compra de propiedad, planta y equipo')
        self.assertContains(r, 'FLUJO NETO DE ACTIVIDADES DE OPERACIÓN')
        self.assertContains(self.client.get(url + '&detalle=IN-S01'), '-4,720.00')
        self.assertIn('spreadsheetml', self.client.get(url + '&formato=excel')['Content-Type'])

    def test_politicas(self):
        self.escenario()
        PoliticaFlujo.objects.update(intereses_pagados='OPERACION', detracciones_en_efectivo=False)
        e = flujo_efectivo.estado(P, P)
        rubros = {f['rubro'].codigo: f['importe'] for s in e['secciones'] for f in s['filas']}
        self.assertEqual(rubros['OP-S07'], D('-100'))                 # intereses en operación
        self.assertNotIn('FI-S04', rubros)
        # el BN fuera del efectivo: la detracción que depositó el cliente no es un cobro
        self.assertEqual(rubros['OP-E01'], D('1180') + D('1038.40') + D('4071.00'))
        validas = {v['codigo']: v for v in e['validaciones']}
        self.assertTrue(validas['V1']['ok'], validas['V1']['detalle'])
        self.assertTrue(validas['V4']['ok'], validas['V4']['detalle'])

    def test_datos_del_sistema_anterior(self):
        """Apertura importada (saldo inicial, no flujo) y gastos registrados solo por función (clase 9)."""
        banco = self.bcp.cuenta_contable

        def asiento(glosa, *lineas):
            a = Asiento.objects.create(fecha=F, glosa=glosa)
            a.origen = 'ANTERIOR'
            a.save(update_fields=['origen'])
            for codigo, debe, haber in lineas:
                AsientoLinea.objects.create(asiento=a, cuenta=banco if codigo == 'banco' else cuenta(codigo),
                                            debe=D(debe), haber=D(haber))
            return a
        asiento('POR LOS SALDOS INICIALES AL APERTURAR EL EJERCICIO 2031', ('banco', '5000', '0'),
                ('3311', '20000', '0'), ('50111', '0', '25000'))
        asiento('APLICACION NC SALDOS INICIALES 000001: cobro', ('banco', '300', '0'), ('1212', '0', '300'))
        asiento('Gasto por función', ('94391200', '52.93', '0'), ('banco', '0', '52.93'))
        e = flujo_efectivo.estado(P, P)
        rubros = {f['rubro'].codigo: f['importe'] for s in e['secciones'] for f in s['filas']}
        self.assertNotIn('FI-E01', rubros)                     # la apertura no es un aporte
        self.assertEqual(rubros['OP-E01'], D('300'))           # el cobro sí es flujo
        self.assertEqual(rubros['OP-S02'], D('-52.93'))        # gasto solo por función
        self.assertEqual(e['efecto_tc'], 0)                    # ...no es diferencia de cambio
        self.assertEqual(e['aperturas'][0][1][:27], 'POR LOS SALDOS INICIALES AL')
        validas = {v['codigo']: v for v in e['validaciones']}
        for codigo in ('V1', 'V4'):
            self.assertTrue(validas[codigo]['ok'], (codigo, validas[codigo]['detalle']))

    def test_configuracion(self):
        r = self.client.get(reverse('contabilidad:flujo_configuracion'))
        self.assertContains(r, 'Reglas por cuenta de contrapartida')
        self.assertEqual(RubroFlujo.objects.count(), 27)
        datos = {'pol-intereses_pagados': 'OPERACION', 'pol-intereses_cobrados': 'INVERSION',
                 'pol-dividendos_pagados': 'FINANCIAMIENTO', 'pol-detracciones_en_efectivo': 'on'}
        for prefijo, fs in (('reglas', r.context['reglas']), ('rubros', r.context['rubros'])):
            existentes = fs.initial_form_count()
            datos.update({f'{prefijo}-TOTAL_FORMS': str(existentes), f'{prefijo}-INITIAL_FORMS': str(existentes),
                          f'{prefijo}-MIN_NUM_FORMS': '0', f'{prefijo}-MAX_NUM_FORMS': '1000'})
            for f in fs.forms[:existentes]:  # lo que muestra la pantalla, sin cambios
                for nombre in f.fields:
                    valor = f[nombre].value()
                    if valor in (None, False, ''):
                        continue
                    datos[f[nombre].html_name] = 'on' if valor is True else valor
        datos['reglas-TOTAL_FORMS'] = str(int(datos['reglas-TOTAL_FORMS']) + 1)  # una regla nueva
        n = int(datos['reglas-TOTAL_FORMS']) - 1
        datos.update({f'reglas-{n}-prefijo': '6599', f'reglas-{n}-rubro_salida':
                      RubroFlujo.objects.get(codigo='OP-S06').pk})
        r = self.client.post(reverse('contabilidad:flujo_configuracion'), datos)
        self.assertEqual(r.status_code, 302, r.content[:2000] if r.status_code == 200 else '')
        self.assertEqual(PoliticaFlujo.actual().intereses_pagados, 'OPERACION')
        from .models import ReglaFlujoCuenta
        self.assertEqual(ReglaFlujoCuenta.objects.get(prefijo='6599').rubro_salida.codigo, 'OP-S06')
