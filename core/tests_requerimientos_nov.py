"""Requerimientos de noviembre 2026 (REQ-01 a REQ-10): copiado al crear empresas, vencimientos SUNAT/SIRE, módulos
administrados solo por el administrador, perfiles de jefatura, permisos del módulo SUNAT, correo de Ceiba, tipo de
cambio por periodo, logo en los formatos y cuentas por operación. Un escenario por clase."""
from types import SimpleNamespace

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from .models import Empresa, PerfilAcceso, PerfilUsuario
from .modulos import GRUPOS, POR_CLAVE

CLAVE = 'Clave-segura-9'


def _usuario(nombre, modulos=(), acciones=(), admin=False):
    if admin:
        return User.objects.create_superuser(nombre, f'{nombre}@x.pe', CLAVE)
    u = User.objects.create_user(nombre, f'{nombre}@x.pe', CLAVE)
    for m in modulos:
        u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
    PerfilUsuario.objects.create(usuario=u, acciones=list(acciones))
    return u


class Base(TestCase):
    def setUp(self):
        Empresa.objects.create(ruc='20100000001', razon_social='EMPRESA DE PRUEBA S.A.C.')


# ---------------------------------------------------------------- REQ-01
class CopiadoAlCrearEmpresaTest(TestCase):
    databases = {'default', 'empresa2'}

    def setUp(self):
        Empresa.objects.using('default').create(ruc='20100000001', razon_social='MODELO S.A.C.', igv_tasa=18)
        Empresa.objects.using('empresa2').create(ruc='20600000005', razon_social='NUEVA S.A.C.')

    def test_copia_solo_lo_marcado_y_sin_datos_propios(self):
        from contabilidad.models import CentroCosto, CuentaContable, CuentaDefecto
        from finanzas.models import Cuenta

        from . import grupo
        from .models import Almacen, Serie
        propia = CuentaContable.objects.using('default').create(codigo='659901', nombre='GASTO PROPIO DEL GRUPO')
        defecto = CuentaDefecto.objects.using('default').first()
        self.assertIsNotNone(defecto, 'el PCGE carga las cuentas por operación')
        defecto.cuenta = propia
        defecto.save(using='default')
        padre = CentroCosto.objects.using('default').create(codigo='ADM', nombre='Administración')
        CentroCosto.objects.using('default').create(codigo='ADM01', nombre='Contabilidad', padre=padre)
        Almacen.objects.using('default').create(codigo='LIM', nombre='Almacén Lima')
        Cuenta.objects.using('default').create(tipo='BANCO', nombre='BCP soles', banco='BCP', numero='191-123',
                                               cci='00219100', saldo_inicial=5000, moneda='PEN')
        Serie.objects.using('default').create(tipo='01', serie='F001', correlativo=250)

        hechas = grupo.copiar_configuracion('default', 'empresa2', ['series', 'plan_contable', 'cuentas_operacion'])
        self.assertEqual(hechas, ['Series de comprobantes', 'Plan contable', 'Cuentas por operación'])
        self.assertEqual(Serie.objects.using('empresa2').get(serie='F001').correlativo, 0)
        self.assertTrue(CuentaContable.objects.using('empresa2').filter(codigo='659901').exists())
        # values_list: las relaciones se leen en la misma base (el enrutador usa la empresa activa)
        self.assertEqual(CuentaDefecto.objects.using('empresa2').filter(clave=defecto.clave).values_list(
            'cuenta__codigo', flat=True).get(), '659901')
        self.assertFalse(CentroCosto.objects.using('empresa2').filter(codigo='ADM').exists())  # no se marcó
        self.assertFalse(Cuenta.objects.using('empresa2').filter(nombre='BCP soles').exists())

        grupo.copiar_configuracion('default', 'empresa2', ['centros', 'almacenes', 'cajas_bancos'])
        self.assertEqual(CentroCosto.objects.using('empresa2').filter(codigo='ADM01').values_list(
            'padre__codigo', flat=True).get(), 'ADM')
        self.assertTrue(Almacen.objects.using('empresa2').filter(codigo='LIM').exists())
        banco = Cuenta.objects.using('empresa2').get(nombre='BCP soles')
        self.assertEqual((banco.numero, banco.cci, banco.saldo_inicial), ('', '', 0))  # datos propios: no

    def test_creacion_cortada_por_un_reinicio_se_puede_reintentar(self):
        from datetime import timedelta

        from django.utils import timezone

        from . import grupo
        from .models import EmpresaGrupo
        r = EmpresaGrupo.objects.create(alias='empresa9', base='erp_empresa9', razon_social='CORTADA S.A.C.',
                                        ruc='20100066603')
        self.assertFalse(grupo.colgada(r))  # recién creada: puede estar creándose en otro proceso
        EmpresaGrupo.objects.filter(pk=r.pk).update(creado=timezone.now() - timedelta(minutes=10))
        r.refresh_from_db()
        self.assertTrue(grupo.colgada(r))  # sin tarea viva que la cree
        r.estado = 'LISTA'
        self.assertFalse(grupo.colgada(r))

    def test_modelo_sin_nada_marcado(self):
        from django.contrib.auth.models import User

        from . import grupo
        admin = User.objects.create_superuser('dueno', 'd@x.pe', CLAVE)
        with self.assertRaisesMessage(grupo.ErrorGrupo, 'Marque qué copiar'):
            grupo.crear_empresa('OTRA S.A.C.', '20100066603', admin, modelo='default', copiar=[])


