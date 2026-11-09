"""v1.107: canales de entrada del CRM (WhatsApp, Messenger, Instagram, anuncios de Meta, chat web, Telegram, TikTok)
y la bandeja de mensajes."""
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from ventas.models import ConfigCRM, ConsentimientoDatos, ConversacionCanal, MensajeCanal, Oportunidad

from .tests_modulo_tesoreria import Base as BaseTesoreria


class Base(BaseTesoreria):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        self.admin = User.objects.create_superuser('admin', password='Clave-segura-9')


def firmar(secreto, cuerpo):
    return 'sha256=' + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def configurar(**campos):
    cfg = ConfigCRM.actual()
    for k, v in campos.items():
        setattr(cfg, k, v)
    cfg.save()
    return cfg


class WhatsAppCRMTest(Base):
    def setUp(self):
        super().setUp()
        from finanzas.models_rendicion import ConfigRendicion
        r = ConfigRendicion.actual()
        r.whatsapp_token, r.whatsapp_phone_id, r.whatsapp_app_secret = 'tok', '123', 'secreto'
        r.save()
        configurar(whatsapp_activo=True)

    def _post(self, mensajes, nombre='Rosa Quispe'):
        cuerpo = json.dumps({'entry': [{'changes': [{'value': {
            'contacts': [{'wa_id': '51911222333', 'profile': {'name': nombre}}], 'messages': mensajes}}]}]}).encode()
        return self.client.post(reverse('rendiciones:whatsapp'), cuerpo, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256=firmar('secreto', cuerpo))

    def test_prospecto_por_whatsapp_crea_la_oportunidad_y_se_responde(self):
        self.assertEqual(self._post([{'from': '51911222333', 'type': 'text',
                                      'text': {'body': 'Precio de 20 m3 de concreto'}}]).status_code, 200)
        o = Oportunidad.objects.get(origen='WHATSAPP')
        self.assertEqual((o.contacto, o.telefono), ('Rosa Quispe', '+51911222333'))
        self.assertIn('Precio de 20 m3', o.notas)
        self.assertTrue(o.actividades.filter(descripcion__contains='WhatsApp', hecha=False).exists())
        self._post([{'from': '51911222333', 'type': 'text', 'text': {'body': '¿Entregan en Ate?'}}])
        conv = ConversacionCanal.objects.get()
        self.assertEqual((Oportunidad.objects.count(), conv.mensajes.count(), conv.sin_leer), (1, 2, 2))
        from ventas import crm_canales
        with patch('ventas.crm_canales._http') as http:
            crm_canales.enviar(conv, 'Sí, entregamos en Ate.', self.admin)
        url, datos, token = http.call_args.args
        self.assertEqual((url.endswith('/123/messages'), datos['to'], datos['text']['body'], token),
                         (True, '51911222333', 'Sí, entregamos en Ate.', 'tok'))
        # pasadas 24 h del último mensaje del cliente, Meta no deja responder sin plantilla
        ConversacionCanal.objects.filter(pk=conv.pk).update(ultimo_entrante=timezone.now() - timedelta(hours=25))
        conv.refresh_from_db()
        with self.assertRaises(crm_canales.ErrorCanal):
            crm_canales.enviar(conv, 'Hola de nuevo')

    def test_el_numero_de_un_usuario_sigue_en_rendiciones(self):
        from core.models import PerfilUsuario
        from finanzas.models_rendicion import ConfigRendicion
        ConfigRendicion.objects.update(whatsapp_activo=True)
        PerfilUsuario.objects.update_or_create(usuario=self.admin, defaults={'celular': '911 222 333'})
        with patch('finanzas.views_rendicion_movil.procesar_mensaje') as gasto:
            self._post([{'from': '51911222333', 'type': 'image', 'image': {'id': 'm1'}}])
        self.assertTrue(gasto.called)
        self.assertFalse(ConversacionCanal.objects.exists())

    def test_mensaje_a_una_oportunidad_cerrada_abre_otra(self):
        self._post([{'from': '51911222333', 'type': 'text', 'text': {'body': 'Hola'}}])
        Oportunidad.objects.update(etapa='PERDIDA')
        self._post([{'from': '51911222333', 'type': 'text', 'text': {'body': 'Quiero otra cotización'}}])
        self.assertEqual(Oportunidad.objects.filter(etapa='PROSPECTO').count(), 1)
        self.assertEqual(ConversacionCanal.objects.get().oportunidad.etapa, 'PROSPECTO')


