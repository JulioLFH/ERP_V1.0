"""v1.82: Planificación (MRP), Calidad y Mantenimiento como módulos propios (separados de Manufactura)."""
import importlib
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import PerfilAcceso, PerfilUsuario

from .modulos import POR_CLAVE, menu_de, modulos_del_usuario
from .tests_modulo_tesoreria import Base


def _etiquetas(clave):
    return [h['etiqueta'] for i in menu_de(POR_CLAVE[clave]) for h in (i.get('hijos') or [i])]


class ModulosPlantaTest(Base):
    def test_menus_propios_y_manufactura_sin_ellos(self):
        self.assertIn('MRP: necesidades y órdenes planificadas', _etiquetas('planificacion'))
        self.assertIn('Inspecciones de calidad', _etiquetas('control_calidad'))
        self.assertIn('Órdenes de mantenimiento', _etiquetas('mantenimiento'))
        manufactura = _etiquetas('manufactura')
        for fuera in ('Inspecciones de calidad', 'Órdenes de mantenimiento', 'Pronóstico de demanda'):
            self.assertNotIn(fuera, manufactura)
        self.assertIn('Órdenes de producción', manufactura)

    def test_usuario_solo_de_cada_modulo(self):
        casos = {'planificacion': ('manufactura:mrp', 'manufactura:pronostico', 'manufactura:capacidad'),
                 'control_calidad': ('manufactura:inspecciones', 'manufactura:planes_calidad',
                                     'manufactura:certificados'),
                 'mantenimiento': ('manufactura:ordenes_mant', 'manufactura:equipos', 'flota:parte')}
        for clave, rutas in casos.items():
            self.client.force_login(self.usuario(f'solo_{clave}', [clave]))
            for nombre in rutas:
                r = self.client.get(reverse(nombre))
                self.assertEqual(r.status_code, 200, nombre)
            self.assertEqual(r.context['modulo']['clave'], clave)
            self.assertEqual(self.client.get(reverse('manufactura:ordenes')).status_code, 403)
        self.client.force_login(self.usuario('solo_calidad_sin_accion', ['control_calidad'], acciones=[]))
        self.assertEqual(self.client.post(reverse('manufactura:inspeccion_nueva'), {}).status_code, 403)

    def test_quien_tenia_manufactura_conserva_todo(self):
        """La migración 0064 da los tres módulos y pasa los permisos de calidad, lotes, mantenimiento y MRP."""
        antiguo = self.usuario('jefe_planta', ['manufactura'], acciones=[
            'manufactura.ordenes', 'manufactura.calidad', 'manufactura.liberar_lotes', 'manufactura.mantenimiento'])
        rol = PerfilAcceso.objects.create(nombre='Planta', modulos=['manufactura'],
                                          acciones=['manufactura.mantenimiento'])
        migracion = importlib.import_module('core.migrations.0064_modulos_planta')
        migracion.separar(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        antiguo = User.objects.get(pk=antiguo.pk)
        self.assertTrue({'planificacion', 'control_calidad', 'mantenimiento'} <= modulos_del_usuario(antiguo))
        self.assertEqual(PerfilUsuario.objects.get(usuario=antiguo).acciones, [
            'control_calidad.inspecciones', 'control_calidad.liberar_lotes', 'mantenimiento.registrar',
            'manufactura.ordenes', 'planificacion.planificar'])
        rol.refresh_from_db()
        self.assertEqual((rol.modulos, rol.acciones), (
            ['manufactura', 'planificacion', 'control_calidad', 'mantenimiento'], ['mantenimiento.registrar']))
