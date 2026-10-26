"""v1.87 (pruebas negativas del 26/10/2026): COM-18, COM-19, VEN-15, VEN-16, VEN-17, CON-15, CON-16, TER-01, REQ-02,
REQ-03, SEG-08, MAN-14 y MAN-15."""
import json
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import ApiToken, Empresa, PerfilUsuario, Tercero
from core.modulos import GRUPOS
from produccion.tests_reportes_produccion import Base as BaseProduccion

HOY = timezone.localdate()


class ConDemo(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t187b', 't@t.com', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.user)


class ComprobantesTest(ConDemo):
    def _venta(self, **cambios):
        _, clave = ApiToken.crear(self.user, 'tienda', solo_lectura=False)
        cache.clear()
        cliente = Tercero.objects.filter(tipo__in=['CLIENTE', 'AMBOS'], tipo_doc='6').first()
        datos = {'tercero': cliente.pk, 'tipo_comprobante': '01', 'serie': 'F001', 'descontar_stock': False,
                 'fecha_emision': HOY.isoformat(),
                 'items': [{'descripcion': 'Servicio', 'cantidad': 1, 'precio_unitario': 100, 'afectacion': 'GRAVADA'}]}
        items = cambios.pop('items', None)
        datos.update(cambios)
        if items:
            datos['items'] = [{**datos['items'][0], **items}]
        return self.client.post('/api/v1/ventas/', json.dumps(datos), content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {clave}')

    def test_montos_y_fechas_en_el_servidor(self):
        """VEN-15, VEN-16 y COM-19 (la API usa las mismas reglas que la pantalla)."""
        r = self._venta(items={'cantidad': -3})
        self.assertEqual(r.status_code, 422)
        self.assertIn('cantidad debe ser mayor a cero', json.dumps(r.json(), ensure_ascii=False))
        r = self._venta(items={'precio_unitario': 0})
        self.assertIn('precio debe ser mayor a cero', json.dumps(r.json(), ensure_ascii=False))
        r = self._venta(fecha_emision=(HOY + timedelta(days=80)).isoformat())
        self.assertIn('no puede ser posterior a hoy', json.dumps(r.json(), ensure_ascii=False))
        r = self._venta(fecha_vencimiento=(HOY - timedelta(days=10)).isoformat(), forma_pago='CREDITO')
        self.assertIn('vencimiento', json.dumps(r.json(), ensure_ascii=False))
        r = self._venta(fecha_vencimiento=(HOY - timedelta(days=10)).isoformat())  # al contado también
        self.assertIn('no puede ser anterior a la emisión', json.dumps(r.json(), ensure_ascii=False))
        self.assertEqual(self._venta().status_code, 201)

    def test_compra_con_cantidad_negativa(self):
        """COM-18: mismas reglas en compras."""
        from compras.models import Compra
        from core.utils import reglas_comprobante
        compra = Compra(tipo_comprobante='01', fecha_emision=HOY)
        form = SimpleNamespace(instance=compra, cleaned_data={'fecha_emision': HOY, 'tipo_operacion': 'GRAVADA'})
        formset = SimpleNamespace(forms=[SimpleNamespace(cleaned_data={'cantidad': D('-5'), 'precio_unitario': D('100')}),
                                         SimpleNamespace(cleaned_data={'cantidad': D('1'), 'precio_unitario': D('-118')})])
        errores = reglas_comprobante(form, formset)
        self.assertEqual(len(errores), 2)

    def test_nota_de_credito_no_supera_el_comprobante(self):
        """VEN-17."""
        from core.utils import _nota_excede
        from ventas.models import Venta
        f = Venta.objects.filter(tipo_comprobante='01', estado='REGISTRADO').first()
        nc = Venta(tipo_comprobante='07', doc_referencia=f, tercero=f.tercero, total=f.total * 10, motivo_nota='01')
        self.assertIn('supera', _nota_excede(nc))
        nc.total = f.total / 2
        self.assertIn('por el total pendiente', _nota_excede(nc))
        nc.motivo_nota = '07'
        self.assertEqual(_nota_excede(nc), '')

    def test_documento_con_asiento_en_periodo_cerrado(self):
        """CON-15: no se edita; se corrige con nota."""
        from compras.models import Compra
        from contabilidad.centralizar import centralizar_periodo
        from contabilidad.models import PeriodoContable
        from core.utils import reglas_comprobante
        c = Compra.objects.filter(estado='REGISTRADO', es_saldo_inicial=False).exclude(tipo_comprobante='07').first()
        centralizar_periodo(c.periodo)
        PeriodoContable.objects.update_or_create(periodo=c.periodo, defaults={'cerrado': True})
        form = SimpleNamespace(instance=c, cleaned_data={'fecha_emision': c.fecha_emision})
        self.assertIn('periodo cerrado', ' '.join(reglas_comprobante(form, SimpleNamespace(forms=[]))))

    def test_asiento_manual_dice_por_que_no_se_graba(self):
        """CON-16."""
        from contabilidad.models import CuentaContable
        a, b = CuentaContable.objects.filter(imputable=True, codigo__startswith='10')[:2]
        datos = {'fecha': HOY.isoformat(), 'libro': '05', 'glosa': 'Prueba', 'moneda': 'PEN', 'tipo_cambio': '1',
                 'norma': 'AMBOS', 'descripcion_sustento': 'Acta',
                 'sustento': SimpleUploadedFile('acta.pdf', b'%PDF-1.4 prueba', content_type='application/pdf'),
                 'lineas-TOTAL_FORMS': '2', 'lineas-INITIAL_FORMS': '0', 'lineas-MIN_NUM_FORMS': '0',
                 'lineas-MAX_NUM_FORMS': '1000', 'lineas-0-cuenta': a.pk, 'lineas-0-debe': '100',
                 'lineas-1-cuenta': b.pk, 'lineas-1-haber': '50'}
        r = self.client.post(reverse('contabilidad:asiento_nuevo'), datos)
        self.assertContains(r, 'No se grabó el asiento')
        self.assertContains(r, 'diferencia S/ 50.00')

    def test_contacto_con_documento_repetido(self):
        """TER-01."""
        from core.forms import TerceroForm
        t = Tercero.objects.filter(tipo_doc='6').first()
        f = TerceroForm({'tipo': 'CLIENTE', 'tipo_doc': '6', 'numero_doc': t.numero_doc, 'nombre': 'DUPLICADO DEMO',
                         'activo': 'on', 'dias_credito': '0', 'limite_credito': '0'})
        self.assertFalse(f.is_valid())
        self.assertIn('Ya existe', str(f.errors['numero_doc']))


class SueldosTest(ConDemo):
    def test_sueldos_solo_rrhh(self):
        """SEG-08: sin un permiso de RR. HH. no se ven sueldos, cuentas ni planillas."""
        from core.politicas import ver_sueldos
        from planillas.forms import TrabajadorForm
        jefe = User.objects.create_user('jefe.logistica', password='x')
        jefe.groups.add(Group.objects.get_or_create(name=GRUPOS['planillas'])[0])
        PerfilUsuario.objects.create(usuario=jefe, acciones=['empleados.registrar'])
        self.assertFalse(ver_sueldos(jefe))
        self.assertNotIn('cuenta_sueldo', TrabajadorForm(ver_sueldos=False).fields)
        rrhh = User.objects.create_user('rrhh', password='x')
        rrhh.groups.add(Group.objects.get_or_create(name=GRUPOS['planillas'])[0])
        PerfilUsuario.objects.create(usuario=rrhh, acciones=['planillas.ver_sueldos'])
        self.assertTrue(ver_sueldos(rrhh))
        self.client.force_login(jefe)
        self.assertNotEqual(self.client.get(reverse('planillas:lista')).status_code, 200)


class RequerimientosSegregacionTest(ConDemo):
    def setUp(self):
        super().setUp()
        from contabilidad.models import CentroCosto, CuentaContable
        from core.models import Almacen, Producto
        from inventario.models import RequerimientoInterno
        self.almacen = Almacen.principal()
        cc = CentroCosto.objects.create(codigo='MNT', nombre='Mantenimiento', tipo='PRODUCCION')
        p = Producto.objects.create(nombre='Guantes', clase='SUMINISTRO', precio_compra=D('5'))
        p.mover_stock(D('6'), 'Saldo', costo=D('5'), almacen=self.almacen)
        self.otro = User.objects.create_superuser('gerente', 'g@x.pe', 'x')
        self.almacenero = User.objects.create_user('almacenero', password='x')
        self.almacenero.groups.add(Group.objects.get_or_create(name=GRUPOS['inventario'])[0])
        self.req = RequerimientoInterno.objects.create(
            centro_costo=cc, almacen=self.almacen, motivo='Hornos', solicitante=self.user,
            cuenta_gasto=CuentaContable.objects.get(codigo='6343'))
        self.req.items.create(producto=p, cantidad=D('2'))

    def test_solicitante_no_aprueba_ni_atiende(self):
        """REQ-03 y REQ-02."""
        from inventario import requerimientos as srv
        srv.enviar(self.req, self.user)
        with self.assertRaisesMessage(srv.ErrorRequerimiento, 'tampoco el administrador'):
            srv.aprobar(self.req, self.user)
        srv.aprobar(self.req, self.otro)
        item = self.req.items.get()
        with self.assertRaisesMessage(srv.ErrorRequerimiento, 'El solicitante no puede atender'):
            srv.atender(self.req, self.user, {item.pk: D('2')})
        with self.assertRaisesMessage(srv.ErrorRequerimiento, 'El solicitante no puede atender'):
            srv.atender(self.req, self.otro, {item.pk: D('2')})  # quien aprobó tampoco
        op = srv.atender(self.req, self.almacenero, {item.pk: D('2')}, recibe='Luis Ramos')
        self.assertIn('Recibió: Luis Ramos', op.glosa)


class ConsumoOrdenTest(BaseProduccion):
    def test_tolerancia_pide_aprobacion_de_otro_usuario(self):
        """MAN-14: con ± 5 %, 55 kg de masa (receta 50) pide motivo y la aprobación de otro usuario."""
        from produccion import consumo, servicios
        Empresa.objects.update(politica_consumo='TOLERANCIA', tolerancia_consumo_pct=D('5'))
        o = self.orden()
        c = o.consumos.get(producto=self.masa)
        h = o.horas.get()
        with self.assertRaisesMessage(servicios.ErrorProduccion, 'indique el motivo'):
            servicios.terminar(o, self.user, D('100'), {c.pk: D('55')}, {h.pk: D('2')})
        with self.assertRaisesMessage(servicios.ErrorProduccion, 'Requiere la aprobación'):
            servicios.terminar(o, self.user, D('100'), {c.pk: D('55')}, {h.pk: D('2')},
                               motivos={c.pk: 'Merma del horno'})
        c.refresh_from_db()
        self.assertEqual((c.aprobacion, c.solicitado), ('POR_APROBAR', D('55')))
        jefe = User.objects.create_user('jefe.produccion', password='x')
        with self.assertRaisesMessage(consumo.ErrorConsumo, 'no lo aprueba'):
            consumo.resolver(c, self.user, True)
        consumo.resolver(c, jefe, True)
        servicios.terminar(o, self.user, D('100'), {c.pk: D('55')}, {h.pk: D('2')})
        o.refresh_from_db()
        self.assertEqual((o.estado, o.consumos.get(producto=self.masa).cantidad_real), ('TERMINADA', D('55')))

    def test_politica_estricta(self):
        from produccion import servicios
        Empresa.objects.update(politica_consumo='ESTRICTO')
        o = self.orden()
        c = o.consumos.get(producto=self.masa)
        servicios.terminar(o, self.user, D('100'), {c.pk: D('70')}, {o.horas.get().pk: D('2')})
        self.assertEqual(o.consumos.get(producto=self.masa).cantidad_real, D('50'))


class SalidaOrdenTest(BaseProduccion):
    def _salida(self, o, producto, cantidad, fecha=HOY, codigo='SAL_OP'):
        from inventario import servicios as inv
        from inventario.models import Operacion, TipoOperacion
        op = Operacion.objects.create(tipo=TipoOperacion.objects.get(codigo=codigo), fecha=fecha,
                                      almacen_origen=self.almacen if codigo == 'SAL_OP' else None,
                                      almacen_destino=self.almacen if codigo == 'DEV_OP' else None,
                                      orden_imputada=o, creado_por=self.user)
        op.items.create(producto=producto, cantidad=D(cantidad))
        return inv.confirmar(op, self.user)

    def test_material_agregado_y_salida_a_la_orden_abierta(self):
        """MAN-14 y MAN-15: lo despachado a la orden no vuelve a salir al terminar y entra a su costo."""
        from core.models import Producto
        from produccion import consumo
        azucar = Producto.objects.create(nombre='Azúcar', unidad='KGM', clase='MATERIA_PRIMA')
        azucar.mover_stock(D('10'), 'Saldo', costo=D('4'), almacen=self.almacen)
        o = self.orden()
        c = consumo.agregar_material(o, azucar, D('2'), 'Sustituto de la masa', self.user)
        self.assertEqual(c.aprobacion, 'APROBADO')  # política libre
        self._salida(o, self.masa, '3')
        masa = o.consumos.get(producto=self.masa)
        self.assertEqual((masa.despachado, masa.cantidad_real), (D('3'), D('53')))
        self.masa.refresh_from_db()
        antes = self.masa.stock
        self.terminar(o, masa='55')  # 55 en total: 3 ya salieron y salen 52
        self.masa.refresh_from_db()
        self.assertEqual(antes - self.masa.stock, D('52'))
        o.refresh_from_db()
        self.assertEqual(o.costo_materiales, D('55') * 6 + D('100') * D('0.10') + 2 * 4)

    def test_salida_a_orden_terminada_recostea(self):
        """MAN-15: la orden terminada en el mes abierto se recostea y se revaloriza su producto."""
        o = self.terminar(self.orden())
        antes = o.costo_unitario
        self._salida(o, self.masa, '10')
        o.refresh_from_db()
        self.assertEqual(o.costo_unitario, (antes * 100 + 60) / 100)
        from core.models import Kardex
        k = Kardex.objects.filter(producto=self.galleta, tipo='ENTRADA').latest('id')
        self.assertEqual(k.costo_unitario, o.costo_unitario)
        from produccion.consumo import error_imputacion
        anterior = (HOY.replace(day=1) - timedelta(days=1))
        self.assertIn('se liquidó', error_imputacion(o, anterior))
