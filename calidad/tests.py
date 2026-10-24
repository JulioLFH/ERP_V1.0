"""Checklist v1.75.0 (18h10) EXC-01 y EXC-02: no conformidades con análisis de causa y acciones con verificación de
eficacia, auditorías con hallazgos, documentos controlados con aprobación y versiones, indicadores por área con
semáforo (manuales con evidencia y automáticos) y sugerencias de mejora."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import sustentos

from . import servicios as srv
from .models import Accion, Auditoria, DocumentoControlado, Indicador, Medicion, NoConformidad
from .views import TIPOS

HOY = timezone.localdate()
P = HOY.strftime('%Y%m')


class CalidadTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)

    def test_no_conformidad_analisis_acciones_y_cierre(self):
        self.client.post(reverse('calidad:registro_nuevo') + '?tipo=no-conformidades', {
            'fecha': HOY, 'origen': 'RECLAMO', 'descripcion': 'Producto entregado con golpes', 'metodo': '5_PORQUES'})
        nc = NoConformidad.objects.get()
        self.assertTrue(nc.codigo.startswith('NC-'))
        url = reverse('calidad:registro', args=['no-conformidades', nc.pk])
        self.client.post(url, {'accion': 'analisis', 'porque_1': 'Se golpeó en el camión',
                               'porque_2': 'No iba sujeto', 'causa_raiz': 'Falta procedimiento de estiba'})
        nc.refresh_from_db()
        self.assertEqual((nc.estado, nc.porques[1]), ('TRATAMIENTO', 'No iba sujeto'))
        ct = ContentType.objects.get_for_model(NoConformidad)
        self.client.post(reverse('calidad:accion_nueva', args=[ct.pk, nc.pk]), {
            'tipo': 'CORRECTIVA', 'descripcion': 'Procedimiento de estiba', 'plazo': HOY - timedelta(days=1),
            'next': url})
        a = Accion.objects.get()
        self.assertTrue(a.vencida)
        self.assertEqual([x.pk for x in srv.acciones_vencidas()], [a.pk])
        with self.assertRaisesMessage(srv.ErrorCalidad, 'Faltan verificar'):
            srv.cerrar_nc(nc, True)
        accion = reverse('calidad:accion', args=[a.pk])
        self.client.post(accion, {'estado': 'VERIFICADA', 'verificacion': 'x', 'next': url})  # aún no ejecutada
        a.refresh_from_db()
        self.assertEqual(a.estado, 'PENDIENTE')
        self.client.post(accion, {'estado': 'HECHA', 'next': url})
        self.client.post(accion, {'estado': 'VERIFICADA', 'verificacion': 'Sin golpes en 30 entregas',
                                  'next': url})
        a.refresh_from_db()
        self.assertEqual((a.estado, a.eficaz), ('VERIFICADA', True))
        self.client.post(url, {'accion': 'cerrar_nc', 'eficaz': '1'})
        nc.refresh_from_db()
        self.assertEqual((nc.estado, nc.eficaz), ('CERRADA', True))
        self.assertContains(self.client.get(reverse('calidad:acciones')), 'Procedimiento de estiba')

    def test_auditoria_hallazgo_a_no_conformidad(self):
        au = Auditoria.objects.create(norma='ISO45001', fecha=HOY, auditor_lider='Ing. Pérez')
        url = reverse('calidad:registro', args=['auditorias', au.pk])
        self.client.post(url, {'accion': 'hallazgo', 'tipo': 'NC_MENOR', 'requisito': '8.1.2',
                               'descripcion': 'IPERC desactualizado en almacén'})
        h = au.hallazgos.get()
        self.client.post(url, {'accion': 'nc_hallazgo', 'id': h.pk})
        h.refresh_from_db()
        self.assertEqual((h.no_conformidad.origen, h.no_conformidad.norma), ('AUDITORIA', 'ISO45001'))
        self.assertContains(self.client.get(url), h.no_conformidad.codigo)

    def test_documento_controlado_aprobacion_y_version(self):
        doc = DocumentoControlado.objects.create(codigo='PR-SST-01', titulo='Trabajos en altura')
        ct = ContentType.objects.get_for_model(DocumentoControlado)
        accion = reverse('validacion_accion', args=[ct.pk, doc.pk])
        self.client.post(accion, {'accion': 'enviar'})
        self.client.post(accion, {'accion': 'validar', 'nombre': 'Gerente general', 'cargo': 'Aprobador',
                                  'archivo': SimpleUploadedFile('pr.pdf', b'%PDF-1.4', 'application/pdf')})
        doc.refresh_from_db()
        self.assertEqual(doc.estado_validacion, 'VALIDADO')
        self.assertTrue(sustentos.tiene(doc))
        self.client.post(accion, {'accion': 'version', 'motivo': 'Nueva norma'})
        v2 = DocumentoControlado.objects.get(version=2)
        self.assertEqual((v2.codigo, v2.titulo, v2.estado_validacion), ('PR-SST-01', 'Trabajos en altura',
                                                                        'BORRADOR'))

    def test_indicadores_semaforo_y_tablero(self):
        ind = Indicador.objects.create(objetivo='Satisfacer al cliente', nombre='Pedidos a tiempo', unidad='%',
                                       meta=D('95'), sentido='MAYOR', fuente='MANUAL')
        self.assertEqual((ind.semaforo(D('96')), ind.semaforo(D('90')), ind.semaforo(D('80'))),
                         ('verde', 'ambar', 'rojo'))
        url = reverse('calidad:registro', args=['indicadores', ind.pk])
        self.client.post(url, {'accion': 'medicion', 'periodo': f'{HOY:%Y-%m}', 'valor': '92.5',
                               'evidencia': SimpleUploadedFile('ev.pdf', b'%PDF-1.4', 'application/pdf')})
        m = Medicion.objects.get()
        self.assertEqual(m.valor, D('92.5'))
        self.assertTrue(sustentos.tiene(m))
        auto = Indicador.objects.create(objetivo='Mejorar procesos', nombre='No conformidades abiertas',
                                        meta=D('2'), sentido='MENOR', fuente='NC_ABIERTAS')
        NoConformidad.objects.create(descripcion='Abierta 1')
        self.assertEqual(srv.valor(auto, P), D('1'))
        r = self.client.get(reverse('calidad:tablero'))
        self.assertContains(r, 'Pedidos a tiempo')
        self.assertContains(r, 'sem-ambar')
        for slug in TIPOS:
            self.assertEqual(self.client.get(reverse('calidad:registros') + f'?tipo={slug}').status_code, 200, slug)
            self.assertEqual(self.client.get(reverse('calidad:registro_nuevo') + f'?tipo={slug}').status_code, 200)
        for f in ('DIAS_COBRO', 'ACCIONES_VENCIDAS', 'SST_IF', 'SST_EMO_VENCIDOS', 'OEE'):
            srv.valor_automatico(f, P)  # se calculan sin error aunque no haya datos
