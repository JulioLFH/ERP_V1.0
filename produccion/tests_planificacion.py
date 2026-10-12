"""Tipo de planificación por producto (78), estrategia MTS / MTO (79) y carga masiva del plan de demanda (80)."""
import io
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from core.models import Producto, Tercero
from ventas.models import Cotizacion

from . import servicios
from .models import ListaMateriales, PlanDemanda, VersionFabricacion

D = Decimal
HOY = date.today()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        Producto.objects.filter(clase='SUMINISTRO').update(tipo_planificacion='NINGUNA')  # aislar del demo

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        self.cliente = Tercero.objects.filter(tipo__in=['CLIENTE', 'AMBOS']).first()

    def mrp(self):
        corrida = servicios.ejecutar_mrp(HOY + timedelta(days=30), self.user)
        return list(corrida.propuestas.select_related('producto'))

    def terminado(self, nombre, **extra):
        insumo = Producto.objects.create(nombre=f'Insumo de {nombre}', clase='MATERIA_PRIMA', proveedor=self.prov)
        pt = Producto.objects.create(nombre=nombre, clase='PRODUCTO_TERMINADO', precio_venta=D('10'), **extra)
        lm = ListaMateriales.objects.create(producto=pt, codigo='L1', cantidad_base=D('1'))
        lm.componentes.create(producto=insumo, cantidad=D('1'))
        VersionFabricacion.objects.create(producto=pt, codigo='1', lista=lm, lote_costeo=D('100'),
                                          lote_min=D('500'))
        return pt

    def pedido(self, producto, cantidad):
        ped = Cotizacion.objects.create(tipo='PED', numero=f'PED-{Cotizacion.objects.count() + 1}',
                                        tercero=self.cliente)
        ped.items.create(producto=producto, descripcion=producto.nombre, cantidad=D(cantidad),
                         precio_unitario=D('10'))
        return ped


class SuministroPorReordenTest(Base):
    def test_suministro_nuevo_queda_por_reorden_y_se_propone_fuera_del_mrp(self):
        guantes = Producto.objects.create(nombre='Guantes', clase='SUMINISTRO', proveedor=self.prov,
                                          punto_reorden=D('20'), stock_maximo=D('100'), lote_compra=D('50'))
        self.assertEqual(guantes.tipo_planificacion, 'REORDEN')
        Producto.objects.filter(pk=guantes.pk).update(stock=D('15'))
        # aunque haya plan de demanda, el MRP no lo planifica: solo el punto de reorden
        PlanDemanda.objects.create(producto=guantes, cantidad=D('1000'), fecha=HOY + timedelta(days=5))
        props = [p for p in self.mrp() if p.producto_id == guantes.pk]
        self.assertEqual(len(props), 1)
        self.assertEqual((props[0].tipo, props[0].cantidad), ('COMPRAR', D('85')))  # hasta el máximo
        self.assertIn('Punto de reorden', props[0].origen)
        # sobre el punto de reorden no se propone nada
        Producto.objects.filter(pk=guantes.pk).update(stock=D('30'))
        self.assertFalse([p for p in self.mrp() if p.producto_id == guantes.pk])


class SinPlanificarTest(Base):
    def test_sin_planificar_no_se_propone(self):
        pt = self.terminado('Mermelada', tipo_planificacion='NINGUNA')
        self.pedido(pt, '300')
        self.assertFalse([p for p in self.mrp() if p.producto_id == pt.pk])


class EstrategiaMTOTest(Base):
    def test_contra_pedido_ignora_plan_y_stock_libre(self):
        pt = self.terminado('Torta personalizada', estrategia='MTO', stock_minimo=D('10'))
        Producto.objects.filter(pk=pt.pk).update(stock=D('1000'))
        PlanDemanda.objects.create(producto=pt, cantidad=D('5000'), fecha=HOY + timedelta(days=5))
        self.pedido(pt, '30')
        self.pedido(pt, '20')
        props = sorted((p.cantidad for p in self.mrp() if p.producto_id == pt.pk and p.tipo == 'PRODUCIR'))
        self.assertEqual(props, [D('20'), D('30')])  # una orden por pedido, sin lote mínimo ni stock libre