# ---------------------------------------------------------------- REQ-02
class CronogramaSunatTest(Base):
    def test_cronograma_cargado_tiene_prioridad_y_solo_lo_edita_el_admin(self):
        from datetime import date

        from .cronograma_sunat import fechas, vencimiento
        from .models import CronogramaSunat
        self.assertEqual(vencimiento('20600000005', '202609'), date(2026, 10, 21))  # tabla del sistema
        self.assertIsNone(vencimiento('20600000005', '202701'))
        contador = _usuario('contador', ['contabilidad'])
        self.client.force_login(contador)
        url = reverse('cronograma_sunat')
        self.assertEqual(self.client.get(url + '?anio=2027').status_code, 200)
        datos = {'anio': '2027', **{f'202701_{c}': f'2027-02-{15 + i:02d}' for i, c in enumerate(CronogramaSunat.CAMPOS)}}
        self.assertEqual(self.client.post(url, datos).status_code, 403)
        self.assertFalse(CronogramaSunat.objects.exists())
        self.client.force_login(_usuario('admin', admin=True))
        datos['202702_d0'] = '2027-03-16'  # febrero incompleto: no se guarda
        self.client.post(url, datos)
        self.assertEqual(list(CronogramaSunat.objects.values_list('periodo', flat=True)), ['202701'])
        self.assertEqual(vencimiento('20600000005', '202701'), date(2027, 2, 18))  # grupo 4-5
        self.assertEqual(vencimiento('20600000005', '202701', buen_contribuyente=True), date(2027, 2, 21))
        self.assertEqual(fechas('202701')[1], 'cargado')
        self.client.post(url, {'anio': '2027'})  # todo vacío: vuelve a no tener cronograma
        self.assertIsNone(vencimiento('20600000005', '202701'))

    def test_alerta_de_vencimiento(self):
        from .estudio import alerta_vencimiento
        sire = {'ventas': 'aceptada', 'compras': 'sin traer'}
        self.assertEqual(alerta_vencimiento(2, sire, False), 'Vence en 2 días: falta SIRE compras, cerrar el periodo.')
        self.assertEqual(alerta_vencimiento(0, {'ventas': 'aceptada', 'compras': 'aceptada'}, False),
                         'Vence hoy: falta cerrar el periodo.')
        self.assertEqual(alerta_vencimiento(1, {'ventas': 'aceptada', 'compras': 'aceptada'}, True), '')
        self.assertEqual(alerta_vencimiento(20, sire, False), '')  # aún lejos
        self.assertEqual(alerta_vencimiento(-1, sire, False), '')  # ya venció


