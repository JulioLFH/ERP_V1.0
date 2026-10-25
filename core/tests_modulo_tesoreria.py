"""v1.75: Tesorería como módulo propio (separado de Finanzas) y coherencia de los menús de todos los módulos."""
import importlib
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import get_resolver, resolve, reverse

from core.models import PerfilAcceso, PerfilUsuario
from finanzas.models import Cuenta

from .modulos import DERIVADOS, GRUPOS, MODULOS, RUTAS_FINANZAS, menu_de, modulos_de_ruta, modulos_del_usuario
from .permisos import TODAS


class Base(TestCase):
    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(Decimal('3.441'), Decimal('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def usuario(self, nombre, modulos=(), acciones=None):
        u = User.objects.create_user(nombre, password='Clave-segura-9')
        for m in modulos:
            u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
        if acciones is not None:
            PerfilUsuario.objects.create(usuario=u, acciones=list(acciones))
        return u


class ModuloTesoreriaTest(Base):
    def test_cada_pantalla_de_la_app_finanzas_tiene_modulo(self):
        """Una pantalla nueva de /finanzas/ debe decidir si es de Tesorería o de Finanzas."""
        nombres = {n for n in get_resolver().namespace_dict['finanzas'][1].reverse_dict if isinstance(n, str)}
        self.assertEqual(nombres - set(RUTAS_FINANZAS), set())
        for modulos in RUTAS_FINANZAS.values():
            self.assertTrue(set(modulos) <= {'tesoreria', 'finanzas', 'sunat'})  # v1.83: detracciones desde SUNAT
        acciones = {a.split('.')[0] for a in TODAS}
        self.assertIn('tesoreria', acciones)
        self.assertNotIn('finanzas.transferir', TODAS)  # las transferencias son de Tesorería

    def test_usuario_solo_de_tesoreria(self):
        Cuenta.objects.create(nombre='Caja central', tipo='CAJA')
        self.client.force_login(self.usuario('cajero', ['tesoreria']))
        for nombre in ('finanzas:cuentas', 'finanzas:movimientos', 'finanzas:cobranza', 'finanzas:pago_multiple',
                       'finanzas:detracciones_masivas', 'finanzas:conciliacion', 'finanzas:flujo',
                       'finanzas:flujo_proyectado', 'tipos_cambio'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)
        r = self.client.get(reverse('finanzas:cuentas'))
        self.assertEqual(r.context['modulo']['clave'], 'tesoreria')
        self.assertContains(r, 'Cobranza múltiple')
        self.assertNotContains(r, 'Préstamos y leasing')
        for nombre in ('finanzas:letras', 'finanzas:anticipos', 'finanzas:prestamos'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)
        self.assertIn('contactos', modulos_del_usuario(User.objects.get(username='cajero')))

    def test_usuario_solo_de_finanzas(self):
        self.client.force_login(self.usuario('analista', ['finanzas']))
        for nombre in ('finanzas:letras', 'finanzas:anticipos', 'finanzas:prestamos', 'finanzas:canje_nuevo',
                       'finanzas:flujo_proyectado'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)
        r = self.client.get(reverse('finanzas:letras'))
        self.assertEqual(r.context['modulo']['clave'], 'finanzas')
        self.assertNotContains(r, 'Cobranza múltiple')
        for nombre in ('finanzas:cuentas', 'finanzas:cobranza', 'finanzas:pago', 'finanzas:transferencia'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)

    def test_acciones_de_tesoreria(self):
        """Las pantallas de cobro y pago múltiple, depósito masivo y constancias exigen registrar en Tesorería."""
        self.client.force_login(self.usuario('consulta', ['tesoreria'], acciones=[]))
        for nombre in ('finanzas:cobranza_multiple', 'finanzas:pago_multiple', 'finanzas:detracciones_masivas_nuevo',
                       'finanzas:constancias_clientes', 'finanzas:movimiento_nuevo'):
            r = self.client.post(reverse(nombre), {})
            self.assertEqual(r.status_code, 403, nombre)
            self.assertContains(r, 'no tiene permiso', status_code=403)
        # con «finanzas.registrar» (anticipos y letras) tampoco se cobra: el permiso es de Tesorería
        self.client.force_login(self.usuario('mixto', ['tesoreria', 'finanzas'], acciones=['finanzas.registrar']))
        self.assertEqual(self.client.post(reverse('finanzas:cobranza_multiple'), {}).status_code, 403)

    def test_quien_tenia_finanzas_conserva_tesoreria(self):
        """La migración 0058 da Tesorería y sus permisos a los usuarios y perfiles que tenían Finanzas."""
        antiguo = self.usuario('tesorero', ['finanzas'], acciones=['finanzas.registrar', 'finanzas.transferir',
                                                                   'ventas.emitir'])
        rol = PerfilAcceso.objects.create(nombre='Tesorero', modulos=['finanzas'],
                                          acciones=['finanzas.anular', 'finanzas.cuentas'])
        migracion = importlib.import_module('core.migrations.0058_modulo_tesoreria')
        migracion.separar(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        antiguo = User.objects.get(pk=antiguo.pk)
        self.assertEqual(modulos_del_usuario(antiguo) & {'tesoreria', 'finanzas'}, {'tesoreria', 'finanzas'})
        self.assertEqual(PerfilUsuario.objects.get(usuario=antiguo).acciones,
                         ['finanzas.registrar', 'tesoreria.registrar', 'tesoreria.transferir', 'ventas.emitir'])
        rol.refresh_from_db()
        self.assertEqual((rol.modulos, rol.acciones), (['finanzas', 'tesoreria'], [
            'finanzas.anular', 'finanzas.cuentas', 'tesoreria.anular', 'tesoreria.cuentas']))
        self.client.force_login(antiguo)
        self.assertEqual(self.client.get(reverse('finanzas:cobranza')).status_code, 200)


class MenusTest(Base):
    def test_cada_opcion_del_menu_la_abre_quien_tiene_el_modulo(self):
        """Ningún módulo muestra en su menú una pantalla que sus usuarios no pueden abrir."""
        errores = []
        for m in MODULOS:
            mods = {m['clave']} | {d for d, origen in DERIVADOS.items() if m['clave'] in origen}
            for item in menu_de(m):
                for enlace in item.get('hijos') or [item]:
                    permitidos = modulos_de_ruta(resolve(enlace['url'].partition('?')[0]))
                    if permitidos is not None and not set(permitidos) & mods:
                        errores.append(f'{m["clave"]}: {enlace["etiqueta"]} ({enlace["url"]}) -> {permitidos}')
        self.assertEqual(errores, [])
