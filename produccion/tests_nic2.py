"""Cierre de costos NIC 2 (v1.24): merma anormal a gasto, liquidación del costo real con capacidad normal (también
multinivel) y prueba del valor neto realizable con su asiento y reversión."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.models import Asiento, AsientoLinea, CentroCosto, CuentaContable
from core.models import Almacen, Producto
from core.permisos import EXPLICITAS

from . import liquidacion, servicios
from .models import (CentroTrabajo, HojaRuta, LiquidacionCosto, ListaMateriales, OrdenProduccion, PruebaVNR,
                     VersionFabricacion)

D = Decimal
HOY = date.today()
FECHA = date(HOY.year, HOY.month, 1) - timedelta(days=1)  # último día del mes anterior (periodo cerrable)
PERIODO = FECHA.strftime('%Y%m')


class BaseNIC2(TestCase):
    # un escenario por clase: en Python 3.10.0 (SQLite local) volver al punto de guardado entre dos pruebas de la
    # misma clase después de registrar producción puede tumbar el proceso; entre clases no ocurre
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.almacen = Almacen.principal()
        self.insumo = Producto.objects.create(nombre='Insumo NIC2', clase='MATERIA_PRIMA')
        self.insumo.mover_stock(D('100'), 'Saldo de prueba', costo=D('10'), almacen=self.almacen,
                                fecha=FECHA - timedelta(days=5))
        self.cc = CentroCosto.objects.create(codigo='PLN', nombre='Planta NIC2', tipo='PRODUCCION')
        self.puesto = CentroTrabajo.objects.create(codigo='ENS', nombre='Ensamble', costo_hora_mo=D('10'),
                                                   costo_hora_cif=D('5'), centro_costo=self.cc,
                                                   horas_normales_mes=D('4'))
        self.ruta = HojaRuta.objects.create(codigo='R1', nombre='Ruta', estado='APROBADA')
        self.ruta.operaciones.create(secuencia=10, centro=self.puesto, descripcion='Armado', horas_unidad=D('0.5'))
        # kit: 1 insumo por unidad con 10 % de merma normal; media hora por unidad
        self.kit = Producto.objects.create(nombre='Kit NIC2', clase='PRODUCTO_TERMINADO', precio_venta=D('500'))
        self.receta = ListaMateriales.objects.create(producto=self.kit, codigo='V1', cantidad_base=D('1'),
                                                      vigente_desde=FECHA - timedelta(days=60))
        self.receta.componentes.create(producto=self.insumo, cantidad=D('1'), merma=D('10'))
        VersionFabricacion.objects.create(producto=self.kit, codigo='V1', lista=self.receta, hoja=self.ruta,
                                          vigente_desde=FECHA - timedelta(days=60))

    def producir(self, producto, lista, cantidad, consumo=None, horas=None, **kw):
        o = OrdenProduccion.objects.create(producto=producto, lista=lista, cantidad=D(cantidad), fecha=FECHA,
                                           almacen_insumos=self.almacen, almacen_destino=self.almacen)
        servicios.explotar(o)
        servicios.confirmar(o, self.user)
        consumos = {c.pk: D(consumo) for c in o.consumos.all()} if consumo else {}
        h = {x.pk: D(horas) for x in o.horas.all()} if horas else {}
        servicios.terminar(o, self.user, D(kw.pop('producido', cantidad)), consumos, h, fecha=FECHA, **kw)
        o.refresh_from_db()
        return o

    def gasto_planta(self, mano_obra, fijo):
        a = Asiento.objects.create(fecha=FECHA, glosa='Planilla y depreciación de planta')
        cuentas = {c.codigo: c for c in CuentaContable.objects.filter(codigo__in=['6211', '6814', '4111'])}
        AsientoLinea.objects.create(asiento=a, cuenta=cuentas['6211'], debe=D(mano_obra), centro_costo=self.cc)
        AsientoLinea.objects.create(asiento=a, cuenta=cuentas['6814'], debe=D(fijo), centro_costo=self.cc)
        AsientoLinea.objects.create(asiento=a, cuenta=cuentas['4111'], haber=D(mano_obra) + D(fijo))


# ---------------------------------------------------------------- merma anormal
class MermaAnormalTest(BaseNIC2):
    def test_merma_anormal_va_a_gasto_y_no_al_producto(self):
        # por defecto: todo lo consumido ÷ todo lo producido (planificado 4, producido 2, consumido todo)
        o = self.producir(self.kit, self.receta, '4', consumo='4.4', producido='2')
        self.assertEqual((o.merma_anormal, o.costo_unitario), (0, ((D('44') + 30) / 2).quantize(D('0.0001'))))
        # si se indica, lo consumido sobre la receta va a gasto
        o = self.producir(self.kit, self.receta, '4', consumo='5', merma_a_gasto=True)  # permitido 4.4
        self.assertEqual(o.merma_anormal, D('6.00'))  # 0.6 × 10
        self.assertEqual(o.costo_materiales, D('50.00'))
        self.assertEqual(o.costo_unitario, ((D('50') - 6 + 30) / 4).quantize(D('0.0001')))
        v = {x.tipo: x.monto for x in o.variaciones.all()}
        self.assertEqual(v['MERMA_ANORMAL'], D('-6.00'))
        self.assertEqual(sum(v.values()), o.costo_total - o.costo_estandar_unit * o.cantidad_producida)
        # dentro de la merma normal no hay merma anormal
        self.assertEqual(self.producir(self.kit, self.receta, '2', consumo='2.2', merma_a_gasto=True).merma_anormal, 0)


# ---------------------------------------------------------------- liquidación del costo real
class LiquidacionCapacidadTest(BaseNIC2):
    def test_liquidacion_con_capacidad_ociosa_inventario_y_costo_de_ventas(self):
        o = self.producir(self.kit, self.receta, '4')  # 2 h: absorbe 20 de mano de obra y 10 de CIF
        self.kit.mover_stock(D('-1'), 'Venta de prueba', fecha=FECHA, almacen=self.almacen, origen='VENTA')
        costo_antes = Producto.objects.get(pk=self.kit.pk).costo_promedio
        self.gasto_planta('100', '40')
        liq = liquidacion.liquidar(PERIODO, self.user)
        c = liq.centros.get(centro_costo=self.cc)
        # 2 h de 4 normales: solo la mitad del CIF fijo va al producto; la otra mitad es capacidad ociosa
        self.assertEqual((c.mo_real, c.fijo_real, c.fijo_inventariable, c.uso_capacidad),
                         (D('100'), D('40'), D('20'), D('50.0')))
        self.assertEqual((c.inventariable, c.absorbido, c.diferencia, c.gasto_periodo),
                         (D('120'), D('30'), D('90'), D('20')))
        lp = liq.productos.get(producto=self.kit)
        self.assertEqual((lp.en_stock, lp.a_inventario, lp.a_costo), (D('3'), D('67.50'), D('22.50')))
        self.assertEqual(Producto.objects.get(pk=self.kit.pk).costo_promedio, costo_antes + D('22.5'))
        o.refresh_from_db()
        self.assertEqual(o.ajuste_liquidacion, D('90'))
        self.assertEqual(o.costo_real_final, o.costo_total + 90)
        inventario = Asiento.objects.get(periodo=PERIODO, origen='INVENTARIO')
        lineas = inventario.lineas.filter(glosa__startswith='Liquidación de costo real')
        self.assertEqual(sum(l.debe for l in lineas), D('90'))
        self.assertEqual(sum(l.haber for l in lineas if l.cuenta.codigo.startswith('71')), D('90'))
        self.assertEqual(sum(l.debe for l in lineas if l.cuenta.codigo.startswith('69')), D('22.50'))
        for a in Asiento.objects.filter(periodo=PERIODO):
            self.assertTrue(a.cuadrado, f'{a} no cuadra')
        # volver a liquidar no duplica; anular devuelve todo a las tarifas
        liquidacion.liquidar(PERIODO, self.user)
        self.assertEqual(Producto.objects.get(pk=self.kit.pk).costo_promedio, costo_antes + D('22.5'))
        liquidacion.anular(PERIODO)
        self.assertEqual(Producto.objects.get(pk=self.kit.pk).costo_promedio, costo_antes)
        self.assertFalse(LiquidacionCosto.objects.exists())
        self.assertEqual(OrdenProduccion.objects.get(pk=o.pk).ajuste_liquidacion, 0)
        self.assertFalse(Asiento.objects.get(periodo=PERIODO, origen='INVENTARIO').lineas.filter(
            glosa__startswith='Liquidación').exists())


class LiquidacionMultinivelTest(BaseNIC2):
    def test_liquidacion_multinivel_pasa_del_semielaborado_al_producto(self):
        semi = Producto.objects.create(nombre='Semi NIC2', clase='SEMIELABORADO')
        r_semi = ListaMateriales.objects.create(producto=semi, codigo='S1', cantidad_base=D('1'),
                                               vigente_desde=FECHA - timedelta(days=60))
        r_semi.componentes.create(producto=self.insumo, cantidad=D('1'))
        VersionFabricacion.objects.create(producto=semi, codigo='S1', lista=r_semi, hoja=self.ruta,
                                          vigente_desde=FECHA - timedelta(days=60))
        final = Producto.objects.create(nombre='Final NIC2', clase='PRODUCTO_TERMINADO')
        r_final = ListaMateriales.objects.create(producto=final, codigo='F1', cantidad_base=D('1'),
                                                vigente_desde=FECHA - timedelta(days=60))
        r_final.componentes.create(producto=semi, cantidad=D('1'))
        VersionFabricacion.objects.create(producto=final, codigo='F1', lista=r_final,
                                          vigente_desde=FECHA - timedelta(days=60))
        self.puesto.horas_normales_mes = None
        self.puesto.save()
        self.producir(semi, r_semi, '2')            # 1 h: absorbe 10 + 5
        o_final = self.producir(final, r_final, '2')  # consume los 2 semielaborados
        self.gasto_planta('100', '0')
        liq = liquidacion.liquidar(PERIODO, self.user)
        lp_semi = liq.productos.get(producto=semi)
        self.assertEqual((lp_semi.en_stock, lp_semi.a_produccion, lp_semi.a_inventario), (D('0'), D('85'), D('0')))
        lp_final = liq.productos.get(producto=final)
        self.assertEqual(lp_final.a_inventario, D('85'))
        o_final.refresh_from_db()
        self.assertEqual(o_final.ajuste_liquidacion, D('85'))


class PeriodoEnCursoTest(BaseNIC2):
    def test_periodo_en_curso_no_se_liquida(self):
        with self.assertRaises(liquidacion.ErrorLiquidacion):
            liquidacion.liquidar(HOY.strftime('%Y%m'), self.user)


# ---------------------------------------------------------------- valor neto realizable
class ValorNetoRealizableTest(BaseNIC2):
    def test_vnr_provisiona_y_revierte_solo_en_libro_niif(self):
        merc = Producto.objects.create(nombre='Mercadería NIC2', clase='MERCADERIA', precio_venta=D('40'))
        merc.mover_stock(D('10'), 'Saldo de prueba', costo=D('50'), almacen=self.almacen, fecha=FECHA)
        sin_lista = next(f for f in liquidacion.calcular_vnr(FECHA, D('10')) if f['p'] == merc)
        self.assertEqual((sin_lista['vnr'], sin_lista['deterioro']), (None, 0))  # sin ventas: se revisa a mano
        filas = liquidacion.calcular_vnr(FECHA, D('10'), usar_lista=True)
        fila = next(f for f in filas if f['p'] == merc)
        self.assertEqual((fila['vnr'], fila['deterioro'], fila['ajuste']), (D('36'), D('140'), D('140')))
        prueba = liquidacion.registrar_vnr(FECHA, D('10'), usuario=self.user, usar_lista=True)
        a = Asiento.objects.get(origen='VNR', periodo=PERIODO)
        self.assertEqual(a.norma, 'NIIF')
        self.assertTrue(a.cuadrado)
        self.assertEqual(a.lineas.get(cuenta__codigo='6951').debe, D('140'))
        self.assertEqual(a.lineas.get(cuenta__codigo='2911').haber, D('140'))
        with self.assertRaises(liquidacion.ErrorLiquidacion):  # no dos pruebas en la misma fecha
            liquidacion.registrar_vnr(FECHA, D('10'))
        # el precio se recupera: se revierte lo provisionado
        Producto.objects.filter(pk=merc.pk).update(precio_venta=D('80'))
        fila = next(f for f in liquidacion.calcular_vnr(HOY, D('10'), usar_lista=True) if f['p'] == merc)
        self.assertEqual((fila['deterioro'], fila['ajuste']), (D('0'), D('-140')))
        # anular la prueba retira su asiento
        liquidacion.anular_vnr(prueba)
        self.assertFalse(PruebaVNR.objects.exists())
        self.assertFalse(Asiento.objects.filter(origen='VNR').exists())


# ---------------------------------------------------------------- pantallas y permisos
class PantallasNIC2Test(BaseNIC2):
    def test_pantallas_y_permiso_explicito(self):
        self.assertIn('costos.liquidar', EXPLICITAS)
        self.producir(self.kit, self.receta, '2')
        self.gasto_planta('50', '10')
        r = self.client.post(reverse('costos:liquidacion'), {'periodo': PERIODO, 'accion': 'liquidar'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(LiquidacionCosto.objects.filter(periodo=PERIODO).exists())
        self.assertContains(self.client.get(reverse('costos:liquidacion') + f'?periodo={FECHA:%Y-%m}'),
                            'Planta NIC2')
        self.assertEqual(self.client.get(reverse('costos:liquidacion') + f'?periodo={FECHA:%Y-%m}&formato=excel')
                         .status_code, 200)
        self.assertContains(self.client.get(reverse('costos:vnr') + f'?fecha={FECHA}&gasto_venta=5'), 'Vista previa')
        self.assertContains(self.client.get(reverse('costos:comportamiento')), 'CIF fijo')
        self.client.post(reverse('costos:comportamiento'), {'prefijo': '6343', 'tipo': 'VARIABLE'})
        self.assertEqual(liquidacion.comportamiento('634301'), 'VARIABLE')
        # sin la acción explícita no se liquida
        operario = User.objects.create_user('op', password='x')
        self.client.force_login(operario)
        r = self.client.post(reverse('costos:liquidacion'), {'periodo': PERIODO, 'accion': 'anular'})
        self.assertTrue(LiquidacionCosto.objects.filter(periodo=PERIODO).exists())