# ---------------------------------------------------------------- REQ-03
class ModulosSoloAdministradorTest(Base):
    def setUp(self):
        super().setUp()
        self.delegado = _usuario('delegado', ['ajustes', 'ventas', 'compras'], ['ajustes.usuarios'])
        self.vendedor = _usuario('vendedor', ['ventas'])

    def form(self, datos, editor):
        from .usuarios import UsuarioForm
        return UsuarioForm({'username': 'vendedor', 'is_active': 'on', **datos}, instance=self.vendedor,
                           editor=editor)

    def test_delegado_no_asigna_modulos_ni_aplica_perfiles(self):
        f = self.form({'modulos': ['ventas', 'compras']}, self.delegado)  # aunque él tenga Compras
        self.assertFalse(f.is_valid())
        self.assertIn('Solo un administrador asigna', str(f.errors))
        perfil = PerfilAcceso.objects.create(nombre='Contador', modulos=['contabilidad'], acciones=[])
        f = self.form({'modulos': ['ventas'], 'perfil_acceso': perfil.pk, 'aplicar_perfil': 'on'}, self.delegado)
        self.assertFalse(f.is_valid())
        self.assertIn('Solo un administrador aplica perfiles', str(f.errors))
        admin = _usuario('admin', admin=True)
        self.assertTrue(self.form({'modulos': ['ventas', 'compras']}, admin).is_valid())

    def test_perfiles_y_modulos_de_la_empresa(self):
        self.client.force_login(self.delegado)
        self.assertEqual(self.client.get(reverse('perfil_acceso_nuevo')).status_code, 403)
        self.assertEqual(self.client.get(reverse('perfiles_acceso')).status_code, 200)  # los puede ver
        e = Empresa.objects.get()
        datos = {'accion': 'guardar', 'giros': [Empresa.GIROS[0][0]], 'regimen': Empresa.REGIMENES[0][0],
                 'sistema_costeo': Empresa.COSTEOS[0][0], 'ocultos': ['ventas']}
        self.client.post(reverse('empresa_giro'), datos)
        e.refresh_from_db()
        self.assertNotIn('ventas', e.modulos_ocultos or [])  # el delegado no apaga módulos
        self.client.force_login(_usuario('admin', admin=True))
        self.assertEqual(self.client.get(reverse('perfil_acceso_nuevo')).status_code, 200)
        self.client.post(reverse('empresa_giro'), datos)
        e.refresh_from_db()
        self.assertIn('ventas', e.modulos_ocultos)


