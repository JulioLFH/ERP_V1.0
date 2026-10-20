"""Seguridad de acceso (v1.43): permisos por pantalla (oculta, solo lectura, sin exportar), sueldos y saldos
reservados, filtros por centro de costo, cuenta y vendedor, perfiles de acceso, política de contraseñas con historial
y vencimiento, doble factor obligatorio, cierre por inactividad, bloqueo por intentos y aviso de equipo nuevo. Un
escenario por clase."""
import time
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import DispositivoConocido, Empresa, PerfilAcceso, PerfilUsuario, SegundoFactor, Tercero
from .modulos import GRUPOS

D = Decimal


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def usuario(self, nombre, modulos=(), acciones=(), **perfil):
        u = User.objects.create_user(nombre, password='Clave-segura-9', email=f'{nombre}@x.pe')
        for m in modulos:
            u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
        PerfilUsuario.objects.create(usuario=u, acciones=list(acciones), **perfil)
        return u

    def entrar(self, u):
        self.client.force_login(u)


# ---------------------------------------------------------------- permisos por pantalla
class PantallaOcultaTest(Base):
    def test_no_aparece_ni_se_abre(self):
        u = self.usuario('ven1', ['ventas'], ['ventas.emitir'], pantallas={'ventas:cot_lista': 'OCULTA'})
        self.entrar(u)
        self.assertEqual(self.client.get(reverse('ventas:cot_lista')).status_code, 403)
        r = self.client.get(reverse('ventas:pendientes'))
        self.assertEqual(r.status_code, 200)
        urls = [h['url'] for i in r.context['menu_modulo'] for h in (i.get('hijos') or [i])]
        self.assertNotIn(reverse('ventas:cot_lista'), urls)
        self.assertIn(reverse('ventas:lista'), urls)


class PantallaSoloLecturaTest(Base):
    def test_consulta_pero_no_registra(self):
        u = self.usuario('ven2', ['ventas'], ['ventas.emitir'], pantallas={'ventas:cot_lista': 'LECTURA'})
        self.entrar(u)
        self.assertEqual(self.client.get(reverse('ventas:cot_lista')).status_code, 200)
        r = self.client.post(reverse('ventas:cot_nuevo'), {'tipo': 'COT'})  # su detalle cuelga de la pantalla
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, 'solo consulta', status_code=403)


class PantallaSinExportarTest(Base):
    def test_trabaja_pero_no_descarga(self):
        u = self.usuario('rrhh1', ['planillas'], ['planillas.calcular'],
                         pantallas={'planillas:trabajadores': 'SIN_EXPORTAR'})
        self.entrar(u)
        self.assertEqual(self.client.get(reverse('planillas:trabajadores')).status_code, 200)
        r = self.client.get(reverse('planillas:trabajadores') + '?formato=excel')
        self.assertEqual(r.status_code, 403)


# ---------------------------------------------------------------- datos sensibles y filtros por dato
class SueldosReservadosTest(Base):
    def test_no_ve_sueldos(self):
        from contabilidad.models import CentroCosto
        from planillas.forms import TrabajadorForm
        from planillas.models import Trabajador
        cc = CentroCosto.objects.create(codigo='CC-S1', nombre='Planta S', tipo='PRODUCCION')
        Trabajador.objects.create(numero_doc='41000001', apellido_paterno='RESERVADO', nombres='X',
                                  fecha_ingreso=date(2024, 1, 1), sueldo=D('7654.32'), centro_costo=cc)
        u = self.usuario('rrhh2', ['planillas'], ['planillas.calcular'], ver_sueldos=False)
        self.entrar(u)
        r = self.client.get(reverse('planillas:trabajadores'))
        self.assertContains(r, 'RESERVADO')
        self.assertNotContains(r, '7,654.32')
        self.assertEqual(self.client.get(reverse('planillas:lista')).status_code, 403)
        self.assertNotIn('sueldo', TrabajadorForm(ver_sueldos=False).fields)


