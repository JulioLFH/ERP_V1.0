"""v1.53, correcciones del piloto (etapa 1): formato de números Perú (46, 48), valor de venta con decimales (49),
factura ingresada por la recepción de su OC (50), usuarios del portal fuera de las listas internas y sin aprobar
(51), almacenes del usuario en los reportes (55), solo lectura sin botones de escritura (56) y registro info desde
las compras registradas (58). Un escenario por clase."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from .models import Almacen, PerfilUsuario, Producto, Tercero
from .modulos import GRUPOS

D = Decimal


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v153', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def usuario(self, nombre, modulos, acciones, almacenes=(), pantallas=None):
        u = User.objects.create_user(nombre, password='x')
        for m in modulos:
            u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
        perfil = PerfilUsuario.objects.create(usuario=u, acciones=list(acciones), pantallas=pantallas or {})
        perfil.almacenes.set(almacenes)
        return u


class FormatoNumerosTest(Base):
    def test_punto_decimal_suma_exacta_y_lectura_de_importes(self):
        from .utils import leer_numero
        texto = Template('{% load erp_tags %}{{ a }}|{{ a|floatformat:2 }}|{{ b|sumar:c|money }}').render(
            Context({'a': D('371.6343'), 'b': D('940.59'), 'c': D('0')}))
        self.assertEqual(texto, '371.6343|371.63|940.59')  # antes 371,6343 y «Valor venta 940.00»
        for escrito, valor in (('287,00', '287.00'), ('3,2773', '3.2773'), ('1,110', '1110'), ('1,109.90', '1109.90'),
                               ('1.109,90', '1109.90'), ('287.00', '287.00'), ('', '0')):
            self.assertEqual(leer_numero(escrito), D(valor), escrito)


class IngresadoPorRecepcionTest(Base):
    def test_factura_de_oc_recibida_no_ofrece_ingresar(self):
        from compras.models import Compra, CompraItem, OrdenCompra, OrdenCompraItem
        from inventario import servicios as inv
        from inventario.models import Operacion, TipoOperacion
        from inventario.servicios import operaciones_de
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        p = Producto.objects.create(codigo='MP-1530', nombre='INSUMO OC 153', tipo='BIEN', unidad='NIU')
        oc = OrdenCompra.objects.create(numero='OC01-00001530', tercero=prov, estado='APROBADO')
        OrdenCompraItem.objects.create(documento=oc, producto=p, descripcion='Insumo', cantidad=5,
                                       precio_unitario=D('10'))
        recepcion = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo='REC_COMPRA'),
                                             orden_compra=oc, almacen_destino=Almacen.principal())
        recepcion.items.create(producto=p, cantidad=D('5'), costo_unitario=D('10'))
        inv.confirmar(recepcion, self.admin)
        recepcion.refresh_from_db()
        compra = Compra.objects.create(tercero=prov, tipo_comprobante='01', serie='F900', numero='1',
                                       orden_compra=oc)
        CompraItem.objects.create(documento=compra, producto=p, descripcion='Insumo', cantidad=5,
                                  precio_unitario=D('10'))
        self.assertIn(recepcion, list(operaciones_de(compra)))
        self.client.force_login(self.admin)
        r = self.client.get(reverse('compras:detalle', args=[compra.pk]))
        self.assertContains(r, f'Ingresado por {recepcion.numero}')
        self.assertNotContains(r, 'Ingresar a almacén')


class PortalFueraDeListasTest(Base):
    def test_usuario_del_portal_no_aparece_ni_aprueba(self):
        from proveedores.models import AccesoProveedor

        from . import aprobaciones
        from .permisos import puede
        from .views_aprobaciones import ReglaForm
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        portal = User.objects.create_user('prov.insumos.prueba', password='x')
        AccesoProveedor.objects.create(usuario=portal, tercero=prov)
        interno = User.objects.create_user('jefe.compras', password='x')
        form = ReglaForm()
        opciones = [str(o) for o in form['aprobador'].field.queryset]
        self.assertIn('jefe.compras', opciones)
        self.assertNotIn('prov.insumos.prueba', opciones)
        form = ReglaForm(data={'documento': 'OC', 'nivel': 1, 'tipo': 'USUARIO', 'aprobador': portal.pk,
                               'monto_desde': '0', 'activo': 'on'})
        self.assertIn('aprobador', form.errors)  # tampoco por POST
        self.assertFalse(puede(portal, 'compras.aprobar_oc'))
        niveles = [{'nivel': 1, 'usuario': portal.pk, 'nombre': 'x', 'desde': None}]
        self.assertFalse(aprobaciones.puede_aprobar(portal, 'OC', niveles, 0))
        self.assertTrue(aprobaciones.puede_aprobar(self.admin, 'OC', niveles, 0))
        self.assertEqual(aprobaciones.aprobadores(niveles[0]), [])
        self.assertIsNotNone(interno)


class AlmacenesEnReportesTest(Base):
    def test_usuario_limitado_solo_ve_sus_almacenes(self):
        principal = Almacen.principal()
        pt = Almacen.objects.create(codigo='PT9', nombre='PRODUCTOS TERMINADOS 9', direccion='AV. 1', ubigeo='150101')
        p = Producto.objects.create(codigo='ZZ-153', nombre='PRODUCTO PRUEBA 153', tipo='BIEN', unidad='NIU')
        p.mover_stock(D('10'), 'ingreso principal', costo=D('2'), almacen=principal)
        p.mover_stock(D('4'), 'ingreso PT', costo=D('2'), almacen=pt)
        u = self.usuario('almacen.prueba', ['inventario'], ['inventario.operar'], almacenes=[pt])
        self.client.force_login(u)
        r = self.client.get(reverse('inv_stock'), {'q': 'ZZ-153'})
        self.assertContains(r, 'PRODUCTOS TERMINADOS 9')
        self.assertNotContains(r, f'<th class="num">{principal.nombre}</th>', html=False)
        fila = r.context['page_obj'].object_list[0]
        self.assertEqual(fila['total'], D('4'))  # no los 14 de todos los almacenes
        r = self.client.get(reverse('inventario:reporte_masivo', args=['kardex']),
                            {'ver': '1', 'q': 'ZZ-153', 'almacen': [principal.pk], 'desde': '2000-01-01'})
        self.assertNotIn(principal.pk, [a.pk for a in r.context['almacenes']])
        self.assertTrue(r.context['filas'])
        self.assertTrue(all(f[3] == 'PT9' for f in r.context['filas']))  # pidió el principal: solo ve el suyo
        r = self.client.get(reverse('inv_kardex'), {'producto': p.pk, 'desde': '2000-01-01'})
        self.assertEqual({f['k'].almacen_id for f in r.context['filas']}, {pt.pk})
        # «Ajuste» solo para quien tiene ese permiso
        r = self.client.get(reverse('inv_stock'))
        self.assertEqual([a.pk for a in r.context['almacenes']], [pt.pk])
        self.assertNotContains(r, 'bi-sliders"></i> Ajuste')


class SoloLecturaTest(Base):
    def test_pantalla_de_solo_lectura_oculta_botones(self):
        u = self.usuario('consulta.stock', ['inventario'], ['inventario.operar', 'inventario.ajustar'],
                         pantallas={'inv_stock': 'LECTURA'})
        self.client.force_login(u)
        r = self.client.get(reverse('inv_stock'))
        self.assertContains(r, 'solo-lectura')
        self.assertNotContains(r, 'bi-sliders"></i> Ajuste')  # tiene el permiso, pero la pantalla es de consulta
        self.assertEqual(self.client.post(reverse('inv_stock')).status_code, 403)  # y el servidor bloquea


class RegistroInfoDesdeComprasTest(Base):
    def test_cargar_desde_compras_es_repetible_y_se_ve_en_el_producto(self):
        from compras.models import Compra, CompraItem, RegistroInfo
        prov = Tercero.objects.create(numero_doc='20100070970', nombre='PROVEEDOR 153 SAC', tipo='PROVEEDOR',
                                      tipo_doc='6')
        p = Producto.objects.create(codigo='MP-153', nombre='INSUMO 153', tipo='BIEN', unidad='KGM')
        from datetime import date
        for n, (fecha, precio) in enumerate(((date(2026, 9, 1), '3.20'), (date(2026, 10, 5), '3.45')), 1):
            c = Compra.objects.create(tercero=prov, tipo_comprobante='01', serie='F777', numero=str(n),
                                      fecha_emision=fecha)
            CompraItem.objects.create(documento=c, producto=p, descripcion='INSUMO', cantidad=10, precio_unitario=D(precio))
        RegistroInfo.objects.filter(producto=p).delete()  # como las compras de antes del registro info
        self.client.force_login(self.admin)
        for _ in range(2):  # repetirlo no duplica los contadores
            r = self.client.post(reverse('compras:registros_info'), {'accion': 'cargar'}, follow=True)
            self.assertContains(r, 'cargado desde las compras registradas')
        reg = RegistroInfo.objects.get(producto=p, proveedor=prov)
        self.assertEqual((reg.compras, reg.ultimo_precio, str(reg.ultima_compra), reg.preferido),
                         (2, D('3.4500'), '2026-10-05', True))
        r = self.client.get(reverse('producto_editar', args=[p.pk]))
        self.assertContains(r, 'Proveedores de este producto')
        self.assertContains(r, 'PROVEEDOR 153 SAC')