# ---------------------------------------------------------------- REQ-06
class CorreoCeibaTest(Base):
    def test_respaldo_cuando_la_empresa_no_tiene_correo(self):
        from unittest.mock import patch

        from . import correo
        from .models import CorreoCeiba, CorreoConfig
        with self.assertRaisesMessage(correo.ErrorCorreo, 'correo de Ceiba'):
            correo.enviar(['a@x.pe'], 'Hola', 'core/correo_simple.html', {'texto': 't', 'asunto': 'Hola'})
        CorreoCeiba.objects.create(servidor='smtp.ceiba.pe', usuario='avisos@ceiba.pe', clave='secreta')
        enviados = []
        with patch('django.core.mail.EmailMultiAlternatives.send', lambda m: enviados.append(m)):
            correo.enviar(['a@x.pe'], 'Hola', 'core/correo_simple.html', {'texto': 't', 'asunto': 'Hola'})
            from email.header import decode_header, make_header
            remitente = str(make_header(decode_header(enviados[-1].from_email)))
            self.assertIn('Ceiba ERP · EMPRESA DE PRUEBA', remitente)
            self.assertIn('avisos@ceiba.pe', remitente)
            self.assertEqual(enviados[-1].connection.host, 'smtp.ceiba.pe')
            cfg = CorreoConfig.actual()  # la empresa configura el suyo: tiene prioridad
            cfg.servidor, cfg.usuario = 'smtp.empresa.pe', 'ventas@empresa.pe'
            cfg.save()
            correo.enviar(['a@x.pe'], 'Hola', 'core/correo_simple.html', {'texto': 't', 'asunto': 'Hola'})
            self.assertEqual(enviados[-1].connection.host, 'smtp.empresa.pe')
        CorreoCeiba.objects.update(activo=False)
        self.assertIsNone(correo.correo_ceiba())

    def test_solo_el_administrador_lo_configura_y_la_clave_no_se_pierde(self):
        from .models import CorreoCeiba
        admin = _usuario('admin', admin=True)
        self.client.force_login(admin)
        url = reverse('correo_ceiba')
        self.assertEqual(self.client.get(url).status_code, 200)
        datos = {'activo': 'on', 'servidor': 'smtp.gmail.com', 'puerto': '587', 'seguridad': 'TLS',
                 'usuario': 'ceiba@gmail.com', 'clave': 'clave-app'}
        self.client.post(url, datos)
        self.client.post(url, {**datos, 'clave': ''})  # vacía: se conserva la guardada
        self.assertEqual(CorreoCeiba.objects.get().clave, 'clave-app')
        self.assertNotContains(self.client.get(url), 'clave-app')  # no se vuelve a mostrar
        self.client.force_login(_usuario('contador', ['contabilidad']))
        self.assertIn(self.client.get(url).status_code, (403, 404))


# ---------------------------------------------------------------- REQ-07
class TipoCambioPorPeriodoTest(Base):
    @staticmethod
    def fuente(fecha):
        """Simula la fuente: no publica sábados ni domingos (404)."""
        from decimal import Decimal
        if fecha.weekday() >= 5:
            error = Exception('404')
            error.code = 404
            raise error
        return Decimal('3.700'), Decimal('3.710'), 'SUNAT'

    def test_descarga_el_mes_y_respeta_lo_manual(self):
        from datetime import date
        from decimal import Decimal
        from unittest.mock import patch

        from . import tipo_cambio
        from .models import TipoCambio
        TipoCambio.objects.create(fecha=date(2026, 1, 5), compra=Decimal('3.5'), venta=Decimal('3.6'), fuente='MANUAL')
        with patch('core.tipo_cambio._consultar', side_effect=self.fuente):
            r = tipo_cambio.descargar_periodo('202601')
        self.assertEqual(r['error'], '')
        self.assertEqual(len(r['sin_publicar']), 9)  # enero 2026: 5 sábados y 4 domingos
        self.assertEqual(r['ok'], 31 - 9 - 1)  # menos el día manual
        self.assertEqual(TipoCambio.objects.get(fecha=date(2026, 1, 5)).fuente, 'MANUAL')
        self.assertFalse(TipoCambio.objects.filter(fecha=date(2026, 1, 3)).exists())  # sábado: no se guarda

        admin = _usuario('admin', admin=True)
        self.client.force_login(admin)
        r = self.client.get(reverse('tipos_cambio') + '?periodo=2026-01')
        self.assertContains(r, 'sin publicación: usa el 02/01')

    def test_sin_conexion_se_detiene(self):
        from unittest.mock import patch

        from . import tipo_cambio
        with patch('core.tipo_cambio._consultar', side_effect=OSError('sin red')) as consulta:
            r = tipo_cambio.descargar_periodo('202602')
        self.assertIn('Sin respuesta de la fuente', r['error'])
        self.assertEqual(consulta.call_count, 1)  # no insiste con los demás días


