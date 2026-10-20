"""Solicitud de pedido (v1.39): orígenes (área, MRP, reposición, requerimiento, mantenimiento), liberación por
niveles, presupuesto, bandeja de Compras (OC, contrato marco, licitación, parcialidades), servicios con conformidad,
trazabilidad y reportes. Un escenario por clase."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contabilidad.models import CentroCosto, CuentaContable
from core.models import Empresa, PerfilUsuario, Producto, Tercero
from core.modulos import GRUPOS

from . import solped as srv
from core.models import ReglaAprobacion

from .models import ContratoMarco, LineaSolicitud, OrdenCompra, OrdenCompraItem, SolicitudPedido

D = Decimal
HOY = timezone.localdate()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_sp', 'a@a.pe', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.centro = CentroCosto.objects.create(codigo='MNT', nombre='Mantenimiento', tipo='PRODUCCION')
        self.prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        self.repuesto = Producto.objects.create(codigo='SU-901', nombre='Rodamiento 6205', clase='SUMINISTRO',
                                                proveedor=self.prov, precio_compra=D('25'))
        grupo = Group.objects.get_or_create(name=GRUPOS['requerimientos'])[0]
        self.jefe = self.usuario('jefe', ['requerimientos.solped', 'requerimientos.liberar_solped'], grupo)
        self.area = self.usuario('area', ['requerimientos.solped'], grupo, jefe=self.jefe)

    def usuario(self, nombre, acciones, grupo, jefe=None):
        u = User.objects.create_user(nombre, password='x', first_name=nombre.upper())
        u.groups.add(grupo)
        PerfilUsuario.objects.create(usuario=u, acciones=acciones, jefe=jefe)
        return u

    def solicitud(self, cantidad='10', precio='0', usuario=None, enviar=True, **cabecera):
        datos = {'centro_costo': self.centro, 'motivo': 'Mantenimiento preventivo de la envasadora', **cabecera}
        sp = srv.crear(datos, [{'producto': self.repuesto, 'cantidad': D(cantidad), 'precio_estimado': D(precio)}],
                       usuario or self.area)
        if enviar:
            srv.enviar(sp, usuario or self.area)
        return sp


class LiberacionPorNivelesTest(Base):
    """Jefe directo siempre; responsable del centro desde S/ 2,000; gerente desde S/ 20,000; nivel adicional para
    urgentes. Secuencial y nadie libera su propia solicitud."""

    def test_niveles_secuenciales(self):
        responsable = User.objects.create_user('resp', password='x')
        gerente = User.objects.create_user('gerente', password='x')
        self.centro.responsable_usuario = responsable
        self.centro.save()
        ReglaAprobacion.objects.create(documento='SOLPED', nivel=1, tipo='JEFE')
        ReglaAprobacion.objects.create(documento='SOLPED', nivel=2, tipo='RESPONSABLE_CC', monto_desde=D('2000'))
        ReglaAprobacion.objects.create(documento='SOLPED', nivel=3, tipo='USUARIO', aprobador=gerente, monto_desde=D('20000'))
        ReglaAprobacion.objects.create(documento='SOLPED', nivel=4, tipo='USUARIO', aprobador=gerente, aplica_a='URGENTE')
        sp = self.solicitud('100')  # 100 × 25 = S/ 2,500
        self.assertEqual(sp.monto, D('2500.00'))
        self.assertEqual([n['usuario'] for n in sp.niveles], [self.jefe.pk, responsable.pk])
        self.assertTrue(sp.numero.startswith('SP01-'))
        self.assertFalse(srv.puede_liberar(self.area, sp))  # nadie libera lo suyo
        self.assertFalse(srv.puede_liberar(responsable, sp))  # aún no es su nivel
        srv.liberar(sp, self.jefe)
        self.assertEqual((sp.estado, sp.nivel_aprobado), ('ENVIADA', 1))
        with self.assertRaises(srv.ErrorSolped):
            srv.liberar(sp, self.jefe)
        srv.liberar(sp, responsable)
        self.assertEqual(sp.estado, 'LIBERADA')
        self.assertIsNotNone(sp.liberada_en)
        # urgente y de S/ 25,000: jefe, responsable y el gerente (una sola vez aunque aplique en dos niveles)
        grande = self.solicitud('1000', urgente=True)
        self.assertEqual([n['usuario'] for n in grande.niveles], [self.jefe.pk, responsable.pk, gerente.pk])
        # el responsable del centro que solicita no se libera a sí mismo: su nivel se salta
        PerfilUsuario.objects.create(usuario=responsable, acciones=['requerimientos.solped'], jefe=self.jefe)
        propia = self.solicitud('100', usuario=responsable)
        self.assertEqual([n['usuario'] for n in propia.niveles], [self.jefe.pk])


class ObservarRechazarAnularTest(Base):
    """Observar devuelve al solicitante (editable) con el comentario; rechazar y anular exigen motivo."""

    def test_observar_y_rechazar(self):
        sp = self.solicitud()
        self.assertEqual(sp.niveles[0]['nombre'], 'Liberador de solicitudes')  # sin reglas
        with self.assertRaises(srv.ErrorSolped):
            srv.observar(sp, self.jefe, 'no')
        srv.observar(sp, self.jefe, 'Indique la marca del rodamiento')
        self.assertTrue(sp.editable)
        self.client.force_login(self.area)
        self.assertContains(self.client.get(reverse('solicitudes:editar', args=[sp.pk])), 'Indique la marca')
        srv.enviar(sp, self.area)
        with self.assertRaises(srv.ErrorSolped):
            srv.rechazar(sp, self.jefe, 'corto')
        srv.rechazar(sp, self.jefe, 'No hay presupuesto este mes')
        self.assertEqual(sp.estado, 'RECHAZADA')
        otra = self.solicitud()
        with self.assertRaises(srv.ErrorSolped):
            srv.anular(otra, self.area, 'x')
        srv.anular(otra, self.area, 'Ya no se necesita el repuesto')
        self.assertEqual(otra.estado, 'ANULADA')


class BandejaParcialidadesTest(Base):
    """Bandeja de Compras: OC por proveedor y centro, parcialidades, anulación de la OC, cierre de líneas y
    trazabilidad hasta la factura."""

    def test_conversion_y_parcialidades(self):
        sp = self.solicitud('10')
        srv.liberar(sp, self.jefe)
        linea = sp.lineas.get()
        self.assertEqual(srv.lineas_abiertas(), [linea])
        oc = srv.a_orden_compra([linea.pk], self.admin)[0]
        item = oc.items.get()
        self.assertEqual((oc.tercero, oc.centro_costo, item.cantidad, item.precio_unitario, item.linea_solped),
                         (self.prov, self.centro, D('10'), D('25'), linea))
        sp.refresh_from_db()
        self.assertEqual(sp.estado, 'PEDIDA')
        # pedir solo 6: queda parcial con 4 en la bandeja
        item.cantidad = D('6')
        item.save()
        srv.actualizar_estados([sp])
        sp.refresh_from_db()
        self.assertEqual((sp.estado, sp.lineas.get().pendiente), ('PARCIAL', D('4')))
        # anular la OC devuelve todo a la bandeja
        self.client.force_login(self.admin)
        self.client.post(reverse('compras:oc_estado', args=[oc.pk]), {'estado': 'ANULADO'})
        sp.refresh_from_db()
        self.assertEqual((sp.estado, sp.lineas.get().pendiente), ('LIBERADA', D('10')))
        # Compras cierra lo que ya no se pedirá
        srv.cerrar_lineas(sp, [linea.pk], self.admin)
        sp.refresh_from_db()
        self.assertEqual(sp.estado, 'CERRADA')
        self.assertEqual(srv.lineas_abiertas(), [])
        r = self.client.get(reverse('solicitudes:detalle', args=[sp.pk]))
        self.assertContains(r, oc.numero)


class ContratoYLicitacionTest(Base):
    """Pedido contra contrato marco (precio pactado) y licitación cuyas OC adjudicadas quedan vinculadas."""

    def test_contrato_y_licitacion(self):
        contrato = ContratoMarco.objects.create(numero='CM-901', proveedor=self.prov, fecha_inicio=HOY,
                                                fecha_fin=HOY + timedelta(days=90))
        contrato.lineas.create(producto=self.repuesto, precio_unitario=D('22'))
        sp = self.solicitud('5')
        srv.liberar(sp, self.jefe)
        oc = srv.a_orden_compra([sp.lineas.get().pk], self.admin, contrato=contrato)[0]
        self.assertEqual((oc.contrato, oc.items.get().precio_unitario), (contrato, D('22')))
        # licitación
        otra = self.solicitud('8')
        srv.liberar(otra, self.jefe)
        lic = srv.a_licitacion([otra.lineas.get().pk], self.admin, 'Rodamientos')
        self.assertEqual(srv.lineas_abiertas(), [])  # en licitación abierta: no se pide dos veces
        from .abastecimiento import adjudicar, registrar_oferta
        oferta = registrar_oferta(lic, self.prov, {lic.lineas.get().pk: D('21')}, {})
        ordenes = adjudicar(lic, {lic.lineas.get().pk: oferta.pk}, self.admin)
        self.assertEqual(ordenes[0].items.get().linea_solped, otra.lineas.get())
        otra.refresh_from_db()
        self.assertEqual(otra.estado, 'PEDIDA')


class OrigenMrpTest(Base):
    """MRP: lo que tiene contrato marco va directo a OC; lo demás a una solicitud liberada si ninguna regla aplica
    (y por liberar si una regla aplica). Lo solicitado cuenta como entrada en la siguiente corrida."""

    def test_mrp_crea_solicitud(self):
        from produccion.models import CorridaMRP, PropuestaMRP
        from produccion.servicios import ejecutar_mrp
        con_contrato = Producto.objects.create(codigo='MP-902', nombre='Azúcar', clase='MATERIA_PRIMA',
                                               proveedor=self.prov, precio_compra=D('3'))
        contrato = ContratoMarco.objects.create(numero='CM-902', proveedor=self.prov, fecha_inicio=HOY,
                                                fecha_fin=HOY + timedelta(days=90))
        contrato.lineas.create(producto=con_contrato, precio_unitario=D('2.80'))
        corrida = CorridaMRP.objects.create(horizonte=HOY + timedelta(days=30))
        props = [PropuestaMRP.objects.create(corrida=corrida, producto=p, tipo='COMPRAR', cantidad=D('50'),
                                             fecha_necesidad=HOY + timedelta(days=5), fecha_inicio=HOY,
                                             proveedor=self.prov) for p in (self.repuesto, con_contrato)]
        sp, ordenes = srv.desde_mrp(props, self.admin, self.centro)
        self.assertEqual((sp.origen, sp.estado, sp.lineas.get().producto), ('MRP', 'LIBERADA', self.repuesto))
        self.assertEqual((ordenes[0].contrato, ordenes[0].items.get().producto), (contrato, con_contrato))
        self.assertTrue(all(p.convertida for p in PropuestaMRP.objects.filter(pk__in=[x.pk for x in props])))
        with self.assertRaises(srv.ErrorSolped):  # ya convertidas
            srv.desde_mrp(PropuestaMRP.objects.filter(pk__in=[x.pk for x in props]), self.admin, self.centro)
        # con una regla que aplica, la del MRP queda por liberar
        ReglaAprobacion.objects.create(documento='SOLPED', nivel=1, tipo='USUARIO', aprobador=self.jefe)
        p3 = PropuestaMRP.objects.create(corrida=corrida, producto=self.repuesto, tipo='COMPRAR', cantidad=D('5'),
                                         fecha_necesidad=HOY, fecha_inicio=HOY)
        sp2, _ = srv.desde_mrp([p3], self.admin, self.centro)
        self.assertEqual(sp2.estado, 'ENVIADA')
        # la demanda de 50 + 5 ya está cubierta por lo solicitado: el MRP no la vuelve a proponer
        from produccion.models import PlanDemanda
        self.repuesto.tipo_planificacion = 'MRP'
        self.repuesto.save()
        PlanDemanda.objects.create(producto=self.repuesto, cantidad=D('50'), fecha=HOY + timedelta(days=5))
        nueva = ejecutar_mrp(HOY + timedelta(days=30))
        self.assertFalse(nueva.propuestas.filter(producto=self.repuesto).exists())


class ReposicionTest(Base):
    """Sugerencia de compra -> una solicitud por proveedor habitual; lo solicitado cuenta como en camino."""

    def test_reposicion(self):
        from core.inventario import sugerencias_compra
        self.repuesto.punto_reorden, self.repuesto.stock_maximo = D('10'), D('30')
        self.repuesto.save()
        filas = [f for f in sugerencias_compra() if f['p'] == self.repuesto]
        self.assertEqual(filas[0]['cantidad'], D('30'))
        self.client.force_login(self.admin)
        r = self.client.post(reverse('inv_reposicion'), {'producto': [self.repuesto.pk],
                                                         'centro_costo': self.centro.pk})
        self.assertRedirects(r, reverse('inv_reposicion'))
        sp = SolicitudPedido.objects.get(origen='REPOSICION')
        self.assertEqual((sp.estado, sp.lineas.get().cantidad, sp.lineas.get().proveedor_sugerido),
                         ('ENVIADA', D('30'), self.prov))
        self.assertEqual([f for f in sugerencias_compra() if f['p'] == self.repuesto], [])


class PresupuestoTest(Base):
    """Control de presupuesto: avisa o bloquea si la solicitud supera lo disponible del centro y la cuenta."""

    def test_avisa_y_bloquea(self):
        from contabilidad.models import Presupuesto
        cuenta = CuentaContable.objects.get(codigo='6343')
        pres = Presupuesto.objects.create(anio=HOY.year, estado='APROBADO')
        pres.lineas.create(cuenta=cuenta, centro_costo=self.centro, **{f'm{m:02d}': D('100') for m in range(1, 13)})
        sp = srv.crear({'centro_costo': self.centro, 'motivo': 'Servicio de calibración', 'tipo': 'SERVICIO'},
                       [{'producto': None, 'descripcion': 'Calibración de balanzas', 'unidad': 'ZZ',
                         'cantidad': D('1'), 'precio_estimado': D('1500'), 'cuenta_contable': cuenta}], self.area)
        avisos = srv.control_presupuesto(sp)
        self.assertEqual(len(avisos), 1)
        self.assertIn('presupuesto del año S/ 1,200.00', avisos[0])
        srv.enviar(sp, self.area)
        self.assertIn('supera lo disponible', sp.aviso_presupuesto)
        Empresa.objects.update(presupuesto_solped='BLOQUEAR')
        otra = srv.crear({'centro_costo': self.centro, 'motivo': 'Otra calibración', 'tipo': 'SERVICIO'},
                         [{'producto': None, 'descripcion': 'Calibración', 'unidad': 'ZZ', 'cantidad': D('1'),
                           'precio_estimado': D('100'), 'cuenta_contable': cuenta}], self.area)
        with self.assertRaisesMessage(srv.ErrorSolped, 'Supera el presupuesto'):  # comprometido 1,500 > 1,200
            srv.enviar(otra, self.area)


class ServicioConConformidadTest(Base):
    """Servicio (texto libre, unidad mes): la factura de su OC exige la hoja de entrada (conformidad)."""

    def test_conformidad(self):
        sp = srv.crear({'centro_costo': self.centro, 'motivo': 'Limpieza industrial', 'tipo': 'SERVICIO'},
                       [{'producto': None, 'descripcion': 'Limpieza de planta', 'unidad': 'MON', 'cantidad': D('1'),
                         'precio_estimado': D('3000'),
                         'cuenta_contable': CuentaContable.objects.get(codigo='6343')}], self.area)
        srv.enviar(sp, self.area)
        srv.liberar(sp, self.jefe)
        with self.assertRaises(srv.ErrorSolped):  # sin proveedor sugerido: hay que elegirlo
            srv.a_orden_compra([sp.lineas.get().pk], self.admin)
        oc = srv.a_orden_compra([sp.lineas.get().pk], self.admin, proveedor=self.prov)[0]
        self.assertTrue(srv.exige_conformidad(oc))
        oc.estado = 'APROBADO'
        oc.save()
        from .forms import CompraForm
        datos = {'tipo_comprobante': '01', 'serie': 'F001', 'numero': '9901', 'tercero': self.prov.pk,
                 'fecha_emision': HOY, 'clasificacion': 'SERVICIO', 'forma_pago': 'CONTADO', 'moneda': 'PEN',
                 'tipo_cambio': '1', 'tipo_operacion': 'GRAVADA', 'detraccion_pct': '0', 'retencion_pct': '0',
                 'percepcion_pct': '0', 'icbper': '0', 'orden_compra': oc.pk, 'centro_costo': self.centro.pk}
        self.assertIn('conformidad del servicio', str(CompraForm(data=datos).errors.get('orden_compra')))
        self.client.force_login(self.admin)
        self.client.post(reverse('compras:conformidad', args=[oc.pk]), {
            'fecha': HOY.isoformat(), 'descripcion': 'Limpieza de octubre conforme', 'porcentaje': '100'})
        self.assertTrue(oc.conformidades.exists())
        self.assertNotIn('orden_compra', CompraForm(data=datos).errors)
        self.assertContains(self.client.get(reverse('compras:oc_detalle', args=[oc.pk])), sp.numero)


class MantenimientoTest(Base):
    """Desde una orden de mantenimiento: repuestos sin stock y el servicio externo, con la orden vinculada."""

    def test_desde_ot(self):
        from core.models import Almacen
        from produccion.models import Equipo, OrdenMantenimiento
        equipo = Equipo.objects.create(codigo='EQ-901', nombre='Envasadora')
        ot = OrdenMantenimiento.objects.create(numero='OT-901', equipo=equipo, descripcion='Cambio de rodamientos',
                                               proveedor=self.prov)
        ot.repuestos.create(producto=self.repuesto, cantidad=D('2'), almacen=Almacen.principal())
        self.client.force_login(self.admin)
        r = self.client.get(reverse('solicitudes:nueva') + f'?ot={ot.pk}')
        self.assertContains(r, 'Servicio de mantenimiento: Cambio de rodamientos')
        cabecera, lineas = srv.iniciales_mantenimiento(ot)
        self.assertEqual([(l['producto'], l['cantidad']) for l in lineas], [(self.repuesto, D('2')), (None, D('1'))])
        datos = {'fecha': HOY.isoformat(), 'fecha_requerida': '', 'centro_costo': self.centro.pk, 'tipo': 'MATERIAL',
                 'motivo': 'OT-901', 'origen_ot': ot.pk, 'accion': 'guardar', 'ln-TOTAL_FORMS': '1',
                 'ln-INITIAL_FORMS': '0', 'ln-MIN_NUM_FORMS': '1', 'ln-MAX_NUM_FORMS': '1000',
                 'ln-0-producto': self.repuesto.pk, 'ln-0-descripcion': '', 'ln-0-unidad': 'NIU', 'ln-0-cantidad': '2',
                 'ln-0-precio_estimado': '', 'ln-0-cuenta_contable': '', 'ln-0-proveedor_sugerido': ''}
        self.client.post(reverse('solicitudes:nueva'), datos)
        sp = SolicitudPedido.objects.get(orden_mantenimiento=ot)
        self.assertEqual((sp.origen, sp.lineas.get().descripcion), ('MANTENIMIENTO', self.repuesto.nombre))
        self.assertContains(self.client.get(reverse('manufactura:orden_mant', args=[ot.pk])), 'Solicitudes de pedido')


class PantallasYPermisosTest(Base):
    """Pantallas; el área registra sin el módulo Compras y solo ve lo suyo; Compras convierte desde la bandeja."""

    def test_pantallas(self):
        self.client.force_login(self.area)
        datos = {'fecha': HOY.isoformat(), 'fecha_requerida': (HOY + timedelta(days=7)).isoformat(),
                 'centro_costo': self.centro.pk, 'tipo': 'MATERIAL', 'motivo': 'Repuestos de la envasadora',
                 'accion': 'enviar', 'ln-TOTAL_FORMS': '1', 'ln-INITIAL_FORMS': '0', 'ln-MIN_NUM_FORMS': '1',
                 'ln-MAX_NUM_FORMS': '1000', 'ln-0-producto': self.repuesto.pk, 'ln-0-descripcion': '',
                 'ln-0-unidad': 'NIU', 'ln-0-cantidad': '4', 'ln-0-precio_estimado': '',
                 'ln-0-cuenta_contable': '', 'ln-0-proveedor_sugerido': ''}
        r = self.client.post(reverse('solicitudes:nueva'), datos)
        sp = SolicitudPedido.objects.get(motivo='Repuestos de la envasadora')
        self.assertRedirects(r, reverse('solicitudes:detalle', args=[sp.pk]))
        self.assertEqual((sp.estado, sp.solicitante), ('ENVIADA', self.area))
        self.assertEqual(self.client.get(reverse('compras:solped_bandeja')).status_code, 403)  # sin Compras
        self.assertContains(self.client.get(reverse('solicitudes:lista')), 'Mis solicitudes de pedido')  # su menú
        otro = self.usuario('otra_area', ['requerimientos.solped'], Group.objects.get(name=GRUPOS['requerimientos']))
        self.client.force_login(otro)
        self.assertRedirects(self.client.get(reverse('solicitudes:detalle', args=[sp.pk])),
                             reverse('solicitudes:lista'))
        sin_permiso = self.usuario('solo_req', ['requerimientos.solicitar'],
                                   Group.objects.get(name=GRUPOS['requerimientos']))
        self.client.force_login(sin_permiso)
        self.assertEqual(self.client.get(reverse('solicitudes:nueva')).status_code, 403)
        sin_modulo = User.objects.create_user('ventas_sp', password='x')
        self.client.force_login(sin_modulo)
        self.assertEqual(self.client.get(reverse('solicitudes:lista')).status_code, 403)
        self.client.force_login(self.jefe)
        self.assertContains(self.client.get(reverse('solicitudes:movil')), sp.numero)
        self.client.post(reverse('solicitudes:liberar', args=[sp.pk]), {'volver': reverse('solicitudes:movil')})
        sp.refresh_from_db()
        self.assertEqual(sp.estado, 'LIBERADA')
        self.client.force_login(self.admin)
        for url in [reverse('solicitudes:lista') + '?vista=todas', reverse('solicitudes:lista') + '?vista=liberar',
                    reverse('solicitudes:detalle', args=[sp.pk]), reverse('compras:solped_bandeja'),
                    reverse('compras:solped_bandeja') + '?agrupar=producto',
                    reverse('aprobaciones_reglas') + '?documento=SOLPED', reverse('compras:solped_reportes'), reverse('solicitudes:nueva'), reverse('inv_reposicion'),
                    reverse('compras:solped_reportes') + '?formato=excel']:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.post(reverse('compras:solped_bandeja'), {'linea': [sp.lineas.get().pk], 'convertir': 'oc'})
        oc = OrdenCompra.objects.get(items__linea_solped__solicitud=sp)
        self.assertRedirects(r, reverse('compras:oc_detalle', args=[oc.pk]))
        self.assertRedirects(self.client.get(reverse('compras:solped_reglas')),
                             reverse('aprobaciones_reglas') + '?documento=SOLPED')
        datos_regla = {'documento': 'SOLPED', 'nivel': '1', 'tipo': 'USUARIO', 'aprobador': '', 'puesto': '',
                       'niveles_arriba': '2', 'monto_desde': '0', 'centro_costo': '', 'aplica_a': '', 'activo': 'on'}
        url_reglas = reverse('aprobaciones_reglas') + '?documento=SOLPED'
        self.assertContains(self.client.post(url_reglas, datos_regla), 'Elija el usuario')
        datos_regla['aprobador'] = self.jefe.pk
        self.client.post(url_reglas, datos_regla)
        self.assertEqual(ReglaAprobacion.objects.filter(documento='SOLPED').count(), 1)
        # reportes: tiempos de liberación y de liberada a OC
        d = srv.reporte(HOY - timedelta(days=1), HOY)
        self.assertEqual(d['total'], 1)
        self.assertEqual(d['dias_a_oc'], 0)
        self.assertIsNotNone(d['horas_liberacion'])
        self.assertEqual(LineaSolicitud.objects.get(solicitud=sp).pendiente, D('0'))
        self.assertTrue(OrdenCompraItem.objects.filter(linea_solped__solicitud=sp).exists())
