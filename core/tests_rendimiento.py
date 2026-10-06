"""Pantallas livianas con miles de registros: selectores remotos, listas por permiso, contabilización por partes y
stock paginado."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad import automatico
from contabilidad.models import PeriodoContable

from .forms import SelectRemoto, item_formset
from .models import Producto
from .modulos import GRUPOS

D = Decimal


class RendimientoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('r', 'r@e.pe', 'x')

    def setUp(self):
        self.client.force_login(self.admin)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def test_selector_remoto_solo_trae_la_opcion_elegida(self):
        from ventas.models import Venta, VentaItem
        p = Producto.objects.filter(activo=True).first()
        fs = item_formset(Venta, VentaItem)(initial=[{'producto': p.pk}])
        campo = fs.forms[0]['producto']
        self.assertIsInstance(campo.field.widget, SelectRemoto)
        html = str(campo)
        self.assertEqual(html.count('<option'), 2)  # vacía + la elegida
        self.assertIn(f'value="{p.pk}" selected', html)
        self.assertIn('data-opciones="/opciones/productos/"', html)
        # la validación sigue siendo la del queryset del campo
        plantilla = Producto.objects.create(codigo='PLT1', nombre='Polo', clase='MERCADERIA', es_plantilla=True)
        datos = {'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-0-producto': plantilla.pk,
                 'items-0-descripcion': 'x', 'items-0-cantidad': '1', 'items-0-precio_unitario': '1'}
        fs = item_formset(Venta, VentaItem)(datos)
        self.assertFalse(fs.is_valid())
        self.assertIn('producto', fs.forms[0].errors)

    def test_listas_de_opciones_y_permisos(self):
        lista = self.client.get(reverse('opciones', args=['productos'])).json()
        self.assertTrue(lista and isinstance(lista[0][0], int) and ' - ' in lista[0][1])
        self.assertTrue(self.client.get(reverse('opciones', args=['ventas'])).json())
        self.assertEqual(self.client.get(reverse('opciones', args=['inventada'])).status_code, 404)
        rrhh = User.objects.create_user('rrhh', 'h@e.pe', 'x')
        rrhh.groups.add(Group.objects.get_or_create(name=GRUPOS['planillas'])[0])
        self.client.force_login(rrhh)
        self.assertEqual(self.client.get(reverse('opciones', args=['ventas'])).status_code, 404)
        self.assertEqual(self.client.get(reverse('opciones', args=['cuentas'])).status_code, 200)

    def test_contabilizacion_por_partes(self):
        automatico.actualizar_pendientes()
        PeriodoContable.objects.update(pendiente=True)
        self.assertTrue(automatico.periodos_pendientes())
        automatico.actualizar_pendientes(limite_segundos=-1)  # sin tiempo: no empieza ningún periodo
        self.assertTrue(automatico.periodos_pendientes())
        automatico.actualizar_pendientes(limite_segundos=60)
        self.assertEqual(automatico.periodos_pendientes(), [])

    def test_stock_paginado_y_solo_con_stock(self):
        sin_stock = Producto.objects.create(codigo='ZZ01', nombre='Sin stock', clase='MERCADERIA')
        r = self.client.get(reverse('inv_stock'))
        self.assertNotContains(r, 'ZZ01')
        self.assertContains(self.client.get(reverse('inv_stock'), {'todos': '1', 'q': 'ZZ01'}), sin_stock.nombre)