# ---------------------------------------------------------------- REQ-09
class LogoEnFormatosTest(Base):
    PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40

    def test_logo_valida_se_guarda_y_sale_en_los_formatos(self):
        from pathlib import Path

        from django.conf import settings
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.template.loader import render_to_string

        from .forms import EmpresaForm
        e = Empresa.objects.get()
        self.assertEqual(e.logo_data_uri, '')
        self.assertEqual(render_to_string('core/_logo_empresa.html', {'empresa': e}).strip(), '')
        form = EmpresaForm(instance=e)
        form.cleaned_data = {'logo_archivo': SimpleUploadedFile('logo.txt', b'no es una imagen')}
        with self.assertRaisesMessage(Exception, 'PNG, JPG, GIF o WEBP'):
            form.clean_logo_archivo()
        e.logo, e.logo_tipo = self.PNG, 'image/png'
        e.save()
        e.refresh_from_db()
        self.assertTrue(e.logo_data_uri.startswith('data:image/png;base64,'))
        self.assertIn('<img src="data:image/png;base64,', render_to_string('core/_logo_empresa.html', {'empresa': e}))
        plantillas = Path(settings.BASE_DIR) / 'templates'
        for f in ('core/comprobante_imprimir.html', 'core/documento_detalle.html', 'logistica/imprimir.html',
                  'inventario/operacion_imprimir.html', 'finanzas/voucher.html', 'compras/hes.html',
                  'planillas/boletas.html', 'distribucion/imprimir.html'):
            self.assertIn('_logo_empresa.html', (plantillas / f).read_text(encoding='utf-8'), f)


# ---------------------------------------------------------------- REQ-10
class CuentasPorOperacionTest(Base):
    def test_agregar_editar_y_eliminar(self):
        from contabilidad.forms import CuentaDefectoFormSet
        from contabilidad.models import CuentaContable, CuentaDefecto
        from contabilidad.pcge import DEFECTOS
        contador = _usuario('contador', ['contabilidad'], ['contabilidad.configurar'])
        self.client.force_login(contador)
        url = reverse('contabilidad:configuracion')
        self.assertEqual(self.client.get(url).status_code, 200)
        cuenta = CuentaContable.objects.filter(imputable=True, codigo__startswith='63').first()
        self.client.post(url, {'accion': 'agregar', 'nueva-descripcion': 'Gastos de viaje de gerencia',
                               'nueva-cuenta': cuenta.pk})
        op = CuentaDefecto.objects.get(descripcion='Gastos de viaje de gerencia')
        self.assertTrue(op.clave.startswith('usr_'))
        r = self.client.post(url, {'accion': 'agregar', 'nueva-descripcion': 'gastos de viaje de gerencia',
                                   'nueva-cuenta': cuenta.pk})
        self.assertContains(r, 'Ya hay una operación con ese nombre')
        sistema = CuentaDefecto.objects.get(clave=DEFECTOS[0][0])
        self.client.post(url, {'accion': 'eliminar', 'pk': sistema.pk})
        self.assertTrue(CuentaDefecto.objects.filter(pk=sistema.pk).exists())  # del sistema: no se elimina
        qs = CuentaDefecto.objects.filter(pk=op.pk)
        fs = CuentaDefectoFormSet({'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '1', 'form-0-id': op.pk,
                                   'form-0-descripcion': 'Viajes de gerencia', 'form-0-cuenta': cuenta.pk},
                                  queryset=qs)
        self.assertTrue(fs.is_valid(), fs.errors)
        fs.save()
        self.assertEqual(CuentaDefecto.objects.get(pk=op.pk).descripcion, 'Viajes de gerencia')
        self.client.post(url, {'accion': 'eliminar', 'pk': op.pk})
        self.assertFalse(CuentaDefecto.objects.filter(pk=op.pk).exists())
        lector = _usuario('lector', ['contabilidad'])
        PerfilUsuario.objects.filter(usuario=lector).update(acciones=[])
        self.client.force_login(lector)
        self.client.post(url, {'accion': 'agregar', 'nueva-descripcion': 'Otra', 'nueva-cuenta': cuenta.pk})
        self.assertFalse(CuentaDefecto.objects.filter(descripcion='Otra').exists())  # sin permiso de configurar