class MetaTest(Base):
    def setUp(self):
        super().setUp()
        self.cfg = configurar(meta_activo=True, meta_leads=True, meta_token='pagina', meta_app_secret='app')
        self.url = reverse('crm_meta', args=['default'])

    def _post(self, cuerpo, firma=None):
        datos = json.dumps(cuerpo).encode()
        return self.client.post(self.url, datos, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256=firma or firmar('app', datos))

    def test_verificacion_y_firma(self):
        r = self.client.get(self.url, {'hub.mode': 'subscribe', 'hub.verify_token': self.cfg.meta_verify_token,
                                       'hub.challenge': '77'})
        self.assertEqual(r.content, b'77')
        self.assertEqual(self.client.get(self.url, {'hub.mode': 'subscribe', 'hub.verify_token': 'x'}).status_code,
                         403)
        self.assertEqual(self._post({'object': 'page'}, firma='sha256=mala').status_code, 403)

    def test_messenger_e_instagram_entran_a_la_bandeja(self):
        with patch('ventas.crm_canales._http', return_value={'name': 'Luis Torres'}):
            self._post({'object': 'page', 'entry': [{'id': 'PAGINA', 'messaging': [
                {'sender': {'id': 'psid-1'}, 'message': {'mid': 'a', 'text': 'Info del depa 301'}},
                {'sender': {'id': 'PAGINA'}, 'message': {'mid': 'b', 'text': 'eco', 'is_echo': True}}]}]})
            self._post({'object': 'instagram', 'entry': [{'id': 'IG', 'messaging': [
                {'sender': {'id': 'igsid-9'}, 'message': {'mid': 'c', 'text': '¿Precio?'}}]}]})
        self.assertEqual(sorted(ConversacionCanal.objects.values_list('canal', 'nombre')),
                         [('INSTAGRAM', 'Luis Torres'), ('MESSENGER', 'Luis Torres')])
        self.assertEqual(sorted(Oportunidad.objects.values_list('origen', flat=True)), ['INSTAGRAM', 'MESSENGER'])
        from ventas import crm_canales
        conv = ConversacionCanal.objects.get(canal='MESSENGER')
        with patch('ventas.crm_canales._http') as http:
            crm_canales.enviar(conv, 'Le escribo en un momento', self.admin)
        url, datos, token = http.call_args.args
        self.assertEqual((url.endswith('/me/messages'), datos['recipient']['id'], token), (True, 'psid-1', 'pagina'))

    def test_formulario_de_anuncio(self):
        lead = {'field_data': [{'name': 'full_name', 'values': ['Ana Ríos']},
                               {'name': 'phone_number', 'values': ['+51 999 888 777']},
                               {'name': 'email', 'values': ['ana@correo.pe']}], 'ad_name': 'Depas Surco'}
        with patch('ventas.crm_canales._http', return_value=lead):
            self._post({'object': 'page', 'entry': [{'changes': [{'field': 'leadgen',
                                                                  'value': {'leadgen_id': '555'}}]}]})
        o = Oportunidad.objects.get(origen='META_ADS')
        self.assertEqual((o.contacto, o.telefono, o.email), ('Ana Ríos', '+51 999 888 777', 'ana@correo.pe'))
        self.assertIn('Depas Surco', o.notas)


class TelegramTest(Base):
    def test_bot_recibe_saluda_y_responde(self):
        cfg = configurar(telegram_activo=True, telegram_token='123:ABC')
        url = reverse('crm_telegram', args=['default'])
        update = {'message': {'chat': {'id': 4455, 'type': 'private'},
                              'from': {'first_name': 'Carla', 'last_name': 'Díaz'}, 'text': '/start'}}
        self.assertEqual(self.client.post(url, json.dumps(update), content_type='application/json',
                                          HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='otra').status_code, 403)
        with patch('ventas.crm_canales._http') as http:
            self.client.post(url, json.dumps(update), content_type='application/json',
                             HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=cfg.telegram_secreto)
            update['message']['text'] = 'Necesito 500 bloquetas'
            self.client.post(url, json.dumps(update), content_type='application/json',
                             HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=cfg.telegram_secreto)
        self.assertEqual(http.call_args.args[1], {'chat_id': 4455, 'text': cfg.chat_saludo})  # saludo al /start
        conv = ConversacionCanal.objects.get(canal='TELEGRAM')
        self.assertEqual((conv.nombre, conv.oportunidad.origen), ('Carla Díaz', 'TELEGRAM'))
        self.assertEqual(list(conv.mensajes.values_list('entrante', flat=True)), [True, False, True])


