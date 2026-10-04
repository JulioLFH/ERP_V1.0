"""Lotes, series y vencimientos (v1.15): ingreso con lote, salida FEFO, anulación a los mismos lotes, traslados,
ventas, validaciones y alertas."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import alertas
from core.models import Almacen, Kardex, Lote, Producto, StockLote, Tercero
from core.sustentos import adjuntar
from ventas.models import Venta, VentaItem

from . import servicios
from .models import Operacion, TipoOperacion

D = Decimal
HOY = date.today()


def stock_lote(producto, codigo, almacen=None):
    qs = StockLote.objects.filter(lote__producto=producto, lote__codigo=codigo)
    if almacen:
        qs = qs.filter(almacen=almacen)
    return sum((s.cantidad for s in qs), D('0'))


class LotesTest(TestCase):
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
        self.jarabe = Producto.objects.create(nombre='Jarabe 120 ml', clase='MERCADERIA', control='LOTE')

    def operacion(self, codigo, filas, **campos):
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo=codigo), fecha=HOY, **campos)
        for producto, cantidad, costo, lote, vence in filas:
            op.items.create(producto=producto, cantidad=D(cantidad), costo_unitario=D(costo) if costo else None,
                            lote=lote, vencimiento=vence)
        if op.tipo.requiere_sustento:
            adjuntar(op, SimpleUploadedFile('acta.pdf', b'%PDF-1.4 acta', content_type='application/pdf'))
        return op

    def ingresar(self):
        op = self.operacion('AJ_ING', [(self.jarabe, '10', '5', 'L-TARDE', HOY + timedelta(days=200)),
                                       (self.jarabe, '6', '5', 'L-PRONTO', HOY + timedelta(days=20))],
                            almacen_destino=self.almacen)
        servicios.confirmar(op, self.user)
        return op

    def test_ingreso_y_salida_fefo_y_anulacion(self):
        self.ingresar()
        self.assertEqual((stock_lote(self.jarabe, 'L-TARDE'), stock_lote(self.jarabe, 'L-PRONTO')), (10, 6))
        self.assertEqual(Lote.objects.get(producto=self.jarabe, codigo='L-PRONTO').vencimiento,
                         HOY + timedelta(days=20))
        salida = self.operacion('CONS_INT', [(self.jarabe, '8', None, '', None)], almacen_origen=self.almacen)
        servicios.confirmar(salida, self.user)
        # sale primero el lote que vence primero
        self.assertEqual((stock_lote(self.jarabe, 'L-PRONTO'), stock_lote(self.jarabe, 'L-TARDE')), (0, 8))
        self.assertEqual(Kardex.objects.filter(producto=self.jarabe, tipo='SALIDA').count(), 2)
        servicios.anular(salida, self.user, 'Consumo registrado por error')
        self.assertEqual((stock_lote(self.jarabe, 'L-PRONTO'), stock_lote(self.jarabe, 'L-TARDE')), (6, 10))

    def test_salida_de_un_lote_indicado_y_validaciones(self):
        sin_lote = self.operacion('AJ_ING', [(self.jarabe, '5', '5', '', None)], almacen_destino=self.almacen)
        self.assertTrue(any('indíquelo' in e for e in servicios.errores_confirmacion(sin_lote)))
        self.ingresar()
        mucho = self.operacion('CONS_INT', [(self.jarabe, '7', None, 'L-PRONTO', None)], almacen_origen=self.almacen)
        self.assertTrue(any('L-PRONTO solo tiene 6' in e for e in servicios.errores_confirmacion(mucho)))
        ok = self.operacion('CONS_INT', [(self.jarabe, '3', None, 'L-TARDE', None)], almacen_origen=self.almacen)
        servicios.confirmar(ok, self.user)
        self.assertEqual(stock_lote(self.jarabe, 'L-TARDE'), 7)

    def test_series(self):
        laptop = Producto.objects.create(nombre='Laptop serie', clase='MERCADERIA', control='SERIE')
        mal = self.operacion('AJ_ING', [(laptop, '2', '2000', 'SN1', None)], almacen_destino=self.almacen)
        self.assertTrue(any('2 número(s) de serie' in e for e in servicios.errores_confirmacion(mal)))
        ok = self.operacion('AJ_ING', [(laptop, '2', '2000', 'sn1, sn2', None)], almacen_destino=self.almacen)
        servicios.confirmar(ok, self.user)
        self.assertEqual(set(Lote.objects.filter(producto=laptop).values_list('codigo', flat=True)), {'SN1', 'SN2'})
        repetida = self.operacion('AJ_ING', [(laptop, '1', '2000', 'SN1', None)], almacen_destino=self.almacen)
        errores = servicios.errores_confirmacion(repetida)
        self.assertTrue(any('ya están en stock' in e for e in errores), errores)

    def test_venta_fefo_y_anulacion_vuelve_al_lote(self):
        self.ingresar()
        cliente = Tercero.objects.filter(tipo_doc='6', tipo__in=['CLIENTE', 'AMBOS']).first()
        v = Venta.objects.create(tercero=cliente, serie='F001', numero='88888', almacen=self.almacen)
        VentaItem.objects.create(documento=v, producto=self.jarabe, descripcion='Jarabe', cantidad=4,
                                 precio_unitario=D('10'))
        v.aplicar_stock()
        self.assertEqual(stock_lote(self.jarabe, 'L-PRONTO'), 2)
        v.revertir_stock()
        self.assertEqual(stock_lote(self.jarabe, 'L-PRONTO'), 6)

    def test_traslado_conserva_los_lotes(self):
        self.ingresar()
        destruccion = Almacen.especial('DESTRUCCION')
        op = self.operacion('TRAS_DESTR', [(self.jarabe, '6', None, '', None)], almacen_origen=self.almacen,
                            almacen_destino=destruccion)
        servicios.confirmar(op, self.user)
        self.assertEqual(stock_lote(self.jarabe, 'L-PRONTO', destruccion), 6)
        self.assertEqual(stock_lote(self.jarabe, 'L-PRONTO', self.almacen), 0)

    def test_alertas_y_pantallas(self):
        self.ingresar()
        Lote.objects.filter(codigo='L-PRONTO').update(vencimiento=HOY - timedelta(days=1))
        lista = alertas.calcular(self.user)
        lote = next(a for a in lista if a['titulo'] == 'Lotes por vencer')
        self.assertEqual(lote['nivel'], 'danger')
        r = self.client.get(reverse('alertas_json'))
        self.assertGreaterEqual(r.json()['total'], 1)
        l = Lote.objects.get(codigo='L-TARDE')
        for url in [reverse('alertas'), reverse('inv_lotes'), reverse('inv_lotes') + '?vencimiento=1&dias=400',
                    reverse('inv_lote', args=[l.pk]), reverse('inv_lotes') + '?formato=excel']:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(reverse('inv_lote', args=[l.pk])), 'Ajuste ingreso')
