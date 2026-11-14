"""Tickets de soporte (v1.111): base central compartida por todas las empresas, alcance por usuario, empresa y grupo,
conversación, adjuntos y «Reportar un problema»."""
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Empresa
from erp.empresas import activar, restaurar

from .models import Adjunto, Comentario, Ticket

CLAVE = 'Clave-segura-9'


@override_settings(TICKETS_INSTALACION='prueba', TICKETS_AVISO=['soporte@ceiba.test'])
class Base(TestCase):
    databases = {'default', 'empresa2', 'tickets'}

    def setUp(self):
        cache.clear()
        Empresa.objects.create(ruc='20100000001', razon_social='HOLDING PRINCIPAL S.A.C.')
        token = activar('empresa2')
        try:
            Empresa.objects.create(ruc='20600000005', razon_social='FILIAL NORTE S.A.C.')
        finally:
            restaurar(token)
        self.admin = User.objects.create_superuser('gerente', 'gerente@holding.pe', CLAVE, first_name='Ana',
                                                   last_name='Gerente')
        self.vendedor = User.objects.create_user('vendedor', 'v@holding.pe', CLAVE, first_name='Luis')

    def ingresar(self, usuario, empresa='default'):
        self.client.logout()
        return self.client.post(reverse('login'), {'username': usuario, 'password': CLAVE, 'empresa': empresa})

    def crear(self, **extra):
        datos = {'tipo': 'PROBLEMA', 'prioridad': 'ALTA', 'modulo': 'ventas', 'titulo': 'No emite la factura F001-120',
                 'descripcion': 'Al guardar la factura sale un error y no se registra.'}
        datos.update(extra)
        return self.client.post(reverse('soporte:nuevo'), datos)


class CrearTicketTest(Base):
    def test_ticket_va_a_la_base_central_con_su_origen(self):
        self.ingresar('vendedor')
        archivo = SimpleUploadedFile('captura.png', b'\x89PNG' + b'0' * 100, content_type='image/png')
        r = self.crear(adjuntos=[archivo])
        t = Ticket.objects.using('tickets').get()
        self.assertRedirects(r, reverse('soporte:detalle', args=[t.pk]))
        self.assertEqual(t.codigo, f'TK-{t.pk:06d}')
        self.assertEqual((t.instalacion, t.empresa_alias, t.empresa_ruc), ('prueba', 'default', '20100000001'))
        self.assertEqual((t.grupo_ruc, t.usuario, t.usuario_nombre), ('20100000001', 'vendedor', 'Luis'))
        self.assertEqual(t.tipo, 'PROBLEMA')
        self.assertEqual(t.adjuntos.get().nombre, 'captura.png')
        self.assertEqual(len(mail.outbox), 0)  # sin correo de Ceiba configurado no falla: solo no avisa
        # los tickets no se guardan en la base de la empresa
        from django.db import connections
        self.assertNotIn('soporte_ticket', connections['default'].introspection.table_names())

    def test_validaciones(self):
        self.ingresar('vendedor')
        r = self.crear(descripcion='corto')
        self.assertContains(r, 'al menos 15 caracteres')
        malo = SimpleUploadedFile('virus.exe', b'MZ', content_type='application/octet-stream')
        r = self.crear(adjuntos=[malo])
        self.assertContains(self.client.get(reverse('soporte:lista')), 'Sin tickets')
        self.assertEqual(Ticket.objects.count(), 0)
        grande = SimpleUploadedFile('enorme.pdf', b'0' * (Adjunto.MAXIMO + 1), content_type='application/pdf')
        self.crear(adjuntos=[grande])
        self.assertEqual(Ticket.objects.count(), 0)

    def test_reportar_un_problema_crea_ticket(self):
        self.ingresar('vendedor')
        r = self.client.post(reverse('soporte:reportar'), {'descripcion': 'El reporte de ventas no carga.',
                                                           'url': '/ventas/'})
        self.assertRedirects(r, '/ventas/', fetch_redirect_response=False)
        t = Ticket.objects.get()
        self.assertEqual((t.tipo, t.url, t.titulo), ('PROBLEMA', '/ventas/', 'El reporte de ventas no carga.'))