class FiltroCentroCostoTest(Base):
    def test_solo_sus_centros(self):
        from contabilidad.models import CentroCosto
        from planillas.models import Trabajador
        planta = CentroCosto.objects.create(codigo='CC-F1', nombre='Planta F', tipo='PRODUCCION')
        linea = CentroCosto.objects.create(codigo='CC-F11', nombre='Línea 1', tipo='PRODUCCION', padre=planta)
        oficina = CentroCosto.objects.create(codigo='CC-F2', nombre='Oficina F', tipo='ADMINISTRACION')
        for doc, ap, cc in (('42000001', 'DELAPLANTA', planta), ('42000002', 'DELALINEA', linea),
                            ('42000003', 'DELAOFICINA', oficina)):
            Trabajador.objects.create(numero_doc=doc, apellido_paterno=ap, nombres='X', fecha_ingreso=date(2024, 1, 1),
                                      sueldo=D('2000'), centro_costo=cc)
        u = self.usuario('jefe_planta', ['planillas'], ['planillas.calcular'])
        PerfilUsuario.objects.get(usuario=u).centros.set([planta])
        self.entrar(u)
        r = self.client.get(reverse('planillas:trabajadores'))
        self.assertContains(r, 'DELAPLANTA')
        self.assertContains(r, 'DELALINEA')  # subcentro
        self.assertNotContains(r, 'DELAOFICINA')
        otro = Trabajador.objects.get(numero_doc='42000003')
        r = self.client.get(reverse('planillas:trabajador_editar', args=[otro.pk]))
        self.assertRedirects(r, reverse('planillas:trabajadores'), fetch_redirect_response=False)


class FiltroCuentaYSaldoTest(Base):
    def test_solo_su_caja_sin_saldo(self):
        from finanzas.models import Cuenta
        mia = Cuenta.objects.create(nombre='Caja tienda Norte', tipo='CAJA', saldo_inicial=D('1234.56'))
        Cuenta.objects.create(nombre='Banco reservado', banco='BCP', tipo='BANCO', saldo_inicial=D('99000'))
        u = self.usuario('cajero1', ['finanzas'], ['finanzas.registrar'], ver_saldos=False)
        PerfilUsuario.objects.get(usuario=u).cuentas.set([mia])
        self.entrar(u)
        r = self.client.get(reverse('finanzas:cuentas'))
        self.assertContains(r, 'Caja tienda Norte')
        self.assertNotContains(r, 'Banco reservado')
        self.assertNotContains(r, '1,234.56')
        self.assertEqual(self.client.get(reverse('finanzas:flujo')).status_code, 403)


class FiltroVendedorTest(Base):
    def test_solo_sus_pedidos(self):
        from ventas.models import Cotizacion
        cliente = Tercero.objects.filter(tipo='CLIENTE').first()
        Cotizacion.objects.create(tipo='COT', numero='COT-V-0001', tercero=cliente, vendedor='Ana Ruiz')
        Cotizacion.objects.create(tipo='COT', numero='COT-V-0002', tercero=cliente, vendedor='Luis Paz')
        u = self.usuario('ana', ['ventas'], ['ventas.emitir'], vendedor='ana ruiz')
        self.entrar(u)
        r = self.client.get(reverse('ventas:cot_lista'))
        self.assertContains(r, 'COT-V-0001')
        self.assertNotContains(r, 'COT-V-0002')


