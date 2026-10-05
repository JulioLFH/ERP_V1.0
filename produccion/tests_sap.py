"""Manufactura tipo SAP (v1.16): MRP multinivel (33), estándar fijado por periodo (34), variaciones por tipo,
absorción de costos de planta (35), hojas de ruta y versiones (42-45) y capacidad (43)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra, CompraItem
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import CentroCosto
from core.models import Almacen, Producto, Tercero

from . import servicios
from .models import (CentroTrabajo, CostoEstandar, HojaRuta, ListaMateriales, OrdenProduccion, PlanDemanda,
                     VersionFabricacion)

D = Decimal
HOY = date.today()
PERIODO = HOY.strftime('%Y%m')


class ManufacturaSAPTest(TestCase):
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
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        mp = dict(clase='MATERIA_PRIMA', proveedor=prov, tiempo_entrega=3)
        self.harina = Producto.objects.create(nombre='Harina', unidad='KGM', precio_compra=D('3'), **mp)
        self.mantequilla = Producto.objects.create(nombre='Mantequilla', unidad='KGM', precio_compra=D('20'), **mp)
        self.empaque = Producto.objects.create(nombre='Empaque', precio_compra=D('0.10'), **mp)
        self.masa = Producto.objects.create(nombre='Masa', unidad='KGM', clase='SEMIELABORADO')
        self.galleta = Producto.objects.create(nombre='Galleta paquete', clase='PRODUCTO_TERMINADO',
                                               precio_venta=D('8'))
        self.cc_planta = CentroCosto.objects.create(codigo='PL', nombre='Planta', tipo='PRODUCCION')
        self.horno = CentroTrabajo.objects.create(codigo='HOR', nombre='Horno', costo_hora_mo=D('20'),
                                                  costo_hora_cif=D('30'), centro_costo=self.cc_planta,
                                                  horas_turno=D('8'), turnos=1, dias_laborables='12345')
        # masa: 1 kg = 0.6 kg harina + 0.2 kg mantequilla; mezclado 0.01 h por kg
        lm = ListaMateriales.objects.create(producto=self.masa, codigo='M1', cantidad_base=D('1'))
        lm.componentes.create(producto=self.harina, cantidad=D('0.6'))
        lm.componentes.create(producto=self.mantequilla, cantidad=D('0.2'))
        rm = HojaRuta.objects.create(codigo='R-MASA', nombre='Mezclado', estado='APROBADA')
        rm.operaciones.create(secuencia=10, centro=self.horno, descripcion='Mezclar', horas_unidad=D('0.01'))
        self.v_masa = VersionFabricacion.objects.create(producto=self.masa, codigo='1', lista=lm, hoja=rm,
                                                        lote_costeo=D('100'), dias_fabricacion=1)
        # galleta: 1 paquete = 0.5 kg de masa + 1 empaque; horneado: 0.5 h de preparación + 0.01 h por paquete
        lg = ListaMateriales.objects.create(producto=self.galleta, codigo='G1', cantidad_base=D('1'))
        lg.componentes.create(producto=self.masa, cantidad=D('0.5'))
        lg.componentes.create(producto=self.empaque, cantidad=D('1'))
        rg = HojaRuta.objects.create(codigo='R-GAL', nombre='Horneado', estado='APROBADA')
        rg.operaciones.create(secuencia=10, centro=self.horno, descripcion='Hornear', horas_preparacion=D('0.5'),
                              horas_unidad=D('0.01'))
        self.v_galleta = VersionFabricacion.objects.create(producto=self.galleta, codigo='1', lista=lg, hoja=rg,
                                                           lote_costeo=D('100'), dias_fabricacion=2)

    # ---------------------------------------------------------------- 33 MRP multinivel
    def test_mrp_explota_todos_los_niveles(self):
        PlanDemanda.objects.create(producto=self.galleta, cantidad=D('2000'), fecha=HOY + timedelta(days=10))
        corrida = servicios.ejecutar_mrp(HOY + timedelta(days=30), self.user)
        props = {(p.producto.nombre, p.tipo): p for p in corrida.propuestas.all()}
        self.assertEqual(props[('Galleta paquete', 'PRODUCIR')].cantidad, D('2000'))
        # la masa se FABRICA (antes se proponía comprarla)
        self.assertEqual(props[('Masa', 'PRODUCIR')].cantidad, D('1000'))
        self.assertNotIn(('Masa', 'COMPRAR'), props)
        self.assertEqual(props[('Harina', 'COMPRAR')].cantidad, D('600'))
        self.assertEqual(props[('Mantequilla', 'COMPRAR')].cantidad, D('200'))
        self.assertEqual(props[('Empaque', 'COMPRAR')].cantidad, D('2000'))
        # fechas: galleta inicia 2 días antes de la necesidad; la masa 1 día antes de que inicie la galleta
        galleta, masa = props[('Galleta paquete', 'PRODUCIR')], props[('Masa', 'PRODUCIR')]
        self.assertEqual(galleta.fecha_inicio, HOY + timedelta(days=8))
        self.assertEqual(masa.fecha_necesidad, galleta.fecha_inicio)
        self.assertEqual(masa.fecha_inicio, HOY + timedelta(days=7))
        self.assertEqual((galleta.nivel, masa.nivel, props[('Harina', 'COMPRAR')].nivel), (0, 1, 2))
        # convertir: OP en borrador y OC agrupadas por proveedor
        op = servicios.convertir_en_orden(galleta, self.user)
        self.assertEqual((op.estado, op.version, op.cantidad), ('BORRADOR', self.v_galleta, D('2000')))
        compras = [p for p in corrida.propuestas.all() if p.tipo == 'COMPRAR']
        ocs = servicios.convertir_en_compras(compras, self.user, self.cc_planta)
        self.assertEqual(len(ocs), 1)
        self.assertEqual(ocs[0].items.count(), 3)
        # una nueva corrida descuenta lo ya pedido y la OP abierta
        corrida2 = servicios.ejecutar_mrp(HOY + timedelta(days=30), self.user)
        nombres = {(p.producto.nombre, p.tipo) for p in corrida2.propuestas.all()}
        self.assertNotIn(('Galleta paquete', 'PRODUCIR'), nombres)
        self.assertNotIn(('Harina', 'COMPRAR'), nombres)

    # ---------------------------------------------------------------- 34 estándar fijo y variaciones
    def liberar(self):
        servicios.calcular_estandar(PERIODO, self.user)
        servicios.liberar_estandar(PERIODO, self.user)
        return CostoEstandar.objects.get(producto=self.galleta, periodo=PERIODO)

    def test_estandar_por_periodo_multinivel_y_fijo(self):
        ce = self.liberar()
        masa = CostoEstandar.objects.get(producto=self.masa, periodo=PERIODO)
        # masa: 0.6×3 + 0.2×20 = 5.80 de materiales + 0.01 h × 50 = 0.50 -> 6.30 por kg
        self.assertEqual(masa.unitario, D('6.3000'))
        # galleta: 0.5 kg masa × 6.30 + empaque 0.10 = 3.25; horno (0.5 + 1) h × 50 / 100 = 0.75 -> 4.00
        self.assertEqual(ce.unitario, D('4.0000'))
        self.assertEqual(ce.estado, 'LIBERADO')
        # sube el precio de la harina: las órdenes del periodo siguen con el estándar liberado
        Producto.objects.filter(pk=self.harina.pk).update(costo_promedio=D('9'))
        o = OrdenProduccion.objects.create(producto=self.galleta, cantidad=D('100'), fecha=HOY,
                                           almacen_insumos=self.almacen, almacen_destino=self.almacen,
                                           lista=self.v_galleta.lista)
        servicios.explotar(o)
        servicios.confirmar(o, self.user)
        self.assertEqual((o.costo_estandar_unit, o.estandar), (D('4.0000'), ce))
        # recalcular no toca lo liberado
        servicios.calcular_estandar(PERIODO, self.user)
        self.assertEqual(CostoEstandar.objects.get(pk=ce.pk).unitario, D('4.0000'))

    def test_variaciones_por_tipo_suman_la_diferencia(self):
        self.liberar()
        for p, cant, costo in ((self.masa, '60', '6.30'), (self.empaque, '120', '0.12')):
            p.mover_stock(D(cant), 'Saldo de prueba', costo=D(costo), almacen=self.almacen)
        o = OrdenProduccion.objects.create(producto=self.galleta, cantidad=D('100'), fecha=HOY,
                                           almacen_insumos=self.almacen, almacen_destino=self.almacen,
                                           lista=self.v_galleta.lista)
        servicios.explotar(o)
        servicios.confirmar(o, self.user)
        masa = o.consumos.get(producto=self.masa)
        h = o.horas.get()
        # se usó 55 kg de masa (estándar 50), empaque a 0.12 (estándar 0.10) y 2 h de horno (estándar 1.5)
        servicios.terminar(o, self.user, D('100'), {masa.pk: D('55')}, {h.pk: D('2')})
        o.refresh_from_db()
        v = {x.tipo: x.monto for x in o.variaciones.all()}
        self.assertEqual(v['CANTIDAD_MAT'], D('31.50'))   # 5 kg × 6.30
        self.assertEqual(v['PRECIO_MAT'], D('2.00'))      # 100 empaques × 0.02
        self.assertEqual(v['EFICIENCIA_MO'], D('10.00'))  # 0.5 h × 20
        self.assertEqual(v['EFICIENCIA_CIF'], D('15.00'))  # 0.5 h × 30
        self.assertEqual(sum(v.values()), o.costo_total - o.costo_estandar_unit * o.cantidad_producida)
        r = self.client.get(reverse('costos:real_vs_estandar') + f'?desde={HOY}&hasta={HOY}')
        self.assertContains(r, 'Cantidad de materiales')

    # ---------------------------------------------------------------- 35 absorción
    def test_absorcion_contra_gasto_real_de_planta(self):
        for p, cant, costo in ((self.harina, '10', '3'), (self.mantequilla, '5', '20')):
            p.mover_stock(D(cant), 'Saldo de prueba', costo=D(costo), almacen=self.almacen)
        o = OrdenProduccion.objects.create(producto=self.masa, cantidad=D('10'), fecha=HOY, lista=self.v_masa.lista,
                                           almacen_insumos=self.almacen, almacen_destino=self.almacen)
        servicios.explotar(o)
        servicios.confirmar(o, self.user)
        servicios.terminar(o, self.user, D('10'), {}, {o.horas.get().pk: D('6')})  # 6 h × 50 = 300 absorbidos
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        c = Compra.objects.create(tercero=prov, serie='F001', numero='9480', fecha_emision=HOY,
                                  clasificacion='SERVICIO', centro_costo=self.cc_planta, ingresar_almacen=False)
        CompraItem.objects.create(documento=c, descripcion='Energía', cantidad=1, precio_unitario=D('480'))
        c.calcular_totales()
        c.save()
        centralizar_periodo(PERIODO)
        filas, _ = servicios.absorcion(PERIODO)
        fila = next(f for f in filas if f['centro'] == self.cc_planta)
        self.assertEqual((fila['real'], fila['absorbido'], fila['diferencia']), (D('480'), D('300'), D('180')))
        self.assertContains(self.client.get(reverse('costos:absorcion')), 'Planta')

    # ---------------------------------------------------------------- 43 capacidad y pantallas
    def test_capacidad_y_pantallas(self):
        lunes = HOY - timedelta(days=HOY.weekday())
        self.assertEqual(self.horno.capacidad_entre(lunes, lunes + timedelta(days=6)), D('40'))  # 5 días × 8 h
        self.assertFalse(self.horno.laborable(lunes + timedelta(days=5)))  # sábado
        self.liberar()
        for url in [reverse('manufactura:mrp'), reverse('manufactura:capacidad'), reverse('manufactura:hojas'),
                    reverse('manufactura:versiones'), reverse('manufactura:version_editar', args=[self.v_masa.pk]),
                    reverse('manufactura:hoja_editar', args=[self.v_masa.hoja_id]), reverse('manufactura:centros'),
                    reverse('manufactura:centro_editar', args=[self.horno.pk]), reverse('costos:estandar'),
                    reverse('costos:estandar') + '?formato=excel', reverse('costos:absorcion'),
                    reverse('manufactura:lista', args=[self.v_galleta.lista_id])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