# ---------------------------------------------------------------- REQ-04
class PerfilesPredefinidosTest(Base):
    def test_se_crean_validos_sin_duplicar_y_solo_el_admin(self):
        from .modulos import GRUPOS
        from .perfiles_predefinidos import PERFILES, crear, perfil_gerencia
        from .permisos import TODAS
        for nombre, _, modulos, acciones, _ in PERFILES:  # sin claves mal escritas
            self.assertTrue(set(modulos) <= set(GRUPOS), nombre)
            self.assertTrue(set(acciones) <= TODAS, f'{nombre}: {set(acciones) - TODAS}')
            self.assertTrue({a.split('.')[0] for a in acciones} - {'contactos'} <= set(modulos), nombre)
        nombres = [p[0] for p in PERFILES] + [perfil_gerencia()[0]]
        antes = PerfilAcceso.objects.count()
        delegado = _usuario('delegado', ['ajustes'], ['ajustes.usuarios'])
        self.client.force_login(delegado)
        self.client.post(reverse('perfiles_acceso'), {'accion': 'predefinidos'})
        self.assertFalse(PerfilAcceso.objects.filter(nombre__in=nombres).exists())
        self.assertEqual(PerfilAcceso.objects.count(), antes)
        self.client.force_login(_usuario('admin', admin=True))
        self.client.post(reverse('perfiles_acceso'), {'accion': 'predefinidos'})
        self.assertEqual(PerfilAcceso.objects.filter(nombre__in=nombres).count(), len(nombres))
        self.assertEqual(crear(), [])  # no duplica
        gerencia = PerfilAcceso.objects.get(nombre=perfil_gerencia()[0])
        self.assertEqual(gerencia.acciones, [])
        self.assertIn('contabilidad', gerencia.modulos)
        self.assertNotIn('ajustes', gerencia.modulos)

    def test_gerencia_ve_pero_no_registra(self):
        from .perfiles_predefinidos import crear, perfil_gerencia
        from .permisos import puede
        from .politicas import aplicar_perfil
        crear()
        gerente = _usuario('gerente')
        aplicar_perfil(PerfilUsuario.objects.get(usuario=gerente), PerfilAcceso.objects.get(nombre=perfil_gerencia()[0]))
        gerente = User.objects.get(pk=gerente.pk)
        self.client.force_login(gerente)
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 200)
        self.assertFalse(puede(gerente, 'ventas.emitir'))
        self.assertFalse(puede(gerente, 'contabilidad.asientos'))


