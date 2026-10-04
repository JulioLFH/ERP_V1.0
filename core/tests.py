from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra
from core.models import Producto, Tercero
from finanzas.models import Cuenta, Movimiento
from logistica.models import GuiaRemision
from ventas.models import Venta


class SmokeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        from core.models import Empresa
        Empresa.objects.update(bloquear_deuda_vencida=False)  # los clientes demo tienen facturas vencidas
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def test_paginas_cargan(self):
        v, c = Venta.objects.first(), Compra.objects.first()
        cuenta = Cuenta.objects.filter(tipo='BANCO').first()
        urls = ['dashboard', 'empresa', 'terceros', 'tercero_nuevo', 'productos', 'producto_nuevo', 'series',
                'compras:lista', 'compras:nuevo', 'compras:registro', 'compras:pendientes', 'compras:reportes',
                'compras:importar', 'compras:oc_lista', 'compras:oc_nuevo',
                'ventas:lista', 'ventas:nuevo', 'ventas:registro', 'ventas:pendientes', 'ventas:reportes',
                'ventas:importar', 'ventas:cot_lista', 'ventas:cot_nuevo',
                'finanzas:cuentas', 'finanzas:cuenta_nueva', 'finanzas:movimientos', 'finanzas:movimiento_nuevo',
                'finanzas:cobranza', 'finanzas:pago', 'finanzas:transferencia', 'finanzas:conciliacion',
                'finanzas:importar', 'finanzas:flujo',
                'ventas:notas', 'compras:notas', 'tipos_cambio', 'facturacion', 'inv_stock', 'inv_kardex',
                'inv_ajuste', 'inv_valorizacion', 'almacenes', 'almacen_nuevo', 'logistica:lista',
                'logistica:nueva', 'logistica:vehiculos', 'logistica:vehiculo_nuevo', 'logistica:conductores',
                'logistica:conductor_nuevo']
        for nombre in urls:
            r = self.client.get(reverse(nombre))
            self.assertEqual(r.status_code, 200, nombre)
        extras = [
            reverse('ventas:detalle', args=[v.pk]), reverse('ventas:imprimir', args=[v.pk]),
            reverse('compras:detalle', args=[c.pk]),
            reverse('compras:imprimir', args=[c.pk]),
            reverse('inv_kardex') + f'?producto={Producto.objects.first().pk}',
            reverse('logistica:nueva') + f'?tipo=09&venta={v.pk}', reverse('logistica:nueva') + '?tipo=31',
            reverse('logistica:detalle', args=[GuiaRemision.objects.first().pk]),
            reverse('logistica:imprimir', args=[GuiaRemision.objects.first().pk]),
            reverse('ventas:notas') + f'?ref={v.pk}', reverse('inv_stock') + '?bajo=1',
            reverse('ventas:nuevo') + f'?ref={v.pk}&tipo=07',
            reverse('finanzas:cobranza') + f'?doc={v.pk}', reverse('finanzas:pago') + f'?doc={c.pk}',
            reverse('finanzas:conciliacion') + f'?cuenta={cuenta.pk}&saldo_banco=1000',
            reverse('finanzas:voucher', args=[Movimiento.objects.first().pk]),
            reverse('ventas:reportes') + '?agrupar=producto', reverse('compras:reportes') + '?agrupar=clasificacion',
        ]
        for url in extras:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        # un comprobante emitido no se edita: se corrige con nota de crédito
        self.assertRedirects(self.client.get(reverse('ventas:editar', args=[v.pk])),
                             reverse('ventas:detalle', args=[v.pk]))
        for app in ('compras', 'ventas'):
            for fmt in ('excel', 'ple'):
                r = self.client.get(reverse(f'{app}:registro') + f'?formato={fmt}')
                self.assertEqual(r.status_code, 200)
                self.assertIn('attachment', r['Content-Disposition'])

    def test_flujo_venta_cobranza_nc(self):
        p = Producto.objects.get(codigo='P002')
        stock0 = p.stock
        cli = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        data = {
            'tipo_comprobante': '01', 'serie': '', 'numero': '', 'tercero': cli.pk, 'fecha_emision': '2026-10-01',
            'fecha_vencimiento': '', 'forma_pago': 'CREDITO', 'moneda': 'PEN', 'tipo_cambio': '1',
            'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0',
            'icbper': '0', 'vendedor': '', 'descontar_stock': 'on', 'glosa': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': p.pk, 'items-0-descripcion': p.nombre,
            'items-0-cantidad': '2', 'items-0-precio_unitario': '100',
        }
        r = self.client.post(reverse('ventas:nuevo'), data)
        self.assertEqual(r.status_code, 302, r.content[:3000] if r.status_code == 200 else '')
        venta = Venta.objects.latest('id')
        self.assertEqual(venta.total, Decimal('236.00'))
        self.assertEqual(venta.serie, 'F001')
        p.refresh_from_db()
        self.assertEqual(p.stock, stock0 - 2)

        cuenta = Cuenta.objects.filter(tipo='BANCO').first()
        r = self.client.post(reverse('finanzas:cobranza') + f'?doc={venta.pk}', {
            'cuenta': cuenta.pk, 'fecha': '2026-10-01', 'medio_pago': 'TRANSFERENCIA', f'monto_{venta.pk}': '100'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(venta.saldo, Decimal('136.00'))

        nc = dict(data, tipo_comprobante='07', doc_referencia=venta.pk, motivo_nota='07', **{'items-0-cantidad': '1'})
        r = self.client.post(reverse('ventas:nuevo'), nc)
        self.assertEqual(r.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.stock, stock0 - 1)
        self.assertEqual(venta.saldo, Decimal('18.00'))

        r = self.client.post(reverse('ventas:anular', args=[venta.pk]))
        venta.refresh_from_db()
        self.assertEqual(venta.estado, 'REGISTRADO')  # tiene cobranza → no se anula

    def test_compra_edicion_revierte_stock(self):
        from core.models import Empresa
        Empresa.objects.update(exigir_orden_compra=False)  # compra directa, sin orden de compra
        p = Producto.objects.get(codigo='P003')
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        stock0 = p.stock
        data = {
            'tipo_comprobante': '01', 'serie': 'F900', 'numero': '77', 'tercero': prov.pk,
            'fecha_emision': '2026-10-01', 'fecha_vencimiento': '', 'periodo': '', 'clasificacion': 'MERCADERIA',
            'forma_pago': 'CONTADO', 'moneda': 'PEN', 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA',
            'detraccion_pct': '0', 'retencion_pct': '0', 'percepcion_pct': '0', 'icbper': '0',
            'ingresar_almacen': 'on', 'glosa': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000', 'items-0-producto': p.pk, 'items-0-descripcion': 'x',
            'items-0-cantidad': '10', 'items-0-precio_unitario': '40',
        }
        self.assertEqual(self.client.post(reverse('compras:nuevo'), data).status_code, 302)
        compra = Compra.objects.get(serie='F900')
        p.refresh_from_db()
        self.assertEqual(p.stock, stock0 + 10)
        item = compra.items.first()
        edit = dict(data, **{'items-INITIAL_FORMS': '1', 'items-0-id': item.pk, 'items-0-documento': compra.pk,
                             'items-0-cantidad': '4'})
        self.assertEqual(self.client.post(reverse('compras:editar', args=[compra.pk]), edit).status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.stock, stock0 + 4)
        self.assertEqual(compra.periodo if compra.periodo else '', '202610')