class TikTokTest(Base):
    def test_formulario_de_tiktok(self):
        cfg = configurar(tiktok_activo=True)
        self.assertEqual(self.client.post(reverse('crm_tiktok', args=['default', 'mala']), '{}',
                                          content_type='application/json').status_code, 404)
        cuerpo = {'lead_id': '7001', 'ad_name': 'Campaña noviembre', 'answers': [
            {'name': 'Nombre', 'value': 'Pedro Salas'}, {'name': 'Teléfono', 'value': '987000111'},
            {'name': 'Empresa', 'value': 'Constructora PS'}]}
        r = self.client.post(reverse('crm_tiktok', args=['default', cfg.tiktok_clave]), json.dumps(cuerpo),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        o = Oportunidad.objects.get(origen='TIKTOK')
        self.assertEqual((o.contacto, o.telefono, o.prospecto), ('Pedro Salas', '987000111', 'Constructora PS'))
        r = self.client.post(reverse('crm_tiktok', args=['default', cfg.tiktok_clave]),
                             json.dumps({'answers': [{'name': 'Comentario', 'value': 'hola'}]}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)  # sin nombre, teléfono ni correo no se crea nada


class ChatWebTest(Base):
    def test_chat_de_la_pagina(self):
        cfg = configurar(chat_activo=True)
        api = reverse('crm_chat', args=['default', cfg.token_web])
        widget = self.client.get(reverse('crm_chat_widget', args=['default', cfg.token_web]))
        self.assertEqual(widget['Content-Type'], 'application/javascript; charset=utf-8')
        self.assertIn(api, widget.content.decode())
        r = self.client.post(api, {'texto': 'Hola'})
        self.assertEqual(r.status_code, 400)  # el primer mensaje pide nombre, contacto y consentimiento
        self.assertEqual(r['Access-Control-Allow-Origin'], '*')
        r = self.client.post(api, {'texto': 'Quiero una visita', 'nombre': 'Mario', 'telefono': '955111222',
                                   'consentimiento': '1'}).json()
        c = r['c']
        o = Oportunidad.objects.get(origen='CHAT_WEB')
        self.assertTrue(ConsentimientoDatos.objects.filter(oportunidad=o, canal='CHAT_WEB').exists())
        conv = ConversacionCanal.objects.get(externo=c)
        from ventas import crm_canales
        crm_canales.enviar(conv, 'Claro, ¿qué día le acomoda?', self.admin)  # el chat no llama a ningún servicio
        nuevos = self.client.get(api, {'c': c, 'desde': r['mensajes'][-1]['id']}).json()['mensajes']
        self.assertEqual([(m['yo'], m['texto']) for m in nuevos], [(False, 'Claro, ¿qué día le acomoda?')])
        self.assertEqual(self.client.get(api, {'c': 'inventada'}).status_code, 404)
        self.client.post(api, {'texto': 'El jueves', 'c': c})
        self.assertEqual(Oportunidad.objects.count(), 1)
        self.assertEqual(self.client.get(reverse('crm_chat', args=['default', 'otra'])).status_code, 404)


class BandejaTest(Base):
    def test_bandeja_respuesta_y_permisos(self):
        from ventas import crm_canales
        configurar(chat_activo=True)
        conv = crm_canales.recibir('CHAT_WEB', 'v1', 'Hola', nombre='Lucía', telefono='999111000')
        vende = self.usuario('vende', ['crm'], acciones=['crm.registrar'])
        Oportunidad.objects.update(responsable=vende)
        otro = self.usuario('otro', ['crm'], acciones=['crm.registrar'])
        self.client.force_login(vende)
        r = self.client.get(reverse('crm:bandeja'))
        self.assertContains(r, 'Lucía')
        self.assertEqual(self.client.get(reverse('crm:conversacion', args=[conv.pk])).status_code, 200)
        conv.refresh_from_db()
        self.assertEqual(conv.sin_leer, 0)
        self.client.post(reverse('crm:conversacion', args=[conv.pk]), {'texto': 'Buenas tardes'})
        self.assertEqual(MensajeCanal.objects.filter(entrante=False, usuario=vende).count(), 1)
        self.assertContains(self.client.get(reverse('crm:oportunidad', args=[conv.oportunidad_id])),
                            reverse('crm:conversacion', args=[conv.pk]))
        self.client.force_login(otro)
        self.assertNotContains(self.client.get(reverse('crm:bandeja')), 'Lucía')
        self.assertEqual(self.client.get(reverse('crm:conversacion', args=[conv.pk])).status_code, 404)
        self.client.force_login(self.admin)
        r = self.client.get(reverse('crm:configuracion'))
        self.assertContains(r, 'Canales de entrada')
        self.assertContains(r, reverse('crm_meta', args=['default']))
