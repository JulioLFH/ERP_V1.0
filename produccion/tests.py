"""Pruebas de manufactura y costos (v1.11): receta, costo estándar, orden de producción con costeo por absorción
(materiales + mano de obra + CIF), anulación, contabilidad (61/71), requerimiento, rentabilidad y permisos."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import Asiento
from core.models import Almacen, Kardex, Producto
from core.modulos import GRUPO_COSTOS, GRUPOS
from ventas.models import Venta

from . import servicios
from .costos import rentabilidad
from .models import CentroTrabajo, HojaRuta, ListaMateriales, OrdenProduccion, VersionFabricacion

D = Decimal
HOY = date.today()


class ManufacturaTest(TestCase):
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
        self.insumo = Producto.objects.get(codigo='P003')
        self.costo_insumo = self.insumo.costo_promedio
        self.kit = Producto.objects.create(nombre='Kit armado', clase='PRODUCTO_TERMINADO', precio_venta=D('500'))
        self.centro = CentroTrabajo.objects.create(codigo='ENS', nombre='Ensamble', costo_hora_mo=D('10'),
                                                   costo_hora_cif=D('5'))
        # rinde 2 kits: 2 insumos con 10 % de merma; ensamble de media hora por kit (1 hora por lote de 2)
        self.receta = ListaMateriales.objects.create(producto=self.kit, codigo='V1', cantidad_base=D('2'))
        self.receta.componentes.create(producto=self.insumo, cantidad=D('2'), merma=D('10'))
        self.ruta = HojaRuta.objects.create(codigo='R-KIT', nombre='Ruta kit', estado='APROBADA')
        self.ruta.operaciones.create(secuencia=10, centro=self.centro, descripcion='Armado', horas_unidad=D('0.5'))
        self.version = VersionFabricacion.objects.create(producto=self.kit, codigo='V1', lista=self.receta,
                                                         hoja=self.ruta)

    def orden(self, cantidad='4'):
        o = OrdenProduccion.objects.create(producto=self.kit, lista=self.receta, cantidad=D(cantidad), fecha=HOY,
                                           almacen_insumos=self.almacen, almacen_destino=self.almacen,
                                           creado_por=self.user)
        servicios.explotar(o)
        return o

    def terminar(self, o, cantidad='4', consumo=None, horas=None):
        c, h = o.consumos.get(), o.horas.get()
        return servicios.terminar(o, self.user, D(cantidad), {c.pk: D(consumo)} if consumo else {},
                                  {h.pk: D(horas)} if horas else {})

    # ---------------------------------------------------------------- costo estándar
    def test_hoja_de_costos_con_merma_y_conversion(self):
        hoja = servicios.hoja_costos(self.receta)
        materiales = (D('2.2') * self.costo_insumo).quantize(D('0.01'))
        self.assertEqual(hoja['tot_materiales'], materiales)
        self.assertEqual((hoja['tot_mano_obra'], hoja['tot_cif']), (D('10'), D('5')))
        self.assertEqual(hoja['unitario'], ((materiales + 15) / 2).quantize(D('0.0001')))
        # a otra cantidad se escala en proporción
        self.assertEqual(servicios.hoja_costos(self.receta, cantidad=D('4'))['tot_mano_obra'], D('20'))

    def test_costo_de_semielaborado_sin_compras_usa_su_receta(self):
        semi = Producto.objects.create(nombre='Subensamble', clase='SEMIELABORADO')
        r = ListaMateriales.objects.create(producto=semi, codigo='V1', cantidad_base=D('1'))
        r.componentes.create(producto=self.insumo, cantidad=D('1'))
        self.receta.componentes.create(producto=semi, cantidad=D('1'))
        hoja = servicios.hoja_costos(self.receta)
        fila = next(m for m in hoja['materiales'] if m['producto'] == semi)
        self.assertEqual(fila['costo'], self.costo_insumo)
        # una receta circular no se cuelga
        r.componentes.create(producto=self.kit, cantidad=D('1'))
        servicios.hoja_costos(self.receta)

    # ---------------------------------------------------------------- orden de producción
    def test_flujo_completo_costea_por_absorcion(self):
        o = self.orden('4')
        self.assertEqual(o.consumos.get().cantidad_plan, D('4.4'))
        self.assertEqual(o.horas.get().horas_plan, D('2'))
        servicios.confirmar(o, self.user)
        self.assertTrue(o.numero.startswith('OP01-'))
        self.assertEqual(o.costo_estandar_unit, servicios.hoja_costos(self.receta)['unitario'])
        servicios.iniciar(o)
        stock_antes = Producto.objects.get(pk=self.insumo.pk).stock
        self.terminar(o, cantidad='4', consumo='5', horas='3')  # se consumió más y tomó más horas
        o.refresh_from_db()
        self.kit.refresh_from_db()
        materiales = (D('5') * self.costo_insumo).quantize(D('0.01'))
        self.assertEqual(o.estado, 'TERMINADA')
        self.assertEqual((o.costo_materiales, o.costo_mano_obra, o.costo_cif), (materiales, D('30'), D('15')))
        # todo lo consumido ÷ todo lo producido (la merma anormal a gasto solo si se indica al terminar)
        self.assertEqual(o.merma_anormal, 0)
        self.assertEqual(o.costo_unitario, ((D('5') * self.costo_insumo + 45) / 4).quantize(D('0.0001')))
        self.assertEqual((self.kit.stock, self.kit.costo_promedio), (D('4'), o.costo_unitario))
        self.assertEqual(Producto.objects.get(pk=self.insumo.pk).stock, stock_antes - 5)
        self.assertEqual(o.operacion.estado, 'CONFIRMADO')
        self.assertEqual(set(Kardex.objects.filter(concepto='MANUF').values_list('codigo_sunat', flat=True)),
                         {'10', '19'})
        total = next(v for v in servicios.variaciones(o) if v['concepto'] == 'Total')
        self.assertGreater(total['variacion'], 0)  # desfavorable

    def test_sin_stock_no_termina_y_no_mueve_nada(self):
        o = self.orden('4')
        servicios.confirmar(o, self.user)
        with self.assertRaises(servicios.ErrorProduccion):
            self.terminar(o, consumo='999999')
        o.refresh_from_db()
        self.assertEqual(o.estado, 'CONFIRMADA')
        self.assertEqual(o.consumos.get().cantidad_real, D('4.4'))
        self.assertFalse(Kardex.objects.filter(concepto='MANUF').exists())

    def test_anular_terminada_revierte_el_almacen_con_motivo(self):
        o = self.orden('2')
        servicios.confirmar(o, self.user)
        stock_insumo = Producto.objects.get(pk=self.insumo.pk).stock
        self.terminar(o, cantidad='2')
        with self.assertRaises(servicios.ErrorProduccion):
            servicios.anular(o, self.user, 'corto')
        servicios.anular(o, self.user, 'Producción registrada por error')
        o.refresh_from_db()
        self.assertEqual(o.estado, 'ANULADA')
        self.assertEqual(o.operacion.estado, 'ANULADO')
        self.assertEqual(Producto.objects.get(pk=self.kit.pk).stock, 0)
        self.assertEqual(Producto.objects.get(pk=self.insumo.pk).stock, stock_insumo)

    def test_contabilidad_consumo_61_y_produccion_71(self):
        o = self.orden('2')
        servicios.confirmar(o, self.user)
        self.terminar(o, cantidad='2')
        o.refresh_from_db()
        periodo = HOY.strftime('%Y%m')
        centralizar_periodo(periodo)
        a = Asiento.objects.get(periodo=periodo, origen='INVENTARIO')
        produccion = a.lineas.filter(cuenta__codigo__startswith='71')
        consumo = a.lineas.filter(cuenta__codigo__startswith='61')
        self.assertEqual(sum(l.haber - l.debe for l in produccion), o.costo_total)
        self.assertEqual(sum(l.debe - l.haber for l in consumo), o.costo_materiales)
        for asiento in Asiento.objects.all():
            self.assertTrue(asiento.cuadrado, f'{asiento} no cuadra')

    def test_requerimiento_de_materiales(self):
        o = self.orden('4')
        servicios.confirmar(o, self.user)
        fila = next(f for f in servicios.requerimientos() if f['p'] == self.insumo)
        self.assertEqual(fila['requerido'], D('4.4'))
        self.assertIn(o.numero, fila['ordenes'])

    def test_receta_usada_no_se_edita(self):
        o = self.orden('2')
        servicios.confirmar(o, self.user)
        r = self.client.get(reverse('manufactura:lista_editar', args=[self.receta.pk]))
        self.assertRedirects(r, reverse('manufactura:lista', args=[self.receta.pk]))
        r = self.client.post(reverse('manufactura:lista_version', args=[self.receta.pk]))
        nueva = ListaMateriales.objects.exclude(pk=self.receta.pk).get(producto=self.kit)
        self.assertEqual((nueva.codigo, nueva.activa, nueva.componentes.count()), ('V2', False, 1))

    # ---------------------------------------------------------------- pantallas
    def test_pantallas_responden(self):
        o = self.orden('2')
        servicios.confirmar(o, self.user)
        self.terminar(o, cantidad='2')
        urls = [reverse('manufactura:ordenes'), reverse('manufactura:orden_nueva'),
                reverse('manufactura:orden', args=[o.pk]), reverse('manufactura:requerimiento'),
                reverse('manufactura:listas'), reverse('manufactura:lista_nueva'),
                reverse('manufactura:lista', args=[self.receta.pk]), reverse('manufactura:centros'),
                reverse('costos:estandar'), reverse('costos:hoja', args=[self.receta.pk]),
                reverse('costos:real_vs_estandar') + f'?desde={HOY}&hasta={HOY}',
                reverse('costos:rentabilidad')]
        for agrupar in ('cliente', 'vendedor', 'mes'):
            urls.append(reverse('costos:rentabilidad') + f'?agrupar={agrupar}')
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get(reverse('costos:rentabilidad') + '?formato=excel').status_code, 200)

    def test_crear_receta_ruta_version_y_orden_desde_formularios(self):
        r = self.client.post(reverse('manufactura:lista_nueva'), {
            'producto': self.kit.pk, 'codigo': 'V9', 'cantidad_base': '1', 'estado': 'APROBADA',
            'vigente_desde': HOY.isoformat(), 'vigente_hasta': '', 'lote_min': '', 'lote_max': '', 'observaciones': '',
            'comp-TOTAL_FORMS': '1', 'comp-INITIAL_FORMS': '0', 'comp-MIN_NUM_FORMS': '1', 'comp-MAX_NUM_FORMS': '1000',
            'comp-0-producto': self.insumo.pk, 'comp-0-cantidad': '1', 'comp-0-merma': '0', 'comp-0-operacion': '10',
            'comp-0-almacen': '',
            'sub-TOTAL_FORMS': '0', 'sub-INITIAL_FORMS': '0', 'sub-MIN_NUM_FORMS': '0', 'sub-MAX_NUM_FORMS': '1000'})
        nueva = ListaMateriales.objects.get(codigo='V9')
        self.assertRedirects(r, reverse('manufactura:lista', args=[nueva.pk]))
        r = self.client.post(reverse('manufactura:hoja_nueva'), {
            'codigo': 'R-GRANDE', 'nombre': 'Ruta lotes grandes', 'estado': 'APROBADA', 'observaciones': '',
            'oper-TOTAL_FORMS': '2', 'oper-INITIAL_FORMS': '0', 'oper-MIN_NUM_FORMS': '1', 'oper-MAX_NUM_FORMS': '1000',
            'oper-0-secuencia': '10', 'oper-0-centro': self.centro.pk, 'oper-0-descripcion': 'Preparar',
            'oper-0-horas_preparacion': '1', 'oper-0-horas_unidad': '0', 'oper-0-horas_espera': '0',
            'oper-0-maquinistas': '1', 'oper-0-ayudantes': '0',
            'oper-1-secuencia': '20', 'oper-1-centro': self.centro.pk, 'oper-1-descripcion': 'Armar',
            'oper-1-horas_preparacion': '0', 'oper-1-horas_unidad': '0.25', 'oper-1-horas_espera': '2',
            'oper-1-maquinistas': '1', 'oper-1-ayudantes': '1'})
        self.assertRedirects(r, reverse('manufactura:hojas'))
        ruta = HojaRuta.objects.get(codigo='R-GRANDE')
        r = self.client.post(reverse('manufactura:version_nueva'), {
            'producto': self.kit.pk, 'codigo': 'G', 'descripcion': 'Lotes de 10 a más', 'lista': nueva.pk,
            'hoja': ruta.pk, 'lote_min': '10', 'lote_max': '', 'lote_costeo': '20', 'vigente_desde': HOY.isoformat(),
            'vigente_hasta': '', 'dias_fabricacion': '2', 'activa': 'on'})
        self.assertRedirects(r, reverse('manufactura:versiones'))
        # la orden elige sola la versión según la cantidad: 12 unidades -> versión G (lote desde 10)
        r = self.client.post(reverse('manufactura:orden_nueva'), {
            'producto': self.kit.pk, 'version': '', 'cantidad': '12', 'fecha': HOY.isoformat(),
            'almacen_insumos': self.almacen.pk, 'almacen_destino': self.almacen.pk, 'glosa': ''})
        o = OrdenProduccion.objects.get(lista=nueva)
        self.assertRedirects(r, reverse('manufactura:orden', args=[o.pk]))
        self.assertEqual(o.version.codigo, 'G')
        horas = list(o.horas.order_by('secuencia').values_list('horas_plan', flat=True))
        self.assertEqual(horas, [D('1'), D('3')])  # preparación 1 h; ejecución 0.25 × 12
        # 4 unidades -> versión V1
        self.client.post(reverse('manufactura:orden_nueva'), {
            'producto': self.kit.pk, 'version': '', 'cantidad': '4', 'fecha': HOY.isoformat(),
            'almacen_insumos': self.almacen.pk, 'almacen_destino': self.almacen.pk, 'glosa': ''})
        self.assertEqual(OrdenProduccion.objects.filter(cantidad=D('4')).last().version, self.version)
        o = OrdenProduccion.objects.get(lista=nueva)
        self.client.post(reverse('manufactura:orden_confirmar', args=[o.pk]))
        c = o.consumos.get()
        self.client.post(reverse('manufactura:orden_terminar', args=[o.pk]),
                         {f'consumo_{c.pk}': '12', 'cantidad_producida': '12'})
        o.refresh_from_db()
        self.assertEqual(o.estado, 'TERMINADA')

    # ---------------------------------------------------------------- costos y permisos
    def test_rentabilidad_por_producto(self):
        venta = Venta.objects.filter(estado='REGISTRADO', es_saldo_inicial=False).exclude(
            tipo_comprobante__in=['07', '08']).first()
        filas, total = rentabilidad(venta.fecha_emision, venta.fecha_emision, 'producto')
        self.assertTrue(filas)
        self.assertEqual(total['margen'], total['ventas'] - total['costo'])

    def test_costos_exige_permiso(self):
        usuario = User.objects.create_user('jefe', password='x')
        usuario.groups.add(Group.objects.get_or_create(name=GRUPOS['costos'])[0],
                           Group.objects.get_or_create(name=GRUPOS['manufactura'])[0])
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse('costos:estandar')).status_code, 403)
        self.assertEqual(self.client.get(reverse('manufactura:ordenes')).status_code, 200)
        usuario.groups.add(Group.objects.get_or_create(name=GRUPO_COSTOS)[0])
        self.assertEqual(self.client.get(reverse('costos:estandar')).status_code, 200)
        # sin el módulo no entra
        otro = User.objects.create_user('otro', password='x')
        self.client.force_login(otro)
        self.assertNotEqual(self.client.get(reverse('manufactura:ordenes')).status_code, 200)
