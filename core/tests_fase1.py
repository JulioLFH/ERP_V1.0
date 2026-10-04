"""Pruebas de la Fase 1 (v1.6): productos por tipo y código, datos generales, juego de cuentas, activos,
ubigeo en cascada y tipo de cambio SBS."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra, CompraItem
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import Asiento, AsientoLinea
from core import tipo_cambio, ubigeo
from core.forms import AlmacenForm, EmpresaForm
from core.models import Almacen, Empresa, Producto, Tercero, TipoCambio
from inventario import servicios
from inventario.models import Operacion, TipoOperacion
from ventas.models import Venta, VentaItem

D = Decimal
HOY = date.today()
TC = (D('3.441'), D('3.450'))


def saldo(codigo, hasta=None):
    qs = AsientoLinea.objects.filter(cuenta__codigo=codigo)
    if hasta:
        qs = qs.filter(asiento__fecha__lte=hasta)
    return sum((l.debe - l.haber for l in qs), D('0'))


class Fase1Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=TC):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=TC)
        p.start()
        self.addCleanup(p.stop)

    # ---------------------------------------------------------------- 1. código por tipo
    def test_codigo_automatico_por_tipo(self):
        mp1 = Producto.objects.create(nombre='Harina', clase='MATERIA_PRIMA', unidad='KGM')
        mp2 = Producto.objects.create(nombre='Azúcar', clase='MATERIA_PRIMA', unidad='KGM')
        se = Producto.objects.create(nombre='Masa base', clase='SEMIELABORADO')
        sv = Producto.objects.create(nombre='Instalación', clase='SERVICIO')
        self.assertEqual((mp1.codigo, mp2.codigo, se.codigo, sv.codigo),
                         ('MP000001', 'MP000002', 'SE000001', 'SV000001'))
        self.assertEqual((mp1.tipo, se.tipo, sv.tipo), ('BIEN', 'BIEN', 'SERVICIO'))
        self.assertTrue(mp1.es_inventariable and se.es_inventariable)
        self.assertFalse(sv.es_inventariable)
        # un código manual ya usado se salta
        Producto.objects.create(codigo='PT000001', nombre='Manual', clase='PRODUCTO_TERMINADO')
        self.assertEqual(Producto.objects.create(nombre='Pan', clase='PRODUCTO_TERMINADO').codigo, 'PT000002')

    # ---------------------------------------------------------------- 2 y 3. datos generales y cuentas
    def test_juego_de_cuentas_por_tipo(self):
        esperado = {'MERCADERIA': ('20111', '6011', '70111', '69111'),
                    'MATERIA_PRIMA': ('2411', '6021', '70111', '69111'),
                    'SEMIELABORADO': ('2311', '6021', '7021', '6921'),
                    'PRODUCTO_TERMINADO': ('2111', '6011', '7021', '6921'),
                    'SUMINISTRO': ('2521', '6032', '7599', '6561')}
        for clase, cuentas in esperado.items():
            p = Producto.objects.create(nombre=f'Prueba {clase}', clase=clase)
            obtenido = tuple(c.codigo for c in (p.cuenta_existencias, p.cuenta_compra, p.cuenta_venta,
                                                p.cuenta_costo))
            self.assertEqual(obtenido, cuentas, clase)
        sv = Producto.objects.create(nombre='Asesoría', clase='SERVICIO')
        self.assertEqual((sv.cuenta_existencias, sv.cuenta_compra.codigo, sv.cuenta_venta.codigo),
                         (None, '6399', '7041'))
        # los productos de la demo (creados antes) también tienen su juego de cuentas
        self.assertFalse(Producto.objects.filter(cuenta_venta__isnull=True).exists())

    def test_formulario_con_pestanas_y_codigo_automatico(self):
        r = self.client.get(reverse('producto_nuevo') + '?clase=MATERIA_PRIMA')
        for pestana in ('General', 'Compras', 'Ventas', 'Contabilidad', 'Planificación'):
            self.assertContains(r, pestana)
        datos = {'clase': 'MATERIA_PRIMA', 'codigo': '', 'nombre': 'Resina epóxica', 'unidad': 'KGM', 'marca': '',
                 'codigo_barras': '', 'descripcion': '', 'activo': 'on', 'puede_comprarse': 'on',
                 'precio_compra': '12.50', 'proveedor': '', 'unidad_compra': '', 'puede_venderse': '',
                 'precio_venta': '0', 'cuenta_existencias': '', 'cuenta_compra': '', 'cuenta_venta': '',
                 'cuenta_costo': '', 'stock_minimo': '10', 'punto_reorden': '20', 'stock_maximo': '100',
                 'lote_compra': '50', 'tiempo_entrega': '7', 'almacen_defecto': ''}
        r = self.client.post(reverse('producto_nuevo'), datos)
        self.assertRedirects(r, reverse('productos'))
        p = Producto.objects.get(nombre='Resina epóxica')
        self.assertTrue(p.codigo.startswith('MP'))
        self.assertEqual((p.cuenta_existencias.codigo, p.tiempo_entrega, p.punto_reorden), ('2411', 7, D('20')))
        # al editar, el tipo no se puede cambiar
        datos.update(clase='SERVICIO', codigo=p.codigo)
        self.client.post(reverse('producto_editar', args=[p.pk]), datos)
        p.refresh_from_db()
        self.assertEqual(p.clase, 'MATERIA_PRIMA')
        self.assertEqual(self.client.get(reverse('productos') + '?clase=MATERIA_PRIMA').status_code, 200)

    # ---------------------------------------------------------------- 4. activo: solo compra
    def test_activo_solo_compra_muestra_precio(self):
        laptop = Producto.objects.create(nombre='Laptop para oficina', clase='ACTIVO', precio_compra=D('3200'),
                                         precio_venta=D('999'), puede_venderse=True)
        self.assertEqual(laptop.codigo, 'AF000001')
        self.assertFalse(laptop.puede_venderse)
        self.assertFalse(laptop.es_inventariable)
        self.assertEqual(laptop.precio_referencia, D('3200'))
        self.assertEqual(laptop.cuenta_compra.codigo, '3369')
        r = self.client.get(reverse('producto_editar', args=[laptop.pk]))
        self.assertContains(r, "activo ? 'Precio'")

    # ---------------------------------------------------------------- contabilidad con cuentas del producto
    def test_contabilidad_usa_las_cuentas_del_producto(self):
        mp = Producto.objects.create(nombre='Harina', clase='MATERIA_PRIMA', unidad='KGM', precio_venta=D('5'))
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        c = Compra.objects.create(tercero=prov, serie='F010', numero='77', fecha_emision=HOY)
        CompraItem.objects.create(documento=c, producto=mp, descripcion='Harina', cantidad=100,
                                  precio_unitario=D('3'))
        c.calcular_totales()
        c.save()
        c.aplicar_stock()
        cli = Tercero.objects.filter(tipo='CLIENTE', tipo_doc='6').first()
        v = Venta.objects.create(tipo_comprobante='01', serie='F001', numero='00009999', tercero=cli,
                                 fecha_emision=HOY)
        VentaItem.objects.create(documento=v, producto=mp, descripcion='Harina', cantidad=40, precio_unitario=D('5'))
        v.calcular_totales()
        v.save()
        v.aplicar_stock()
        # manufactura: 20 kg de harina pasan a producto terminado
        pt = Producto.objects.create(nombre='Pan de molde', clase='PRODUCTO_TERMINADO')
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo='MANUF'), fecha=HOY,
                                      almacen_origen=Almacen.principal(), almacen_destino=Almacen.principal())
        op.items.create(producto=mp, cantidad=20, rol='INSUMO')
        op.items.create(producto=pt, cantidad=10, rol='PRODUCTO')
        servicios.confirmar(op)

        periodo = HOY.strftime('%Y%m')
        centralizar_periodo(periodo)
        compra = c.asientos.get()
        self.assertEqual(compra.lineas.get(cuenta__codigo='6021').debe, D('300.00'))
        self.assertTrue(compra.lineas.filter(cuenta__codigo='2841', debe=D('300.00')).exists())
        self.assertTrue(compra.lineas.filter(cuenta__codigo='6121', haber=D('300.00')).exists())
        self.assertEqual(v.asientos.get().lineas.get(cuenta__codigo='70111').haber, D('200.00'))
        inv = Asiento.objects.get(periodo=periodo, origen='INVENTARIO')
        self.assertEqual(inv.lineas.filter(cuenta__codigo='2841').get().haber, D('300.00'))
        # existencias: 2411 = 40 kg de harina a S/ 3; 2111 = 10 panes con el costo de la harina
        self.assertEqual(saldo('2411'), D('120.00'))
        self.assertEqual(saldo('2111'), D('60.00'))
        self.assertEqual(saldo('2841'), D('0.00'))
        for a in Asiento.objects.all():
            self.assertTrue(a.cuadrado, f'{a} no cuadra')

    # ---------------------------------------------------------------- 5. ubigeo
    def test_ubigeo_inei(self):
        self.assertEqual(len(ubigeo.tabla()), 1874)
        self.assertEqual(len(ubigeo.arbol()), 25)
        self.assertEqual(ubigeo.descripcion('150131'), 'San Isidro - Lima - Lima')
        r = self.client.get(reverse('ubigeos_json'))
        self.assertEqual(len(r.json()), 25)
        base = {'codigo': 'ALM09', 'nombre': 'Almacén Arequipa', 'direccion': 'Av. Ejército 100',
                'codigo_sunat': '0002', 'uso': '', 'activo': 'on'}
        malo = AlmacenForm(data={**base, 'ubigeo': '999999'})
        self.assertFalse(malo.is_valid())
        self.assertIn('ubigeo', malo.errors)
        bueno = AlmacenForm(data={**base, 'ubigeo': '040101'})
        self.assertTrue(bueno.is_valid(), bueno.errors)
        self.assertEqual(list(bueno.fields)[list(bueno.fields).index('ubigeo') - 2:list(bueno.fields).index('ubigeo')],
                         ['ubigeo_departamento', 'ubigeo_provincia'])
        r = self.client.get(reverse('almacen_nuevo'))
        self.assertContains(r, 'data-ubigeo-nivel="departamento"')

    # ---------------------------------------------------------------- 6. tipo de cambio SBS
    def test_tipo_cambio_sbs_con_respaldo_sunat(self):
        e = Empresa.actual()
        e.fuente_tipo_cambio, e.token_tipo_cambio = 'SBS', 'token-de-prueba'
        e.save()
        TipoCambio.objects.all().delete()
        patch.stopall()
        with patch('core.tipo_cambio._consultar_sbs', return_value=(D('3.380'), D('3.385'))) as sbs, \
                patch('core.tipo_cambio._consultar_sunat', return_value=(D('3.370'), D('3.390'))):
            tc = tipo_cambio.obtener(HOY)
        self.assertEqual((tc.venta, tc.fuente), (D('3.385'), 'SBS'))
        self.assertEqual(sbs.call_args[0][1], 'token-de-prueba')
        TipoCambio.objects.all().delete()
        from django.core.cache import cache
        cache.clear()
        with patch('core.tipo_cambio._consultar_sbs', side_effect=OSError('sin conexión')), \
                patch('core.tipo_cambio._consultar_sunat', return_value=(D('3.370'), D('3.390'))):
            tc = tipo_cambio.obtener(HOY)
        self.assertEqual((tc.venta, tc.fuente), (D('3.390'), 'SUNAT'))

    def test_sbs_exige_token(self):
        e = Empresa.actual()
        datos = {'ruc': e.ruc, 'razon_social': e.razon_social, 'nombre_comercial': '', 'direccion': 'Av. 1',
                 'telefono': '', 'email': '', 'igv_tasa': '18', 'ubigeo': '150131', 'registro_mtc': '',
                 'fuente_tipo_cambio': 'SBS', 'token_tipo_cambio': ''}
        form = EmpresaForm(data=datos, instance=e)
        self.assertFalse(form.is_valid())
        self.assertIn('token_tipo_cambio', form.errors)
