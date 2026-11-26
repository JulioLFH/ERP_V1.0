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
    """Buzón IMAP de prueba (por UID, como Dovecot): los correos sin leer, lo que se marca como leído, las carpetas
    (cuelgan de INBOX.) y lo que se mueve a cada una. v1.117.13: MOVE, COPY, UID EXPUNGE, CREATE."""
    correos, leidos, clave_buena = {}, [], 'buena'
    carpetas, movidos, capacidades, comandos = [], {}, b'IMAP4rev1 UIDPLUS MOVE', []

    def __init__(self, servidor, puerto, timeout=None):
        self.timeout = timeout

    def login(self, usuario, clave):
        import imaplib
        if clave != self.clave_buena:
            raise imaplib.IMAP4.error('[AUTHENTICATIONFAILED] Invalid credentials')

    def select(self, buzon):
        return 'OK', [b'1']

    def capability(self):
        return 'OK', [self.capacidades]

    def list(self):
        return 'OK', [b'(\\HasChildren) "." INBOX'] + [f'(\\HasNoChildren) "." {c}'.encode() for c in self.carpetas]

    def create(self, nombre):
        self.carpetas.append(nombre.strip('"'))
        return 'OK', [b'Create completed']

    def subscribe(self, nombre):
        return 'OK', [b'']

    def uid(self, comando, *args):
        ImapFalso.comandos.append(comando)
        if comando == 'SEARCH':
            return 'OK', [b' '.join(k for k in self.correos if k not in self.leidos)]
        if comando == 'FETCH':
            return 'OK', [(b'1 (UID ' + args[0] + b' RFC822)', self.correos[args[0]])]
        if comando == 'STORE':
            if 'Seen' in args[2]:
                self.leidos.append(args[0])
            return 'OK', [b'']
        uids = [u.encode() for u in str(args[0]).split(',')]
        if comando in ('MOVE', 'COPY'):
            destino = args[1].strip('"')
            if destino not in self.carpetas:
                return 'NO', [b'Mailbox does not exist']
            self.movidos.setdefault(destino, []).extend(uids)
            if comando == 'MOVE':
                for u in uids:
                    self.correos.pop(u, None)
        if comando == 'EXPUNGE':
            for u in uids:
                self.correos.pop(u, None)
        return 'OK', [b'']

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
        ImapFalso.carpetas, ImapFalso.movidos, ImapFalso.comandos = ['INBOX.Sent', 'INBOX.Trash'], {}, []
        ImapFalso.capacidades = b'IMAP4rev1 UIDPLUS MOVE'

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


class CarpetaCrmTest(BuzonCrmTest):
    """v1.117.13: los correos que se vuelven oportunidad salen de la bandeja a la carpeta del CRM del buzón."""

    def test_se_mueven_a_la_carpeta_crm_que_se_crea_sola(self):
        self.leer()
        self.assertIn('INBOX.CRM', ImapFalso.carpetas)  # cuelga de INBOX como las demás carpetas del buzón
        self.assertEqual(ImapFalso.movidos, {'INBOX.CRM': [b'1', b'2']})
        self.assertEqual(ImapFalso.correos, {})  # ya no están en la bandeja de entrada
        self.assertEqual(Oportunidad.objects.filter(origen='CORREO').count(), 2)
        # la segunda vez la carpeta ya existe: no se vuelve a crear
        ImapFalso.correos = {b'3': _correo('pia@minera.pe', 'Pedido', 'Precios')}
        self.leer()
        self.assertEqual(ImapFalso.carpetas.count('INBOX.CRM'), 1)
        self.assertEqual(ImapFalso.movidos['INBOX.CRM'], [b'1', b'2', b'3'])

    def test_el_que_fallo_no_se_mueve(self):
        original = __import__('ventas.crm_web', fromlist=['procesar_correo']).procesar_correo

        def falla_el_primero(mensaje):
            if 'ana@' in mensaje.get('From', ''):
                raise ValueError('correo raro')
            return original(mensaje)
        with patch('ventas.crm_web.procesar_correo', falla_el_primero):
            self.leer()
        self.assertEqual(ImapFalso.movidos, {'INBOX.CRM': [b'2']})
        self.assertIn(b'1', ImapFalso.correos)  # sigue en la bandeja (leído) para revisarlo a mano

    def test_carpeta_vacia_los_deja_en_la_bandeja(self):
        ConfigCRM.objects.update(correo_carpeta='')
        salida, _ = self.leer()
        self.assertIn('2 oportunidad(es)', salida)
        self.assertEqual(ImapFalso.movidos, {})
        self.assertEqual(sorted(ImapFalso.correos), [b'1', b'2'])
        self.assertEqual(ImapFalso.leidos, [b'1', b'2'])

    def test_carpeta_propia_con_tilde_y_buzon_sin_prefijo(self):
        ConfigCRM.objects.update(correo_carpeta='Ventas Perú')
        ImapFalso.carpetas = ['Sent', 'Trash']  # Gmail y otros: las carpetas no cuelgan de INBOX
        self.leer()
        self.assertEqual(list(ImapFalso.movidos), ['Ventas Per&APo-'])  # UTF-7 de IMAP

    def test_servidor_sin_move_copia_y_borra_solo_esos(self):
        ImapFalso.capacidades = b'IMAP4rev1 UIDPLUS'
        self.leer()
        self.assertEqual(ImapFalso.movidos, {'INBOX.CRM': [b'1', b'2']})
        self.assertEqual(ImapFalso.correos, {})
        self.assertIn('EXPUNGE', ImapFalso.comandos)  # UID EXPUNGE: no toca otros correos marcados para borrar

    def test_servidor_sin_move_ni_uidplus_solo_copia(self):
        ImapFalso.capacidades = b'IMAP4rev1'
        _, errores = self.leer()
        self.assertEqual(ImapFalso.movidos, {'INBOX.CRM': [b'1', b'2']})
        self.assertEqual(sorted(ImapFalso.correos), [b'1', b'2'])  # nunca un EXPUNGE general
        self.assertNotIn('EXPUNGE', ImapFalso.comandos)
        self.assertIn('siguen en la bandeja', errores)

    def test_si_no_se_puede_mover_la_oportunidad_queda(self):
        with patch.object(ImapFalso, 'create', return_value=('NO', [b'Permission denied'])):
            salida, errores = self.leer()
        self.assertIn('2 oportunidad(es)', salida)
        self.assertIn('no se pudieron mover', errores)
        self.assertEqual(ImapFalso.leidos, [b'1', b'2'])

    def test_la_pantalla_de_configuracion_muestra_la_carpeta(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        self.client.force_login(User.objects.create_superuser('crm13', 'crm13@t.com', 'x'))
        r = self.client.get(reverse('crm:configuracion'))
        self.assertContains(r, 'name="correo_carpeta"')
        self.assertContains(r, 'Carpeta del buzón para los correos del CRM')

    def test_la_carpeta_se_configura_en_el_crm(self):
        from ventas.crm_gestion import ConfigForm
        self.assertEqual(ConfigCRM.actual().correo_carpeta, 'CRM')
        self.assertIn('correo_carpeta', ConfigForm.base_fields)
        cfg = ConfigCRM.actual()
        cfg.correo_carpeta = 'CRM/../INBOX'
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            cfg.full_clean()