class EstrategiaMTSTest(Base):
    def test_para_stock_usa_plan_y_stock(self):
        pt = self.terminado('Galleta')
        Producto.objects.filter(pk=pt.pk).update(stock=D('100'))
        PlanDemanda.objects.create(producto=pt, cantidad=D('300'), fecha=HOY + timedelta(days=5))
        props = [p for p in self.mrp() if p.producto_id == pt.pk and p.tipo == 'PRODUCIR']
        self.assertEqual([p.cantidad for p in props], [D('500')])  # 200 de faltante, lote mínimo 500


def excel(filas):
    wb = Workbook()
    for f in filas:
        wb.active.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile('plan.xlsx', buf.getvalue())


class CargaPlanDemandaTest(Base):
    def test_carga_masiva_del_plan(self):
        pt = self.terminado('Panetón')
        url = reverse('carga_masiva')
        self.assertContains(self.client.get(reverse('manufactura:mrp')), '?tipo=plan_demanda')
        filas = [['Producto (código)', 'Cantidad', 'Fecha', 'Nota'],
                 [pt.codigo, 500, '30/11/2026', 'Campaña navidad'],
                 [pt.codigo, 800, '15/12/2026', '']]
        r = self.client.post(url, {'tipo': 'plan_demanda', 'accion': 'validar', 'archivo': excel(filas)})
        self.assertNotContains(r, 'con error')
        self.client.post(url, {'tipo': 'plan_demanda', 'accion': 'confirmar'})
        self.assertEqual(PlanDemanda.objects.filter(producto=pt).count(), 2)
        self.assertEqual(PlanDemanda.objects.get(producto=pt, fecha=date(2026, 11, 30)).nota, 'Campaña navidad')
        # la misma fecha otra vez: error sin "actualizar"; con "actualizar" reemplaza la cantidad
        filas = [['producto', 'cantidad', 'fecha'], [pt.codigo, 650, '30/11/2026'], ['NOEXISTE', 1, '30/11/2026']]
        r = self.client.post(url, {'tipo': 'plan_demanda', 'accion': 'validar', 'archivo': excel(filas)})
        self.assertContains(r, 'ya tiene plan')
        self.assertContains(r, 'NOEXISTE no existe')
        r = self.client.post(url, {'tipo': 'plan_demanda', 'accion': 'validar', 'actualizar': 'on',
                                   'archivo': excel(filas[:2])})
        self.assertNotContains(r, 'con error')
        self.client.post(url, {'tipo': 'plan_demanda', 'accion': 'confirmar'})
        self.assertEqual(PlanDemanda.objects.get(producto=pt, fecha=date(2026, 11, 30)).cantidad, D('650'))
        self.assertEqual(PlanDemanda.objects.filter(producto=pt).count(), 2)


class CargaProductoPlanificacionTest(Base):
    def test_columnas_de_planificacion_en_productos(self):
        url = reverse('carga_masiva')
        filas = [['nombre', 'tipo', 'tipo_planificacion', 'estrategia'],
                 ['Torta a pedido', 'Producto terminado', 'MRP', 'Contra pedido'],
                 ['Detergente', 'Suministros', '', ''],
                 ['Cajas', 'Materia prima', 'Punto de reorden', '']]
        r = self.client.post(url, {'tipo': 'productos', 'accion': 'validar', 'archivo': excel(filas)})
        self.assertNotContains(r, 'con error')
        self.client.post(url, {'tipo': 'productos', 'accion': 'confirmar'})
        t = Producto.objects.get(nombre='Torta a pedido')
        self.assertEqual((t.tipo_planificacion, t.estrategia), ('MRP', 'MTO'))
        self.assertEqual(Producto.objects.get(nombre='Detergente').tipo_planificacion, 'REORDEN')
        self.assertEqual(Producto.objects.get(nombre='Cajas').tipo_planificacion, 'REORDEN')
