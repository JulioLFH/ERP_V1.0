"""v1.58, checklist v1.56 (productos y contabilidad): cuentas por tipo de producto editables (INV-11), listas de cuentas
filtradas por clase (INV-12), tipo Envases y embalajes (INV-13), pestaña Costos (INV-14) y pestaña Almacenes con
límites por almacén que usa la sugerencia de compra (INV-15). Un escenario por clase."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.models import CuentaContable

from .models import Almacen, CuentasTipoProducto, Producto, StockAlmacen

D = Decimal


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v158', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)


class CuentasPorTipoTest(Base):
    def test_tabla_editable_aplica_a_los_productos_y_respeta_excepciones(self):
        r = self.client.get(reverse('contabilidad:cuentas_tipo'))
        self.assertContains(r, 'Cuentas por tipo de producto')
        self.assertEqual(CuentasTipoProducto.objects.count(), len(Producto.CLASES))  # una fila por tipo
        mp = CuentasTipoProducto.objects.get(clase='MATERIA_PRIMA')
        sigue = Producto.objects.create(codigo='MP-158A', nombre='Sigue al tipo', clase='MATERIA_PRIMA')
        excepcion = Producto.objects.create(codigo='MP-158B', nombre='Excepción', clase='MATERIA_PRIMA')
        otra_venta = CuentaContable.objects.filter(codigo__startswith='70', imputable=True).exclude(
            pk=mp.cuenta_venta_id).order_by('codigo').first()
        tercera = CuentaContable.objects.filter(codigo__startswith='70', imputable=True).exclude(
            pk__in=[mp.cuenta_venta_id, otra_venta.pk]).order_by('codigo').first()
        excepcion.cuenta_venta = tercera
        excepcion.save()
        filas = list(CuentasTipoProducto.objects.all())
        datos = {'form-TOTAL_FORMS': len(filas), 'form-INITIAL_FORMS': len(filas), 'form-MIN_NUM_FORMS': 0,
                 'form-MAX_NUM_FORMS': 1000}
        for n, f in enumerate(filas):
            datos[f'form-{n}-id'] = f.pk
            for campo in ('cuenta_existencias', 'cuenta_compra', 'cuenta_variacion', 'cuenta_venta', 'cuenta_costo',
                          'cuenta_produccion', 'cuenta_diferencia_precio'):
                valor = getattr(f, f'{campo}_id')
                if f.pk == mp.pk and campo == 'cuenta_venta':
                    valor = otra_venta.pk
                datos[f'form-{n}-{campo}'] = valor or ''
            if f.pk == mp.pk:
                datos[f'form-{n}-aplicar'] = 'on'
        r = self.client.post(reverse('contabilidad:cuentas_tipo'), datos, follow=True)
        self.assertContains(r, 'Cuentas por tipo de producto guardadas')
        sigue.refresh_from_db()
        excepcion.refresh_from_db()
        self.assertEqual(sigue.cuenta_venta_id, otra_venta.pk)  # seguía al tipo: se actualiza
        self.assertEqual(excepcion.cuenta_venta_id, tercera.pk)  # excepción: no se toca
        nuevo = Producto.objects.create(codigo='MP-158C', nombre='Nuevo', clase='MATERIA_PRIMA')
        self.assertEqual(nuevo.cuenta_venta_id, otra_venta.pk)  # los nuevos heredan la tabla


class FichaContabilidadTest(Base):
    def test_listas_filtradas_envases_y_pestanas(self):
        from .forms import ProductoForm
        form = ProductoForm(instance=Producto.objects.filter(clase='MERCADERIA').first())
        codigos = [c.codigo for c in form.fields['cuenta_existencias'].queryset]
        self.assertTrue(codigos and all(c[:2] in ('20', '21', '22', '23', '24', '25', '26', '27', '28')
                                        for c in codigos))  # no «101 Caja»
        self.assertTrue(all(c.codigo.startswith('69') or c.codigo.startswith('65')
                            for c in form.fields['cuenta_costo'].queryset))
        envase = Producto.objects.create(nombre='Botella PET 500 ml', clase='ENVASE')
        self.assertTrue(envase.codigo.startswith('EM'))
        if CuentaContable.objects.filter(codigo__startswith='2611').exists():
            self.assertTrue(envase.cuenta_existencias.codigo.startswith('26'))
            self.assertTrue(envase.cuenta_compra.codigo.startswith('604'))
        p = Producto.objects.create(codigo='ZZ-158', nombre='Insumo 158', clase='MATERIA_PRIMA', unidad='KGM')
        p.mover_stock(D('10'), 'ingreso', costo=D('2.50'), almacen=Almacen.principal())
        r = self.client.get(reverse('producto_editar', args=[p.pk]))
        self.assertContains(r, 'pest-almacenes')
        self.assertContains(r, 'pest-costos')
        self.assertContains(r, 'Promedio ponderado móvil')


class LimitesPorAlmacenTest(Base):
    def test_limites_por_almacen_y_sugerencia_de_compra(self):
        from .inventario import sugerencias_compra
        principal = Almacen.principal()
        p = Producto.objects.create(codigo='ZZ-1581', nombre='Repuesto 158', clase='SUMINISTRO', unidad='NIU',
                                    precio_compra=D('5'))
        p.mover_stock(D('3'), 'ingreso', costo=D('5'), almacen=principal)
        datos = {f: getattr(p, f) for f in ('clase', 'codigo', 'nombre', 'unidad')}
        from .forms import ProductoForm
        base = {k: v for k, v in ProductoForm(instance=p).initial.items() if v is not None and k != 'imagen'}
        base.update(datos)
        base.update({f'alm_{principal.pk}_min': '5', f'alm_{principal.pk}_reorden': '6',
                     f'alm_{principal.pk}_max': '20'})
        base = {k: (v.pk if hasattr(v, 'pk') else v) for k, v in base.items()}
        self.client.post(reverse('producto_editar', args=[p.pk]), base)
        s = StockAlmacen.objects.get(producto=p, almacen=principal)
        self.assertEqual((s.stock_minimo, s.punto_reorden, s.stock_maximo), (D('5'), D('6'), D('20')))
        self.assertTrue(s.bajo_minimo)
        fila = next(f for f in sugerencias_compra() if f['p'].pk == p.pk)
        self.assertEqual(fila['almacenes_bajo'], principal.codigo)
        self.assertEqual(fila['cantidad'], D('17'))  # hasta el máximo del almacén: 20 − 3
