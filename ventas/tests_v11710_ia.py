"""v1.117.10: IA (Groq) en el CRM: clave en CRM › Configuración, datos leídos de los mensajes que entran y respuesta
sugerida. Groq se simula: ninguna prueba sale a internet."""
import json
from email.message import EmailMessage
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Tercero

from . import crm_ia
from .crm_web import procesar_correo
from .models import ConfigCRM, Oportunidad


def _correo(texto, remitente='Ana Ruiz <ana.ruiz@gmail.com>'):
    m = EmailMessage()
    m['From'], m['Subject'] = remitente, 'Consulta'
    m.set_content(texto)
    return m


class CrmIaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('ia1', 'ia@t.com', 'x')
        cls.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20601234567',
                                             nombre='CONSTRUCTORA SOL S.A.C.')

    def setUp(self):
        self.client.force_login(self.user)

    def activar(self):
        cfg = ConfigCRM.actual()
        cfg.ia_activa, cfg.ia_api_key = True, 'gsk_prueba'
        cfg.save()

    def test_sin_ia_la_oportunidad_entra_como_antes(self):
        with patch.object(crm_ia, '_chat') as chat:
            o = procesar_correo(_correo('Quiero precios. Mi celular es 999 888 777'))
        chat.assert_not_called()
        self.assertEqual((o.telefono, o.prospecto), ('', 'Ana Ruiz'))

    def test_con_ia_llena_telefono_empresa_ruc_y_lo_que_pide(self):
        self.activar()
        respuesta = json.dumps({'telefono': '+51 999 888 777', 'empresa': 'Constructora Sol', 'ruc': '20601234567',
                                'interes': 'cotización de ERP para 3 usuarios'})
        with patch.object(crm_ia, '_chat', return_value=respuesta) as chat:
            o = procesar_correo(_correo('Somos Constructora Sol (RUC 20601234567). Llámenme al +51 999 888 777.'))
        self.assertIn('Llámenme', chat.call_args[0][2])
        self.assertEqual(o.telefono, '+51 999 888 777')
        self.assertEqual(o.prospecto, 'Constructora Sol')
        self.assertEqual(o.tercero, self.cliente)
        self.assertEqual(o.nombre, 'Consulta por correo: Constructora Sol – cotización de ERP para 3 usuarios')
        self.assertIn('Datos leídos con IA: teléfono: +51 999 888 777', o.notas)
        self.assertEqual(o.actividades.first().tipo, 'LLAMADA')  # con teléfono, «Llamar hoy»

    def test_si_la_ia_falla_la_oportunidad_se_crea_igual(self):
        self.activar()
        with patch.object(crm_ia, '_chat', side_effect=crm_ia.ErrorIA('No se pudo conectar con Groq.')):
            o = procesar_correo(_correo('Hola, quiero información'))
        self.assertIsNotNone(o.pk)
        self.assertEqual(o.telefono, '')

    def test_datos_inventados_se_descartan(self):
        self.assertEqual(crm_ia._limpiar({'telefono': '12', 'ruc': '123', 'empresa': '  ACME  SAC ', 'interes': ''}),
                         {'telefono': '', 'empresa': 'ACME SAC', 'ruc': '', 'interes': ''})

    def test_sugerir_respuesta_en_la_oportunidad(self):
        o = procesar_correo(_correo('Quiero una cotización del ERP'))
        url = reverse('crm:sugerir', args=[o.pk])
        self.assertEqual(self.client.post(url).status_code, 400)  # sin IA activa
        self.activar()
        self.assertContains(self.client.get(reverse('crm:oportunidad', args=[o.pk])), 'Sugerir respuesta')
        with patch.object(crm_ia, '_chat', return_value='Hola Ana, gracias por escribirnos…') as chat:
            r = self.client.post(url)
        self.assertEqual(r.json()['texto'], 'Hola Ana, gracias por escribirnos…')
        self.assertEqual(r.json()['email'], 'ana.ruiz@gmail.com')
        self.assertIn('No inventes precios', chat.call_args[0][1])
        self.assertIn('Quiero una cotización del ERP', chat.call_args[0][2])
        with patch.object(crm_ia, '_chat', side_effect=crm_ia.ErrorIA('La clave de Groq no es válida.')):
            r = self.client.post(url)
        self.assertEqual((r.status_code, r.json()['error']), (502, 'La clave de Groq no es válida.'))

    def test_sugerir_respuesta_en_la_bandeja(self):
        from . import crm_canales
        conv = crm_canales.recibir('TELEGRAM', '777', '¿Tienen planes para colegios?', nombre='Luis')
        self.activar()
        self.assertContains(self.client.get(reverse('crm:conversacion', args=[conv.pk])), 'Sugerir respuesta')
        with patch.object(crm_ia, '_chat', return_value='Hola Luis, sí tenemos…') as chat:
            r = self.client.post(reverse('crm:conversacion_sugerir', args=[conv.pk]))
        self.assertEqual(r.json()['texto'], 'Hola Luis, sí tenemos…')
        self.assertIn('CLIENTE: ¿Tienen planes para colegios?', chat.call_args[0][2])

    def test_clave_en_configuracion_y_prueba(self):
        r = self.client.get(reverse('crm:configuracion'))
        self.assertContains(r, 'name="ia_api_key"')
        self.assertContains(r, 'IA del CRM (Groq)')
        self.activar()
        self.assertNotContains(self.client.get(reverse('crm:configuracion')), 'gsk_prueba')  # nunca se muestra
        with patch.object(crm_ia, '_chat', return_value='OK'):
            r = self.client.post(reverse('crm:configuracion'), {'accion': 'probar_ia'}, follow=True)
        self.assertContains(r, 'Groq aceptó la clave')
