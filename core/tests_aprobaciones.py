"""Motor único de aprobación, estructura organizativa y personal (v1.42): niveles de OC y requerimientos, suplencias
y escalamiento, jefe por jerarquía, cese que desactiva el usuario y reasigna, movimientos con fecha efectiva y
remuneración vigente por periodo, autoservicio (vacaciones aprobadas por el jefe), legajo y contratos. Un escenario
por clase."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contabilidad.models import CentroCosto
from planillas.models import Puesto, Trabajador, UnidadOrganizativa

from . import aprobaciones as motor
from .models import Delegacion, Empresa, PerfilUsuario, Producto, ReglaAprobacion, Tercero
from .modulos import GRUPOS

D = Decimal
HOY = timezone.localdate()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_ap', 'a@a.pe', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.centro = CentroCosto.objects.create(codigo='CC-AP', nombre='Planta', tipo='PRODUCCION')

    def usuario(self, nombre, modulos=(), acciones=(), jefe=None):
        u = User.objects.create_user(nombre, password='x', first_name=nombre.upper())
        for m in modulos:
            u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
        PerfilUsuario.objects.create(usuario=u, acciones=list(acciones), jefe=jefe)
        return u

    def trabajador(self, nombre, puesto=None, usuario=None, sueldo='2000', **extra):
        return Trabajador.objects.create(numero_doc=str(40000000 + Trabajador.objects.count() + 500)[:8],
                                         apellido_paterno=nombre, nombres='X', fecha_ingreso=date(2024, 1, 1),
                                         sueldo=D(sueldo), puesto=puesto, usuario=usuario, centro_costo=self.centro,
                                         **extra)

    def oc(self, total='1500', creador=None):
        from compras.models import OrdenCompra, OrdenCompraItem
        prov = Tercero.objects.filter(tipo='PROVEEDOR').first()
        oc = OrdenCompra.objects.create(numero=f'OC01-0000{OrdenCompra.objects.count() + 900}', tercero=prov,
                                        centro_costo=self.centro)
        p = Producto.objects.get(codigo='P001')
        OrdenCompraItem.objects.create(documento=oc, producto=p, descripcion=p.nombre, cantidad=1,
                                       precio_unitario=D(total))
        oc.calcular_totales()
        oc.save()
        if creador:  # quién la registró (como lo deja la bitácora de auditoría)
            from .models import Bitacora
            Bitacora.objects.create(accion='CREAR', modelo=oc._meta.label, objeto_id=str(oc.pk), usuario=creador)
        return oc


class OrdenCompraPorNivelesTest(Base):
    """N.º 25: OC aprobada en cadena (nivel 1 responsable del centro, nivel 2 gerente desde S/ 1,000), con historial,
    rechazo con motivo, bandeja «Mis aprobaciones» y reinicio al editar."""

    def test_niveles_de_oc(self):
        jefe_planta = self.usuario('jplanta')
        gerente = self.usuario('gerente')
        self.centro.responsable_usuario = jefe_planta
        self.centro.save()
        ReglaAprobacion.objects.create(documento='OC', nivel=1, tipo='RESPONSABLE_CC')
        ReglaAprobacion.objects.create(documento='OC', nivel=2, tipo='USUARIO', aprobador=gerente,
                                       monto_desde=D('1000'))
        comprador = self.usuario('comprador', ['compras'], ['compras.oc'])
        oc = self.oc('1500', creador=comprador)  # 1,770 con IGV
        from compras import aprobacion_oc as srv
        self.assertTrue(srv.por_niveles(oc))
        self.assertEqual([n['usuario'] for n in oc.niveles], [jefe_planta.pk, gerente.pk])
        self.assertFalse(srv.puede_aprobar(gerente, oc))  # todavía no es su nivel
        # el jefe de planta no tiene el módulo Compras: aprueba desde «Mis aprobaciones»
        self.client.force_login(jefe_planta)
        r = self.client.get(reverse('aprobaciones_bandeja'))
        self.assertContains(r, oc.numero)
        self.client.post(reverse('aprobaciones_bandeja'), {'doc': 'OC', 'pk': oc.pk, 'accion': 'aprobar'})
        oc.refresh_from_db()
        self.assertEqual((oc.estado, oc.nivel_aprobado), ('PENDIENTE', 1))
        # el gerente la rechaza con motivo: vuelve al comprador y la aprobación empieza de nuevo
        with self.assertRaises(srv.ErrorAprobacionOC):
            srv.rechazar(oc, gerente, 'corto')
        srv.rechazar(oc, gerente, 'Precio alto: pida otra cotización')
        oc.refresh_from_db()
        self.assertEqual((oc.niveles, oc.observacion_aprobacion), ([], 'Precio alto: pida otra cotización'))
        srv.aprobar(oc, jefe_planta)
        srv.aprobar(oc, gerente)
        oc.refresh_from_db()
        self.assertEqual(oc.estado, 'APROBADO')
        self.assertEqual([h.accion for h in motor.historial('OC', oc)],
                         ['APROBADA', 'RECHAZADA', 'APROBADA', 'APROBADA'])
        # una OC chica solo necesita el nivel 1; el comprador no aprueba la suya
        chica = self.oc('100', creador=comprador)
        self.assertEqual([n['usuario'] for n in srv.calcular_niveles(chica)], [jefe_planta.pk])
        self.assertFalse(srv.puede_aprobar(comprador, chica))
        self.client.force_login(self.admin)
        r = self.client.get(reverse('compras:oc_detalle', args=[oc.pk]))
        self.assertContains(r, 'Precio alto')


class SuplenciaYEscalamientoTest(Base):
    """N.º 34: el suplente aprueba mientras dure la delegación; un nivel sin respuesta escala al jefe del
    aprobador."""

    def test_suplencia_y_escalamiento(self):
        director = self.usuario('director')
        titular = self.usuario('titular', jefe=director)
        suplente = self.usuario('suplente')
        nivel = {'nivel': 1, 'usuario': titular.pk, 'nombre': 'TITULAR', 'desde': timezone.now().isoformat()}
        self.assertEqual(motor.aprobadores(nivel), [titular])
        Delegacion.objects.create(titular=titular, suplente=suplente, desde=HOY - timedelta(days=1),
                                  hasta=HOY + timedelta(days=5), motivo='Vacaciones')
        self.assertIn(suplente, motor.aprobadores(nivel))
        self.assertTrue(motor.puede_aprobar(suplente, 'OC', [nivel], 0))
        Empresa.objects.update(dias_escalamiento=3)
        self.assertNotIn(director, motor.aprobadores(nivel))
        nivel['desde'] = (timezone.now() - timedelta(days=4)).isoformat()
        self.assertIn(director, motor.aprobadores(nivel))  # 4 días sin respuesta: escaló
        self.client.force_login(titular)
        r = self.client.post(reverse('aprobaciones_delegaciones'), {
            'titular': titular.pk, 'suplente': titular.pk, 'desde': HOY, 'hasta': HOY, 'motivo': ''})
        self.assertContains(r, 'otra persona')


class RequerimientoPorJefeTest(Base):
    """N.º 26: el requerimiento lo aprueba el jefe del solicitante (por regla), aunque no tenga el permiso
    general; otro aprobador no."""

    def test_jefe_aprueba(self):
        from inventario import requerimientos as srv
        from inventario.models import RequerimientoInterno
        from core.models import Almacen
        ReglaAprobacion.objects.create(documento='REQUERIMIENTO', nivel=1, tipo='JEFE')
        jefe = self.usuario('jefe_area', ['requerimientos'])
        otro = self.usuario('aprobador', ['requerimientos'], ['requerimientos.aprobar'])
        area = self.usuario('area', ['requerimientos'], ['requerimientos.solicitar'], jefe=jefe)
        req = RequerimientoInterno.objects.create(centro_costo=self.centro, almacen=Almacen.principal(),
                                                  motivo='Guantes', solicitante=area)
        req.items.create(producto=Producto.objects.get(codigo='P001'), cantidad=D('1'))
        srv.enviar(req, area)
        self.assertEqual([n['usuario'] for n in req.niveles], [jefe.pk])
        self.assertFalse(srv.puede_aprobar(otro, req))
        self.client.force_login(jefe)
        self.assertContains(self.client.get(reverse('requerimientos:lista')), req.numero)
        self.client.post(reverse('requerimientos:aprobar', args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual(req.estado, 'APROBADO')


class OrganizacionYCeseTest(Base):
    """N.º 31 y 32: jefe = ocupante del puesto superior (salta vacantes); regla «nivel N de la jerarquía»; el cese
    desactiva el usuario, le quita permisos y pasa sus aprobaciones pendientes a su jefe."""

    def test_jerarquia_y_cese(self):
        from planillas import organizacion as org
        gg = UnidadOrganizativa.objects.create(codigo='GG', nombre='Gerencia general', tipo='GERENCIA')
        prod = UnidadOrganizativa.objects.create(codigo='PRD', nombre='Producción', padre=gg,
                                                 centro_costo=self.centro)
        p_gg = Puesto.objects.create(codigo='GG1', nombre='Gerente general', unidad=gg)
        p_jp = Puesto.objects.create(codigo='JP1', nombre='Jefe de planta', unidad=prod, superior=p_gg)
        p_sup = Puesto.objects.create(codigo='SP1', nombre='Supervisor', unidad=prod, superior=p_jp)  # vacante
        p_op = Puesto.objects.create(codigo='OP1', nombre='Operario', unidad=prod, superior=p_sup)
        u_gg, u_jp, u_op = self.usuario('u_gg'), self.usuario('u_jp', ['requerimientos']), self.usuario('u_op')
        self.trabajador('GERENTE', p_gg, u_gg)
        t_jp = self.trabajador('JEFE', p_jp, u_jp)
        t_op = self.trabajador('OPERARIO', p_op, u_op)
        self.assertEqual(org.jefe_de_trabajador(t_op), t_jp)  # el supervisor está vacante: sube
        self.assertEqual(motor._jefe_de(u_op), u_jp)
        ReglaAprobacion.objects.create(documento='VACACION', nivel=1, tipo='JEFE')
        ReglaAprobacion.objects.create(documento='VACACION', nivel=2, tipo='JERARQUIA', niveles_arriba=2)
        v = org.solicitar_vacaciones(t_op, u_op, 'GOCE', HOY + timedelta(days=10), HOY + timedelta(days=14))
        self.assertEqual([n['usuario'] for n in v.niveles], [u_jp.pk, u_gg.pk])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('planillas:organigrama')), 'vacante')
        # cese del jefe de planta: su usuario se desactiva y la vacación pendiente pasa al gerente
        t_jp.fecha_cese, t_jp.motivo_cese = HOY, 'Renuncia'
        t_jp.save()
        u_jp.refresh_from_db()
        self.assertFalse(u_jp.is_active)
        self.assertEqual(list(u_jp.groups.all()), [])
        v.refresh_from_db()
        self.assertEqual(v.niveles[0]['usuario'], u_gg.pk)


class MovimientosDePersonalTest(Base):
    """N.º 35: aumento con fecha efectiva futura, aprobado y aplicado en su fecha; la planilla de cada periodo usa la
    remuneración vigente; la ficha ya no edita el sueldo directamente."""

    def test_movimiento(self):
        from planillas import organizacion as org
        from planillas.forms import TrabajadorForm
        from planillas.models import MovimientoPersonal
        t = self.trabajador('PEREZ', sueldo='2000')
        self.assertTrue(TrabajadorForm(instance=t).fields['sueldo'].disabled)
        efectiva = (HOY.replace(day=1) + timedelta(days=40)).replace(day=1)  # el 1.º del mes siguiente
        m = MovimientoPersonal.objects.create(trabajador=t, tipo='AUMENTO', fecha_efectiva=efectiva,
                                              motivo='Evaluación anual', sueldo=D('2500'))
        org.enviar(m, self.admin)
        with self.assertRaises(org.ErrorPersonal):
            org.rechazar(m, self.admin, 'corto')
        org.aprobar(m, self.admin)
        m.refresh_from_db()
        self.assertEqual(m.estado, 'APROBADO')  # futuro: aún no se aplica
        org.aplicar_vigentes(hoy=efectiva)
        m.refresh_from_db()
        t.refresh_from_db()
        self.assertEqual((m.estado, t.sueldo, m.anterior['sueldo']), ('APLICADO', D('2500'), '2000.00'))
        self.assertEqual(org.vigente(t, 'sueldo', efectiva - timedelta(days=1)), D('2000'))
        self.assertEqual(org.vigente(t, 'sueldo', efectiva), D('2500'))
        self.client.force_login(self.admin)
        for url in [reverse('planillas:movimientos'), reverse('planillas:movimiento', args=[m.pk]),
                    reverse('planillas:movimiento_nuevo'), reverse('planillas:legajo', args=[t.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)


class PortalYLegajoTest(Base):
    """N.º 36 y 37: el trabajador pide vacaciones desde su portal y las aprueba su jefe; solo las aprobadas entran a
    la planilla; ve solo sus boletas; contratos con alerta de vencimiento y exportación para el T-Registro."""

    def test_portal_y_legajo(self):
        from planillas.calculo import vacaciones_del_mes
        from planillas.models import Contrato
        jefe_u = self.usuario('jefe_portal')
        u = self.usuario('trab_portal')
        PerfilUsuario.objects.filter(usuario=u).update(jefe=jefe_u)
        t = self.trabajador('PORTAL', usuario=u)
        self.client.force_login(u)
        self.assertContains(self.client.get(reverse('mi_portal')), 'Mis boletas de pago')
        inicio = HOY.replace(day=1) + timedelta(days=40)
        inicio = inicio.replace(day=5)
        self.client.post(reverse('mi_portal'), {'que': 'vacaciones', 'tipo': 'GOCE', 'desde': inicio.isoformat(),
                                                'hasta': (inicio + timedelta(days=4)).isoformat()})
        v = t.vacaciones.get()
        self.assertEqual((v.estado, v.niveles[0]['usuario']), ('SOLICITADA', jefe_u.pk))
        periodo = inicio.strftime('%Y%m')
        self.assertEqual(vacaciones_del_mes(t, periodo)[0], D('0'))  # aún no aprobada
        self.client.force_login(jefe_u)
        self.client.post(reverse('aprobaciones_bandeja'), {'doc': 'VACACION', 'pk': v.pk, 'accion': 'aprobar'})
        v.refresh_from_db()
        self.assertEqual(v.estado, 'APROBADA')
        self.assertEqual(vacaciones_del_mes(t, periodo)[0], D('5'))
        # otro usuario sin trabajador ve el aviso y no ve boletas ajenas
        self.client.force_login(jefe_u)
        self.assertContains(self.client.get(reverse('mi_portal')), 'no está vinculado')
        # contrato por vencer en 20 días: alerta para Planillas
        Contrato.objects.create(trabajador=t, tipo='NECESIDAD_MERCADO', inicio=HOY - timedelta(days=150),
                                fin=HOY + timedelta(days=20))
        from planillas.organizacion import por_vencer
        self.assertEqual(len(por_vencer()[0]), 1)
        self.client.force_login(self.admin)
        r = self.client.get(reverse('planillas:t_registro'))
        self.assertIn('spreadsheetml', r['Content-Type'])
        r = self.client.get(reverse('planillas:legajo', args=[t.pk]))
        self.assertContains(r, 'vence en 20 d')
