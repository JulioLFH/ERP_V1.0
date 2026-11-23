"""v1.117.2: los módulos que tienen su casilla (Rendición de gastos, Requerimientos) se tienen solo si se marcan; ya no
llegan solos con Tesorería, Finanzas, Contabilidad, Inventario, Compras u Obras. Lo que muestra el usuario es lo que
ve. Los módulos sin casilla (Contactos, Consulta RUC, Conversaciones…) siguen derivándose."""
from django.contrib.auth.models import Group, User
from django.test import TestCase

from .modulos import DERIVADOS, GRUPOS, SIN_GRUPO, modulos_del_usuario
from .plataforma import modulos_permitidos


class ModulosConCasillaTest(TestCase):
    def usuario(self, *modulos):
        u = User.objects.create_user(f'u{User.objects.count()}', 'u@e.pe', 'x')
        u.groups.set([Group.objects.get_or_create(name=GRUPOS[m])[0] for m in modulos])
        return User.objects.get(pk=u.pk)

    def test_solo_se_derivan_los_modulos_sin_casilla(self):
        self.assertEqual(set(DERIVADOS) - SIN_GRUPO, set())

    def test_tesoreria_no_trae_rendicion_ni_compras_requerimientos(self):
        plan = modulos_permitidos()
        mods = modulos_del_usuario(self.usuario('tesoreria', 'compras', 'contabilidad'))
        self.assertNotIn('rendiciones', mods)
        self.assertNotIn('requerimientos', mods)
        if 'contactos' in plan:
            self.assertIn('contactos', mods)  # sin casilla: sigue llegando con los módulos que lo usan
        mods = modulos_del_usuario(self.usuario('tesoreria', 'rendiciones'))
        if 'rendiciones' in plan:
            self.assertIn('rendiciones', mods)
