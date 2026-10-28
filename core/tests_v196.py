"""v1.96: chat como en Odoo: avisos de aprobación por chat directo a quienes aprueban (y la respuesta a quien la
pidió) y el chat en la barra superior."""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from colaboracion.avisos import aviso_aprobacion
from colaboracion.models import Mensaje
from core.modulos import GRUPOS


class _Doc:
    """Documento con niveles de aprobación (como un requerimiento)."""
    def __init__(self, pk, solicitante, niveles):
        self.pk, self.solicitante, self.niveles, self.nivel_aprobado = pk, solicitante, niveles, 0

    def __str__(self):
        return f'RQ01-{self.pk:08d}'


class AvisosAprobacionTest(TestCase):
    def setUp(self):
        self.ana = User.objects.create_user('ana', password='x', first_name='Ana', last_name='Ruiz')
        self.jefe = User.objects.create_user('jefe', password='x', first_name='Jorge', last_name='Paz')
        self.gerente = User.objects.create_user('gerente', password='x', first_name='Gina', last_name='Soto')
        grupo = Group.objects.get_or_create(name=GRUPOS['inventario'])[0]  # con un módulo ya tienen Conversaciones
        for u in (self.ana, self.jefe, self.gerente):
            u.groups.add(grupo)
        self.doc = _Doc(5, self.ana, [{'nivel': 1, 'usuario': self.jefe.pk, 'nombre': 'Jorge Paz', 'desde': None},
                                      {'nivel': 2, 'usuario': self.gerente.pk, 'nombre': 'Gina Soto', 'desde': None}])

    def _avisar(self, *args):
        with self.captureOnCommitCallbacks(execute=True):
            aviso_aprobacion(*args)

    def test_pedido_aprobacion_y_respuesta_por_chat(self):
        self._avisar('REQUERIMIENTO', self.doc, self.ana, 'ENVIADO', 'Lo necesito para el lunes')
        m = Mensaje.objects.get(autor=self.ana)
        self.assertIn('Solicito su aprobación para el requerimiento RQ01-00000005', m.texto)
        self.assertIn('Lo necesito para el lunes', m.texto)
        self.assertEqual(m.enlace, reverse('requerimientos:detalle', args=[5]))
        self.assertEqual(set(m.canal.membresias.values_list('usuario__username', flat=True)), {'ana', 'jefe'})
        # el jefe aprueba su nivel: el aviso pasa a la gerente (nivel 2)
        self.doc.nivel_aprobado = 1
        self._avisar('REQUERIMIENTO', self.doc, self.jefe, 'APROBADO')
        m = Mensaje.objects.get(autor=self.jefe)
        self.assertIn('Aprobé el requerimiento RQ01-00000005 en mi nivel', m.texto)
        self.assertIn('(nivel 2)', m.texto)
        self.assertEqual(set(m.canal.membresias.values_list('usuario__username', flat=True)), {'jefe', 'gerente'})
        # la gerente rechaza: se avisa a quien lo pidió, con el motivo
        self._avisar('REQUERIMIENTO', self.doc, self.gerente, 'RECHAZADO', 'Sin presupuesto')
        m = Mensaje.objects.get(autor=self.gerente)
        self.assertEqual(m.texto, 'Rechacé el requerimiento RQ01-00000005.\nMotivo: Sin presupuesto')
        self.assertEqual(set(m.canal.membresias.values_list('usuario__username', flat=True)), {'gerente', 'ana'})

    def test_chat_de_la_barra(self):
        self._avisar('REQUERIMIENTO', self.doc, self.ana, 'ENVIADO')
        self.client.force_login(self.jefe)
        datos = self.client.get(reverse('conversaciones:resumen')).json()
        self.assertEqual(datos['total'], 1)
        self.assertEqual(datos['conversaciones'][0]['titulo'], 'Ana Ruiz')
        self.assertIn('Solicito su aprobación', datos['conversaciones'][0]['texto'])
        r = self.client.get(reverse('home'))
        self.assertContains(r, 'id="chat"')
        r = self.client.get(datos['conversaciones'][0]['url'])
        self.assertContains(r, 'Abrir el documento')
        self.assertEqual(self.client.get(reverse('conversaciones:resumen')).json()['total'], 0)  # ya lo leyó

    def test_un_aviso_que_falla_no_detiene_la_aprobacion(self):
        roto = _Doc(6, self.ana, [{'nivel': 1, 'usuario': 999999, 'nombre': '?', 'desde': None}])
        self._avisar('REQUERIMIENTO', roto, self.ana, 'ENVIADO')  # aprobador inexistente: no hay a quién escribir
        self._avisar('NO_EXISTE', roto, None, 'ENVIADO')  # sin usuario: no se envía nada
        self.assertFalse(Mensaje.objects.exists())
