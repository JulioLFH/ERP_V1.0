"""Pendientes de la hoja (v1.47): formulario de venta sin el catálogo completo, flujo de caja proyectado, pronóstico
de demanda al plan del MRP, mercadería de reventa en el MRP con conversión masiva a solicitud de pedido y utilidad no
realizada en la consolidación del grupo. Un escenario por clase."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Empresa, PerfilUsuario, Producto, Tercero
from .modulos import GRUPOS

D = Decimal
HOY = timezone.localdate()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v147', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)


class FormularioLivianoTest(Base):
    def test_no_descarga_el_catalogo(self):
        self.assertGreater(Producto.objects.filter(activo=True).count(), 3)
        r = self.client.get(reverse('ventas:nuevo'))
        self.assertEqual(r.context['productos'], [])  # sin ítems: no viaja ningún producto
        p = Producto.objects.filter(activo=True).first()
        datos = self.client.get(reverse('producto_datos', args=[p.pk])).json()
        self.assertEqual((datos['id'], datos['nombre']), (p.pk, p.nombre))
        self.assertIn('costo_promedio', datos)  # el administrador ve costos
        vendedor = User.objects.create_user('vend147', password='Clave-segura-9')
        vendedor.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0])
        PerfilUsuario.objects.create(usuario=vendedor, acciones=['ventas.emitir'])
        self.client.force_login(vendedor)
        datos = self.client.get(reverse('producto_datos', args=[p.pk])).json()
        self.assertNotIn('costo_promedio', datos)  # sin permiso de costos no viaja el costo
        contador = User.objects.create_user('cont147', password='Clave-segura-9')
        contador.groups.add(Group.objects.get_or_create(name=GRUPOS['contabilidad'])[0])
        self.client.force_login(contador)
        self.assertEqual(self.client.get(reverse('producto_datos', args=[p.pk])).status_code, 404)


class FlujoProyectadoTest(Base):
    def test_cobros_y_pagos_por_semana(self):
        from compras.models import Compra
        from finanzas.proyeccion import proyectar
        from ventas.models import Venta
        cliente = Tercero.objects.filter(tipo='CLIENTE').first()
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        vencida = Venta.objects.create(tercero=cliente, serie='F147', numero='1', forma_pago='CREDITO',
                                       fecha_emision=HOY - timedelta(days=40), fecha_vencimiento=HOY - timedelta(days=10),
                                       total=D('1180'), igv=D('180'), base_imponible=D('1000'), descontar_stock=False)
        por_pagar = Compra.objects.create(tercero=prov, serie='F147', numero='9', forma_pago='CREDITO',
                                          fecha_emision=HOY, fecha_vencimiento=HOY + timedelta(days=10),
                                          total=D('590'), igv=D('90'), base_imponible=D('500'), ingresar_almacen=False)
        p = proyectar(12)
        filas = {f['concepto']: f for f in p['filas']}
        self.assertIn(vencida, [d['doc'] for d in filas['Cobranzas vencidas']['documentos']])
        doc = next(d for d in filas['Pagos a proveedores por vencer']['documentos'] if d['doc'] == por_pagar)
        self.assertEqual((doc['semana'], doc['monto']), (2, D('590')))
        self.assertEqual(p['saldos'][-1], p['saldo_inicial'] + sum(p['ingresos']) - sum(p['egresos']))
        r = self.client.get(reverse('finanzas:flujo_proyectado'))
        self.assertContains(r, 'Cobranzas vencidas')
        self.assertIn('spreadsheetml', self.client.get(reverse('finanzas:flujo_proyectado'),
                                                       {'formato': 'excel'})['Content-Type'])


class PronosticoTest(Base):
    def test_pronostica_y_pasa_al_plan(self):
        from produccion.models import PlanDemanda
        from produccion.pronostico import pronosticar
        from ventas.models import Venta, VentaItem
        metodo, error, valores = pronosticar([10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32], 3)
        self.assertEqual(metodo, 'HOLT')  # demanda con tendencia: gana el método con tendencia
        self.assertGreater(valores[2], valores[0])
        producto = Producto.objects.create(nombre='Galleta pronóstico', clase='MERCADERIA', precio_venta=D('5'))
        cliente = Tercero.objects.filter(tipo='CLIENTE').first()
        inicio = date(HOY.year, HOY.month, 1)
        for k in range(1, 13):
            total = inicio.year * 12 + inicio.month - 1 - k
            mes = date(total // 12, total % 12 + 1, 10)
            v = Venta.objects.create(tercero=cliente, serie='B147', numero=str(k), tipo_comprobante='03',
                                     fecha_emision=mes, descontar_stock=False)
            VentaItem.objects.create(documento=v, producto=producto, descripcion='Galleta', cantidad=100 + k,
                                     precio_unitario=D('5'))
        r = self.client.get(reverse('manufactura:pronostico'), {'q': 'Galleta pronóstico', 'meses': 3})
        fila = next(f for f in r.context['filas'] if f['producto'] == producto)
        self.assertEqual(len(fila['pronostico']), 3)
        self.client.post(reverse('manufactura:pronostico') + '?q=Galleta pronóstico&meses=3',
                         {'producto': [producto.pk]})
        self.assertEqual(PlanDemanda.objects.filter(producto=producto).count(), 3)
        self.client.post(reverse('manufactura:pronostico') + '?q=Galleta pronóstico&meses=3',
                         {'producto': [producto.pk]})
        self.assertEqual(PlanDemanda.objects.filter(producto=producto).count(), 3)  # reemplaza, no duplica
        self.assertTrue(PlanDemanda.objects.filter(producto=producto, nota__startswith='Pronóstico').exists())


class MRPReventaTest(Base):
    def test_mercaderia_se_propone_comprar_y_se_convierte_en_bloque(self):
        from compras.models import RegistroInfo, SolicitudPedido
        from produccion import servicios
        from produccion.models import PlanDemanda, PropuestaMRP
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        productos = []
        for n in range(2):
            p = Producto.objects.create(nombre=f'Reventa MRP {n}', clase='MERCADERIA', tipo_planificacion='MRP',
                                        estrategia='MTS', precio_venta=D('10'))
            RegistroInfo.objects.create(producto=p, proveedor=prov, plazo_entrega=5, preferido=True)
            PlanDemanda.objects.create(producto=p, cantidad=D('40'), fecha=HOY + timedelta(days=20))
            productos.append(p)
        corrida = servicios.ejecutar_mrp(HOY + timedelta(days=60), self.admin)
        propuestas = list(PropuestaMRP.objects.filter(corrida=corrida, producto__in=productos))
        self.assertEqual({(p.tipo, p.proveedor_id) for p in propuestas}, {('COMPRAR', prov.pk)})
        self.assertEqual(propuestas[0].fecha_inicio, HOY + timedelta(days=15))  # 20 días menos 5 de entrega
        antes = SolicitudPedido.objects.count()
        from contabilidad.models import CentroCosto
        centro = CentroCosto.objects.create(codigo='CC-147', nombre='Comercial 147', tipo='VENTAS')
        r = self.client.post(reverse('manufactura:mrp'), {'accion': 'comprar', 'centro_costo': centro.pk,
                                                          'propuesta': [p.pk for p in propuestas]}, follow=True)
        self.assertEqual(SolicitudPedido.objects.count(), antes + 1, [str(m) for m in r.context['messages']])
        sp = SolicitudPedido.objects.order_by('-pk').first()
        self.assertEqual(sp.lineas.count(), 2)


class UtilidadNoRealizadaTest(TestCase):
    databases = {'default', 'empresa2'}

    def test_margen_en_el_inventario_de_la_compradora(self):
        from compras.models import Compra, CompraItem
        from contabilidad.cierre import utilidad_no_realizada
        from erp.empresas import activar, restaurar
        from ventas.models import Venta, VentaItem
        Empresa.objects.create(ruc='20100000001', razon_social='VENDEDORA S.A.C.')
        compradora_t = Tercero.objects.create(numero_doc='20600000002', nombre='COMPRADORA S.A.C.', tipo='CLIENTE')
        p = Producto.objects.create(codigo='UNR-1', nombre='Producto grupo', clase='MERCADERIA',
                                    costo_promedio=D('60'))
        v = Venta.objects.create(tercero=compradora_t, serie='F001', numero='1', fecha_emision=HOY,
                                 descontar_stock=False)
        VentaItem.objects.create(documento=v, producto=p, descripcion='Producto grupo', cantidad=10,
                                 precio_unitario=D('100'))
        token = activar('empresa2')
        try:
            Empresa.objects.create(ruc='20600000002', razon_social='COMPRADORA S.A.C.')
            vendedora_t = Tercero.objects.create(numero_doc='20100000001', nombre='VENDEDORA S.A.C.',
                                                 tipo='PROVEEDOR')
            p2 = Producto.objects.create(codigo='UNR-1', nombre='Producto grupo', clase='MERCADERIA')
            c = Compra.objects.create(tercero=vendedora_t, serie='F001', numero='1', fecha_emision=HOY,
                                      ingresar_almacen=True)
            CompraItem.objects.create(documento=c, producto=p2, descripcion='Producto grupo', cantidad=10,
                                      precio_unitario=D('100'))
            c.calcular_totales()
            c.save()
            c.aplicar_stock()
            p2.mover_stock(D('-4'), 'Venta a terceros', fecha=HOY)  # 6 siguen en su inventario
        finally:
            restaurar(token)
        datos = [{'alias': 'default', 'ruc': '20100000001', 'nombre': 'VENDEDORA S.A.C.'},
                 {'alias': 'empresa2', 'ruc': '20600000002', 'nombre': 'COMPRADORA S.A.C.'}]
        total, detalle = utilidad_no_realizada(datos, HOY.strftime('%Y%m'))
        # margen 40% (100 venta, 60 costo) sobre 6 unidades a 100 = 600 de inventario -> 240 a eliminar
        self.assertEqual(total, D('240.00'))
        self.assertEqual((detalle[0]['codigo'], detalle[0]['cantidad'], detalle[0]['margen_pct']),
                         ('UNR-1', D('6'), D('40.00')))
