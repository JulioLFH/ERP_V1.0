"""Checklist v1.74.0 OBR-06: reparto de gastos configurable por cada empresa (origen, base, destinos, cascada),
simulación, ejecución, recálculo al centralizar, reversión con motivo, bloqueo al cierre y resultado por obra."""
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from core.models import Bitacora
from core.modulos import GRUPOS
from obras.models import Obra, Presupuesto

from . import cierre
from . import reparto as rp
from .centralizar import Borrador, centralizar_periodo
from .models import (Asiento, AsientoLinea, CentroCosto, CifraReparto, CuentaContable, EjecucionReparto,
                     PeriodoContable, ReglaReparto, ValorCifra)

P = '203105'
F = date(2031, 5, 20)


def cuenta(codigo):
    return CuentaContable.objects.filter(codigo=codigo).first() or CuentaContable.objects.create(
        codigo=codigo, nombre=f'Cuenta {codigo}', imputable=True)


def gasto(centro, monto, codigo='6361', fecha=F):
    """Gasto manual con centro de costo (63 servicios contra 4212)."""
    b = Borrador(Asiento(fecha=fecha, origen='MANUAL', glosa=f'Gasto {centro.codigo}'))
    b.add(cuenta(codigo), debe=D(monto), centro_costo=centro)
    b.add(cuenta('4212'), haber=D(monto))
    b.agregar_destinos()
    return b.grabar()


class RepartoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.adm = CentroCosto.objects.create(codigo='ADM', nombre='Administración', tipo='ADMINISTRACION')
        cls.sis = CentroCosto.objects.create(codigo='SIS', nombre='Sistemas', tipo='ADMINISTRACION')
        cls.o1 = Obra.objects.create(nombre='Puente', area_m2=D('300'))
        cls.o2 = Obra.objects.create(nombre='Colegio', area_m2=D('100'))
        cls.obras = CentroCosto.objects.get(codigo='OBRAS')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def regla(self, **datos):
        origenes = datos.pop('origenes')
        destinos = datos.pop('destinos', [])
        r = ReglaReparto.objects.create(**datos)
        r.origenes.set(origenes)
        r.destinos.set(destinos)
        return r

    def por_m2(self):
        return self.regla(orden=20, nombre='Administración → obras por m²', origenes=[self.adm], base='OBRA_M2',
                          tipo_destino='HIJOS', centro_padre=self.obras)

    def asignado(self, centro):
        agg = AsientoLinea.objects.filter(asiento__origen='REPARTO', centro_costo=centro, es_destino=False)
        return sum((l.debe - l.haber for l in agg), D('0'))

    def test_simular_ejecutar_recalcular_y_revertir(self):
        gasto(self.adm, '1000')
        gasto(self.o1.centro_costo, '2000')
        self.por_m2()
        filas = rp.calcular(P)
        self.assertEqual([(d['centro'], d['pct'], d['monto']) for d in filas[0]['destinos']],
                         [(self.o1.centro_costo, D('75.00'), D('750.00')), (self.o2.centro_costo, D('25.00'),
                                                                            D('250.00'))])
        self.assertFalse(Asiento.objects.filter(origen='REPARTO').exists())  # simular no graba
        ej, n = rp.ejecutar(P, self.user)
        a = Asiento.objects.get(origen='REPARTO')
        self.assertTrue(a.cuadrado)
        self.assertEqual((self.asignado(self.adm), self.asignado(self.o1.centro_costo), self.asignado(self.o2.centro_costo)),
                         (D('-1000.00'), D('750.00'), D('250.00')))
        self.assertTrue(a.lineas.filter(es_destino=True).exists())  # con sus destinos 9x
        with self.assertRaisesMessage(rp.ErrorReparto, 'ya se ejecutó'):
            rp.ejecutar(P, self.user)
        # un gasto nuevo de Administración: la centralización recalcula el reparto con los datos al día
        gasto(self.adm, '200')
        centralizar_periodo(P)
        self.assertEqual(Asiento.objects.filter(origen='REPARTO').count(), 1)
        self.assertEqual(self.asignado(self.o1.centro_costo), D('900.00'))
        # resultado por obra con lo asignado
        filas, totales = rp.resultado(P, P, solo_obras=True)
        o1 = next(f for f in filas if f['centro'] == self.o1.centro_costo)
        self.assertEqual((o1['propios'], o1['asignados'], o1['resultado']), (D('2000.00'), D('900.00'), D('-2900.00')))
        self.assertNotIn(self.adm, [f['centro'] for f in filas])
        # revertir exige motivo y queda en la bitácora
        with self.assertRaisesMessage(rp.ErrorReparto, 'motivo'):
            rp.revertir(P, self.user, '')
        rp.revertir(P, self.user, 'Se corrigen los m² del colegio')
        self.assertFalse(Asiento.objects.filter(origen='REPARTO').exists())
        self.assertFalse(EjecucionReparto.objects.exists())
        self.assertTrue(Bitacora.objects.filter(accion='ANULAR', modelo='contabilidad.EjecucionReparto',
                                                motivo__contains='m² del colegio').exists())
        centralizar_periodo(P)
        self.assertFalse(Asiento.objects.filter(origen='REPARTO').exists())  # revertido: ya no se regenera
        # periodo cerrado: no se ejecuta
        PeriodoContable.objects.filter(periodo=P).update(cerrado=True)
        with self.assertRaisesMessage(rp.ErrorReparto, 'cerrado'):
            rp.ejecutar(P, self.user)

    def test_cascada_porcentaje_fijo_y_cifras(self):
        """Sistemas → 50 % Administración y 50 % Puente; luego Administración (con lo recibido) → obras por m²."""
        gasto(self.sis, '400')
        gasto(self.adm, '1000')
        self.regla(orden=10, nombre='Sistemas', origenes=[self.sis], base='FIJO', tipo_destino='LISTA',
                   destinos=[self.adm, self.o1.centro_costo],
                   porcentajes={str(self.adm.pk): '50', str(self.o1.centro_costo_id): '50'})
        self.por_m2()
        rp.ejecutar(P, self.user)
        self.assertEqual(Asiento.objects.filter(origen='REPARTO').count(), 2)
        self.assertEqual(self.asignado(self.sis), D('-400.00'))
        self.assertEqual(self.asignado(self.adm), D('200.00') - D('1200.00'))  # recibe 200 y reparte 1,200
        self.assertEqual(self.asignado(self.o1.centro_costo), D('200.00') + D('900.00'))
        self.assertEqual(self.asignado(self.o2.centro_costo), D('300.00'))
        # base propia: trabajadores por centro; un mes sin valor toma el último registrado
        rp.revertir(P, self.user, 'Cambio de base')
        ReglaReparto.objects.all().delete()
        trab = CifraReparto.objects.create(codigo='TRAB', nombre='Trabajadores')
        ValorCifra.objects.create(cifra=trab, periodo='203101', centro=self.o1.centro_costo, valor=D('8'))
        ValorCifra.objects.create(cifra=trab, periodo='203104', centro=self.o2.centro_costo, valor=D('2'))
        ValorCifra.objects.create(cifra=trab, periodo='203112', centro=self.o2.centro_costo, valor=D('50'))  # futuro
        self.regla(orden=10, nombre='Administración por trabajadores', origenes=[self.adm], base='CIFRA', cifra=trab,
                   tipo_destino='CON_BASE')
        filas = rp.calcular(P)
        self.assertEqual([(d['centro'], d['monto']) for d in filas[0]['destinos']],
                         [(self.o1.centro_costo, D('800.00')), (self.o2.centro_costo, D('200.00'))])

    def test_pantallas_permisos_y_cierre(self):
        gasto(self.adm, '1000')
        Presupuesto.objects.create(obra=self.o1, nombre='Meta', tipo='META', gastos_generales=D('3000'))
        r = self.client.post(reverse('contabilidad:reparto_regla_nueva'), {
            'orden': '10', 'nombre': 'Administración por m²', 'origenes': [self.adm.pk], 'cuentas': '63',
            'base': 'OBRA_M2', 'tipo_destino': 'HIJOS', 'centro_padre': self.obras.pk, 'activo': 'on'})
        self.assertRedirects(r, reverse('contabilidad:reparto'), fetch_redirect_response=False)
        self.assertEqual(ReglaReparto.objects.get().prefijos, ['63'])
        r = self.client.post(reverse('contabilidad:reparto_regla_nueva'), {
            'orden': '20', 'nombre': 'Mal', 'origenes': [self.adm.pk], 'base': 'FIJO', 'tipo_destino': 'HIJOS',
            'centro_padre': self.obras.pk, 'porcentajes_texto': 'NO-EXISTE = 10', 'activo': 'on'})
        self.assertContains(r, 'Revise «NO-EXISTE = 10»')
        url = reverse('contabilidad:reparto') + '?mes=2031-05'
        r = self.client.get(url)
        self.assertContains(r, 'Ejecutar el reparto')
        self.assertContains(r, '750.00')
        self.assertTrue(any(p['titulo'] == 'Reparto de gastos generales' and p['estado'] == 'pendiente'
                            for p in cierre.pasos(P)[0]))
        self.client.post(reverse('contabilidad:reparto'), {'periodo': P, 'accion': 'ejecutar'})
        self.assertTrue(EjecucionReparto.objects.filter(periodo=P).exists())
        self.assertTrue(any(p['titulo'] == 'Reparto de gastos generales' and p['estado'] == 'ok'
                            for p in cierre.pasos(P)[0]))
        r = self.client.get(reverse('contabilidad:reparto_resultado') + '?desde=2031-05&hasta=2031-05&obras=1')
        self.assertContains(r, '25.0 %')  # 750 asignados de 3,000 de gastos generales del presupuesto
        # cifras: valores del mes a mano
        r = self.client.post(reverse('contabilidad:reparto_cifras'), {
            'accion': 'nueva', 'periodo': P, 'codigo': 'M2D', 'nombre': 'm² declarados', 'unidad': 'm²',
            'activo': 'on'})
        cifra = CifraReparto.objects.get(codigo='M2D')
        self.client.post(reverse('contabilidad:reparto_cifras'), {
            'accion': 'valores', 'periodo': P, 'cifra': cifra.pk, f'valor_{self.o1.centro_costo_id}': '1,250.5'})
        self.assertEqual(ValorCifra.objects.get(cifra=cifra).valor, D('1250.5'))
        # un usuario de Obras ve el resultado por obra, pero no ejecuta el reparto
        ingeniero = User.objects.create_user('ing', password='x')
        ingeniero.groups.add(Group.objects.get_or_create(name=GRUPOS['obras'])[0])
        self.client.force_login(ingeniero)
        self.assertEqual(self.client.get(reverse('contabilidad:reparto_resultado')).status_code, 200)
        self.assertEqual(self.client.get(reverse('contabilidad:reparto')).status_code, 403)