class AlcanceTest(Base):
    def test_usuario_ve_solo_los_suyos_y_el_admin_los_de_su_empresa(self):
        self.ingresar('vendedor')
        self.crear(titulo='Ticket del vendedor')
        self.ingresar('gerente')
        self.crear(titulo='Ticket del gerente')
        r = self.client.get(reverse('soporte:lista'))
        self.assertContains(r, 'Ticket del vendedor')
        self.assertContains(r, 'Ticket del gerente')
        self.ingresar('vendedor')
        r = self.client.get(reverse('soporte:lista'))
        self.assertContains(r, 'Ticket del vendedor')
        self.assertNotContains(r, 'Ticket del gerente')
        ajeno = Ticket.objects.get(titulo='Ticket del gerente')
        self.assertEqual(self.client.get(reverse('soporte:detalle', args=[ajeno.pk])).status_code, 404)

    def test_otra_instalacion_no_se_ve(self):
        Ticket.objects.create(titulo='De otro ERP', descripcion='x' * 20, instalacion='otra', empresa_alias='default',
                              usuario='gerente')
        self.ingresar('gerente')
        self.assertNotContains(self.client.get(reverse('soporte:lista')), 'De otro ERP')

    def test_corporativo_ve_todo_el_grupo(self):
        self.ingresar('gerente')
        self.crear(titulo='Ticket de la principal')
        self.client.post(reverse('cambiar_empresa'), {'empresa': 'empresa2'})
        self.crear(titulo='Ticket de la filial')
        t = Ticket.objects.get(titulo='Ticket de la filial')
        self.assertEqual((t.empresa_alias, t.empresa_ruc, t.grupo_ruc), ('empresa2', '20600000005', '20100000001'))
        r = self.client.get(reverse('soporte:lista'))
        self.assertContains(r, 'Ticket de la filial')
        self.assertNotContains(r, 'Ticket de la principal')
        r = self.client.get(reverse('soporte:lista') + '?ver=grupo')
        self.assertContains(r, 'Ticket de la filial')
        self.assertContains(r, 'Ticket de la principal')


class ConversacionTest(Base):
    def test_responder_cerrar_y_reabrir(self):
        self.ingresar('vendedor')
        self.crear()
        t = Ticket.objects.get()
        Comentario.objects.create(ticket=t, autor='soporte', de_soporte=True, texto='¿Qué mensaje sale?')
        t.estado = 'ESPERA'
        t.save()
        url = reverse('soporte:detalle', args=[t.pk])
        self.assertContains(self.client.get(url), 'Soporte Ceiba')
        self.client.post(url, {'accion': 'comentar', 'texto': 'Sale «Serie no válida».'})
        t.refresh_from_db()
        self.assertEqual(t.estado, 'REVISION')  # respondió el cliente: vuelve a soporte
        self.assertEqual(t.comentarios.count(), 2)
        self.client.post(url, {'accion': 'cerrar'})
        t.refresh_from_db()
        self.assertEqual(t.estado, 'CERRADO')
        self.assertIsNotNone(t.cerrado_en)
        self.client.post(url, {'accion': 'reabrir'})
        t.refresh_from_db()
        self.assertEqual(t.estado, 'REVISION')

    def test_adjunto_solo_para_quien_ve_el_ticket(self):
        self.ingresar('gerente')
        self.crear(adjuntos=[SimpleUploadedFile('nota.txt', b'hola', content_type='text/plain')])
        a = Adjunto.objects.get()
        r = self.client.get(reverse('soporte:adjunto', args=[a.pk]))
        self.assertEqual(r.content, b'hola')
        self.assertIn('attachment', r['Content-Disposition'])
        self.ingresar('vendedor')
        self.assertEqual(self.client.get(reverse('soporte:adjunto', args=[a.pk])).status_code, 404)

    def test_menu_muestra_soporte(self):
        self.ingresar('vendedor')
        r = self.client.get(reverse('home'))
        self.assertContains(r, 'Soporte y tickets')
        self.assertContains(r, reverse('soporte:reportar'))