# ---------------------------------------------------------------- REQ-03 (plan del grupo y plataforma Ceiba)
class PlanDelGrupoTest(Base):
    def setUp(self):
        super().setUp()
        from . import plataforma
        from .models import OperadorPlataforma
        plataforma.invalidar()
        self.addCleanup(plataforma.invalidar)  # el plan se memoriza unos segundos: no debe pasar a otras pruebas
        self.op = OperadorPlataforma(usuario='ceiba', nombre='Equipo Ceiba')
        self.op.fijar_clave(CLAVE)
        self.op.save()

    def test_consola_con_ingreso_propio(self):
        url = reverse('plataforma_inicio')
        admin = _usuario('admin', admin=True)
        self.client.force_login(admin)  # un administrador del cliente no entra a la consola
        self.assertRedirects(self.client.get(url), reverse('plataforma_ingresar'), fetch_redirect_response=False)
        r = self.client.post(reverse('plataforma_ingresar'), {'usuario': 'ceiba', 'clave': 'otra'}, follow=True)
        self.assertContains(r, 'incorrectos')
        self.client.post(reverse('plataforma_ingresar'), {'usuario': 'CEIBA', 'clave': CLAVE})
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.get(reverse('plataforma_salir'))
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_modulo_fuera_del_plan_no_existe_para_nadie(self):
        from .modulos import modulos_del_usuario
        from .plataforma import PLANES
        self.client.post(reverse('plataforma_ingresar'), {'usuario': 'ceiba', 'clave': CLAVE})
        basico = PLANES['basico'][1]
        self.client.post(reverse('plataforma_inicio'), {'nombre': 'Básico', 'todos': '0', 'modulos': sorted(basico)})
        admin = User.objects.create_superuser('admin', 'a@x.pe', CLAVE)
        mods = modulos_del_usuario(admin)
        self.assertIn('ventas', mods)
        self.assertNotIn('manufactura', mods)  # ni el administrador del cliente
        self.assertTrue({'tablero', 'ajustes'} <= mods)  # siempre
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 200)
        self.assertEqual(self.client.get(reverse('planillas:lista')).status_code, 403)
        r = self.client.get(reverse('grupo_panel'))
        self.assertContains(r, 'Plan contratado: Básico')
        # el cliente gestiona los módulos del plan; los de fuera no aparecen ni se pierden al guardar
        from .usuarios import UsuarioForm
        vendedor = _usuario('vendedor', ['ventas', 'planillas'])
        f = UsuarioForm(instance=vendedor, editor=admin)
        opciones = {c for c, _ in f.fields['modulos'].choices}
        self.assertIn('ventas', opciones)
        self.assertNotIn('planillas', opciones)
        f = UsuarioForm({'username': 'vendedor', 'is_active': 'on', 'modulos': ['ventas', 'compras']},
                        instance=vendedor, editor=admin)
        self.assertTrue(f.is_valid(), f.errors)
        f.save()
        grupos = set(User.objects.get(pk=vendedor.pk).groups.values_list('name', flat=True))
        self.assertTrue({GRUPOS['ventas'], GRUPOS['compras'], GRUPOS['planillas']} <= grupos)


# ---------------------------------------------------------------- REQ-05
class PermisosModuloSunatTest(Base):
    def test_sire_y_detracciones_con_permiso_propio(self):
        from .permisos import ACCIONES, RUTAS, accion_de_ruta, puede
        self.assertEqual({a for a, _, _ in ACCIONES['sunat']}, {'sire', 'detracciones'})
        tributario = _usuario('tributario', ['sunat'], ['sunat.sire', 'sunat.detracciones'])
        contador = _usuario('contador', ['contabilidad', 'tesoreria'], ['contabilidad.asientos', 'tesoreria.registrar'])
        consulta = _usuario('consulta', ['sunat'])

        def accion(ruta, user, metodo='POST'):
            match = SimpleNamespace(namespace=ruta.partition(':')[0] if ':' in ruta else '',
                                    url_name=ruta.partition(':')[2] or ruta)
            req = SimpleNamespace(method=metodo, user=user)
            return accion_de_ruta(match, req)

        self.assertIn('sire_propuestas', RUTAS)
        self.assertEqual(accion('sire_propuestas', tributario), 'sunat.sire')
        self.assertEqual(accion('sire_propuestas', contador), 'contabilidad.asientos')
        self.assertIsNone(accion('sire_propuestas', consulta, 'GET'))  # ver no exige acción
        a = accion('sire_propuestas', consulta)
        self.assertFalse(puede(consulta, a))  # sin permiso: no registra
        self.assertEqual(accion('finanzas:detraccion_masiva', tributario), 'sunat.detracciones')
        self.assertEqual(accion('ventas:deposito_detraccion', contador), 'tesoreria.registrar')
        self.assertFalse(puede(consulta, 'sunat.detracciones'))  # acción explícita: no viene por defecto

    def test_log_de_modificaciones_del_modulo(self):
        from .views import MODELOS_DEL_MODULO
        self.assertIn('core.PropuestaSIRE', MODELOS_DEL_MODULO['sunat'])
        destinos = [d for _, items in POR_CLAVE['sunat']['menu'] if isinstance(items, list) for _, d, *_ in items]
        self.assertIn('modificaciones?modulo=sunat', destinos)
