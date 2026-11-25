"""v1.117.10: el buzón comercial del CRM se lee solo (servir.py cada 5 minutos) y leer_correo_crm no se frena por un
error de clave ni por un correo que no se puede procesar."""
from email.message import EmailMessage
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

import servir
from ventas.models import ConfigCRM, Oportunidad


def _correo(remitente, asunto, texto):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'] = remitente, 'jflores@ceiba-tecnologic.com', asunto
    m.set_content(texto)
    return m.as_bytes()


class ImapFalso:
    """Buzón IMAP de prueba: los correos sin leer y lo que se marca como leído."""
    correos, leidos, clave_buena = {}, [], 'buena'

    def __init__(self, servidor, puerto, timeout=None):
        self.timeout = timeout

    def login(self, usuario, clave):
        import imaplib
        if clave != self.clave_buena:
            raise imaplib.IMAP4.error('[AUTHENTICATIONFAILED] Invalid credentials')

    def select(self, buzon):
        return 'OK', [b'1']

    def search(self, *criterio):
        return 'OK', [b' '.join(k for k in self.correos if k not in self.leidos)]

    def fetch(self, num, partes):
        return 'OK', [(b'1 (RFC822)', self.correos[num])]

    def store(self, num, banderas, valor):
        self.leidos.append(num)

    def logout(self):
        pass


class BuzonCrmTest(TestCase):
    databases = {'default', 'empresa2'}  # el comando recorre todas las empresas del grupo

    def setUp(self):
        cfg = ConfigCRM.actual()
        cfg.correo_activo, cfg.correo_servidor, cfg.correo_puerto = True, 'mail.ceiba-tecnologic.com', 993
        cfg.correo_usuario, cfg.correo_clave = 'jflores@ceiba-tecnologic.com', 'buena'
        cfg.save()
        ImapFalso.correos = {b'1': _correo('Ana Ruiz <ana@constructora-sol.pe>', 'Cotización', 'Necesito precios'),
                             b'2': _correo('luis@gmail.com', 'Consulta', 'Hola')}
        ImapFalso.leidos = []

    def leer(self, **extra):
        salida, errores = StringIO(), StringIO()
        with patch('imaplib.IMAP4_SSL', ImapFalso):
            call_command('leer_correo_crm', stdout=salida, stderr=errores, **extra)
        return salida.getvalue(), errores.getvalue()

    def test_cada_correo_nuevo_es_una_oportunidad(self):
        salida, _ = self.leer()
        self.assertIn('2 oportunidad(es)', salida)
        self.assertEqual(ImapFalso.leidos, [b'1', b'2'])
        ana = Oportunidad.objects.get(email='ana@constructora-sol.pe')
        self.assertEqual(ana.origen, 'CORREO')
        # la segunda vez ya no hay nada nuevo y no escribe nada en el registro
        salida, errores = self.leer()
        self.assertEqual((salida, errores), ('', ''))

    def test_clave_mal_no_rompe_el_comando(self):
        ConfigCRM.objects.update(correo_clave='mala')
        salida, errores = self.leer()
        self.assertIn('no se pudo leer el buzón jflores@ceiba-tecnologic.com', errores)
        self.assertEqual(ImapFalso.leidos, [])

    def test_un_correo_con_error_no_frena_a_los_demas(self):
        original = __import__('ventas.crm_web', fromlist=['procesar_correo']).procesar_correo

        def falla_el_primero(mensaje):
            if 'ana@' in mensaje.get('From', ''):
                raise ValueError('correo raro')
            return original(mensaje)
        with patch('ventas.crm_web.procesar_correo', falla_el_primero):
            salida, errores = self.leer()
        self.assertIn('1 oportunidad(es)', salida)
        self.assertIn('correo raro', errores)
        self.assertEqual(ImapFalso.leidos, [b'1', b'2'])

    def test_servir_lo_lanza_cada_5_minutos(self):
        self.assertEqual(servir.CADA_CORREO_CRM, 300)
        with patch('subprocess.Popen') as popen:
            servir.leer_correo_crm('erp.settings')
        self.assertEqual(popen.call_args[0][0][1:3], ['manage.py', 'leer_correo_crm'])