# ---------------------------------------------------------------- perfiles de acceso
class PerfilAccesoTest(Base):
    def test_perfil_se_aplica_y_actualiza(self):
        admin = User.objects.create_superuser('admin_seg', 'a@a.pe', 'Clave-segura-9')
        self.client.force_login(admin)
        r = self.client.post(reverse('perfil_acceso_nuevo'), {
            'nombre': 'Asistente de compras', 'descripcion': '', 'ver_sueldos': '', 'ver_saldos': 'on',
            'limite_aprobacion': '0', 'modulos': ['compras'], 'acciones': ['compras.oc', 'compras.registrar'],
            'pantalla|compras:sire': 'OCULTA', 'actualizar_usuarios': 'on'})
        self.assertRedirects(r, reverse('perfiles_acceso'), fetch_redirect_response=False)
        perfil = PerfilAcceso.objects.get(nombre='Asistente de compras')
        self.assertEqual(perfil.pantallas, {'compras:sire': 'OCULTA'})
        r = self.client.post(reverse('usuario_nuevo'), {
            'username': 'asist1', 'first_name': '', 'last_name': '', 'email': '', 'is_active': 'on',
            'perfil_acceso': perfil.pk, 'clave1': 'Clave-segura-9', 'clave2': 'Clave-segura-9'})
        self.assertRedirects(r, reverse('usuarios'), fetch_redirect_response=False)
        u = User.objects.get(username='asist1')
        pu = PerfilUsuario.objects.get(usuario=u)
        self.assertEqual(set(u.groups.values_list('name', flat=True)), {GRUPOS['compras']})
        self.assertEqual(pu.acciones, ['compras.oc', 'compras.registrar'])
        self.assertFalse(pu.ver_sueldos)
        self.assertEqual(pu.pantallas, {'compras:sire': 'OCULTA'})
        # el perfil cambia: los usuarios que lo tienen se actualizan
        self.client.post(reverse('perfil_acceso_editar', args=[perfil.pk]), {
            'nombre': 'Asistente de compras', 'descripcion': '', 'ver_sueldos': '', 'ver_saldos': 'on',
            'limite_aprobacion': '0', 'modulos': ['compras', 'inventario'], 'acciones': ['compras.oc'],
            'actualizar_usuarios': 'on'})
        pu.refresh_from_db()
        self.assertEqual(pu.acciones, ['compras.oc'])
        self.assertEqual(pu.pantallas, {})
        self.assertIn(GRUPOS['inventario'], set(u.groups.values_list('name', flat=True)))
        self.assertEqual(self.client.get(reverse('perfiles_acceso')).status_code, 200)
        self.assertContains(self.client.get(reverse('usuario_editar', args=[u.pk])), 'Permisos por pantalla')


# ---------------------------------------------------------------- contraseñas
class PoliticaClaveTest(Base):
    def test_politica_e_historial(self):
        Empresa.objects.update(clave_largo_minimo=10, clave_simbolo=True, clave_historial=2)
        u = self.usuario('clave1')
        for mala in ('Corta-1', 'sinmayuscula-12', 'SinNumero-xx', 'SinSimbolo123'):
            with self.assertRaises(ValidationError, msg=mala):
                validate_password(mala, u)
        validate_password('Muy-Segura-2026', u)
        u.set_password('Muy-Segura-2026')
        u.save()
        self.assertIsNotNone(PerfilUsuario.objects.get(usuario=u).clave_cambiada_en)
        with self.assertRaisesMessage(ValidationError, 'No puede repetir'):
            validate_password('Muy-Segura-2026', u)
        u.set_password('Otra-Segura-2026')
        u.save()
        u.set_password('Tercera-Segura-2026')
        u.save()
        validate_password('Muy-Segura-2026', u)  # ya no está entre las 2 últimas


