"""Variantes de producto e imagen (v1.17, punto 25)."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .forms import item_formset
from .models import ApiToken, Producto
from .variantes import ErrorVariantes, generar, leer_atributos

D = Decimal
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


class VariantesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        self.polo = Producto.objects.create(codigo='POLO', nombre='Polo algodón', clase='MERCADERIA',
                                            precio_venta=D('39.90'), punto_reorden=D('5'))

    def test_leer_atributos(self):
        self.assertEqual(leer_atributos('Talla: S, M, M, L\nColor: Rojo'), {'Talla': ['S', 'M', 'L'],
                                                                            'Color': ['Rojo']})
        for malo in ('', 'Talla S', 'Talla:', 'Talla: S\nTalla: M'):
            with self.assertRaises(ErrorVariantes):
                leer_atributos(malo)

    def test_generar_combinaciones_heredando_datos(self):
        nuevas = generar(self.polo, {'Talla': ['S', 'M'], 'Color': ['Negro', 'Azul marino']})
        self.assertEqual(len(nuevas), 4)
        self.polo.refresh_from_db()
        self.assertTrue(self.polo.es_plantilla)
        v = Producto.objects.get(codigo='POLO-M-NEGR')
        self.assertEqual((v.nombre, v.precio_venta, v.plantilla, v.atributos, v.punto_reorden),
                         ('Polo algodón (M, Negro)', D('39.90'), self.polo, {'Talla': 'M', 'Color': 'Negro'}, D('5')))
        # agregar un valor: solo crea las combinaciones que faltan
        self.assertEqual(len(generar(self.polo, {'Talla': ['S', 'M', 'L'], 'Color': ['Negro', 'Azul marino']})), 2)
        self.assertEqual(self.polo.variantes.count(), 6)
        # la plantilla no aparece en los documentos; las variantes sí
        from ventas.models import Venta, VentaItem
        qs = item_formset(Venta, VentaItem)().empty_form.fields['producto'].queryset
        self.assertNotIn(self.polo, qs)
        self.assertIn(v, qs)
        self.assertFalse(self.polo.requiere_reposicion)
        # una variante no puede tener variantes; un producto con stock tampoco
        with self.assertRaises(ErrorVariantes):
            generar(v, {'Talla': ['S']})
        con_stock = Producto.objects.get(codigo='P002')
        if con_stock.stock:
            with self.assertRaises(ErrorVariantes):
                generar(con_stock, {'Talla': ['S']})

    def test_pantalla_variantes_e_imagen(self):
        r = self.client.post(reverse('producto_variantes', args=[self.polo.pk]), {'atributos': 'Talla: S, M'})
        self.assertRedirects(r, reverse('producto_variantes', args=[self.polo.pk]))
        self.assertContains(self.client.get(reverse('producto_variantes', args=[self.polo.pk])), 'POLO-S')
        r = self.client.get(reverse('producto_editar', args=[self.polo.pk]))
        self.assertContains(r, 'Plantilla de 2 variantes')
        # imagen: se valida el contenido, no la extensión
        datos = {f: v for f, v in r.context['form'].initial.items() if v is not None and not isinstance(v, list)}
        datos.update({'clase': 'MERCADERIA', 'codigo': 'POLO', 'nombre': 'Polo algodón', 'precio_venta': '39.90',
                      'imagen_archivo': SimpleUploadedFile('foto.png', b'no es imagen')})
        r = self.client.post(reverse('producto_editar', args=[self.polo.pk]), datos)
        self.assertContains(r, 'PNG, JPG, GIF o WEBP')
        datos['imagen_archivo'] = SimpleUploadedFile('foto.png', PNG)
        r = self.client.post(reverse('producto_editar', args=[self.polo.pk]), datos)
        self.assertRedirects(r, reverse('productos'))
        self.polo.refresh_from_db()
        self.assertTrue(self.polo.imagen_id)
        r = self.client.get(reverse('producto_imagen', args=[self.polo.pk]))
        self.assertEqual((r['Content-Type'], r.content), ('image/png', PNG))
        # la API expone variantes e imagen
        _, clave = ApiToken.crear(self.user, 'Tienda')
        r = self.client.get(f'/api/v1/productos/{self.polo.pk}/', HTTP_AUTHORIZATION=f'Bearer {clave}').json()
        self.assertEqual((len(r['variantes']), r['imagen']), (2, f'/api/v1/productos/{self.polo.pk}/imagen/'))
        r = self.client.get(f'/api/v1/productos/{self.polo.pk}/imagen/', HTTP_AUTHORIZATION=f'Bearer {clave}')
        self.assertEqual(r.content, PNG)