class ClaveVencidaTest(Base):
    def test_obliga_a_cambiarla(self):
        Empresa.objects.update(clave_vence_dias=30)
        u = self.usuario('vence1', ['ventas'], ['ventas.emitir'])
        PerfilUsuario.objects.filter(usuario=u).update(clave_cambiada_en=timezone.now() - timedelta(days=31))
        self.entrar(u)
        r = self.client.get(reverse('ventas:lista'))
        self.assertRedirects(r, reverse('cambiar_clave'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('cambiar_clave')).status_code, 200)
        r = self.client.post(reverse('cambiar_clave'), {'old_password': 'Clave-segura-9',
                                                        'new_password1': 'Nueva-Clave-2026',
                                                        'new_password2': 'Nueva-Clave-2026'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 200)


# ---------------------------------------------------------------- doble factor, inactividad, intentos y equipos
class DobleFactorObligatorioTest(Base):
    def test_aprobadores_deben_activarlo(self):
        Empresa.objects.update(exigir_doble_factor='APROBADORES')
        aprobador = self.usuario('aprob1', ['compras'], ['compras.aprobar_oc'])
        operador = self.usuario('oper1', ['compras'], ['compras.registrar'])
        self.entrar(aprobador)
        r = self.client.get(reverse('compras:lista'))
        self.assertRedirects(r, reverse('seguridad'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('seguridad')).status_code, 200)
        SegundoFactor.objects.create(usuario=aprobador, secreto='JBSWY3DPEHPK3PXP', activo=True)
        self.assertEqual(self.client.get(reverse('compras:lista')).status_code, 200)
        self.entrar(operador)
        self.assertEqual(self.client.get(reverse('compras:lista')).status_code, 200)
        Empresa.objects.update(exigir_doble_factor='TODOS')
        self.assertRedirects(self.client.get(reverse('compras:lista')), reverse('seguridad'),
                             fetch_redirect_response=False)


class InactividadTest(Base):
    def test_cierra_la_sesion(self):
        Empresa.objects.update(minutos_inactividad=30)
        u = self.usuario('inact1', ['ventas'], ['ventas.emitir'])
        self.entrar(u)
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 200)
        s = self.client.session
        s['ultima_actividad'] = time.time() - 31 * 60
        s.save()
        r = self.client.get(reverse('ventas:lista'))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith(reverse('login')))
        self.assertNotIn('_auth_user_id', self.client.session)


class IntentosBloqueoTest(Base):
    def test_bloqueo_segun_la_empresa(self):
        Empresa.objects.update(intentos_bloqueo=2)
        self.usuario('bloq1')
        for _ in range(2):
            self.client.post(reverse('login'), {'username': 'bloq1', 'password': 'mala'})
        r = self.client.post(reverse('login'), {'username': 'bloq1', 'password': 'Clave-segura-9'})
        self.assertContains(r, 'bloqueado temporalmente por 2 intentos')


class EquipoNuevoTest(Base):
    def test_avisa_el_ingreso_desde_otro_equipo(self):
        u = self.usuario('equipo1')
        datos = {'username': 'equipo1', 'password': 'Clave-segura-9'}
        with patch('core.correo.enviar') as enviar:
            self.assertEqual(self.client.post(reverse('login'), datos).status_code, 302)
            self.assertFalse(enviar.called)  # primer equipo: no hay con qué comparar
            self.client.post(reverse('login'), datos)  # mismo navegador (cookie): no avisa
            self.assertFalse(enviar.called)
            otro = self.client_class()
            otro.post(reverse('login'), datos)
            self.assertTrue(enviar.called)
            self.assertEqual(enviar.call_args[0][0], ['equipo1@x.pe'])
        self.assertEqual(DispositivoConocido.objects.filter(usuario=u).count(), 2)


class AlertaSeguridadAdminTest(Base):
    def test_avisa_controles_apagados(self):
        from . import alertas
        admin = User.objects.create_superuser('admin_al', 'a@a.pe', 'Clave-segura-9')
        Empresa.objects.update(segregacion_estricta=False, exigir_doble_factor='')
        titulos = {a['titulo'] for a in alertas.calcular(admin)}
        self.assertIn('Segregación de funciones no estricta', titulos)
        Empresa.objects.update(segregacion_estricta=True, exigir_doble_factor='APROBADORES')
        titulos = {a['titulo'] for a in alertas.calcular(admin)}
        self.assertNotIn('Segregación de funciones no estricta', titulos)
        self.assertNotIn('Verificación en dos pasos opcional', titulos)
