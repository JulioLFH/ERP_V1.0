"""Checklist v1.75.0 (18h10) SST-01 y SST-02: registros obligatorios con validación por especialista (registro firmado,
bloqueo y versiones), entrega de EPP como salida de almacén, IPERC, accidentes con plazo de notificación e índices."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contabilidad.models import CentroCosto
from core import sustentos
from core.alertas import calcular
from core.models import Almacen, Producto, ReglaValidacion
from core.registros import _valor
from planillas.models import Trabajador

from . import servicios as srv
from .models import EntregaEPP, Evento, ExamenMedico, HorasTrabajadas, Iperc
from .views import TIPOS

HOY = timezone.localdate()


def pdf(nombre='registro.pdf'):
    return SimpleUploadedFile(nombre, b'%PDF-1.4 firmado', 'application/pdf')


class SSTTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.centro = CentroCosto.objects.create(codigo='PROD', nombre='Producción', tipo='PRODUCCION')
        cls.t = Trabajador.objects.create(numero_doc='45678912', apellido_paterno='QUISPE', nombres='ANA',
                                          fecha_ingreso=HOY - timedelta(days=400), centro_costo=cls.centro)

    def setUp(self):
        self.client.force_login(self.user)

    def accion(self, obj, **datos):
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(obj)
        return self.client.post(reverse('validacion_accion', args=[ct.pk, obj.pk]), datos)

    def test_emo_validacion_bloqueo_y_version(self):
        r = self.client.post(reverse('sst:registro_nuevo') + '?tipo=emo', {
            'trabajador': self.t.pk, 'tipo': 'PERIODICO', 'fecha': HOY, 'vence': HOY + timedelta(days=365),
            'resultado': 'APTO', 'clinica': 'Clínica Ocupacional'})
        e = ExamenMedico.objects.get()
        self.assertRedirects(r, reverse('sst:registro', args=['emo', e.pk]))
        self.accion(e, accion='enviar')
        e.refresh_from_db()
        self.assertEqual(e.estado_validacion, 'POR_VALIDAR')  # el EMO lo valida el médico ocupacional
        self.accion(e, accion='validar', nombre='Dra. Rosa Ruiz', cargo='Médico ocupacional', registro='CMP 12345')
        e.refresh_from_db()
        self.assertEqual(e.estado_validacion, 'POR_VALIDAR')  # sin el registro firmado no se valida
        self.accion(e, accion='validar', nombre='Dra. Rosa Ruiz', cargo='Médico ocupacional', registro='CMP 12345',
                    archivo=pdf())
        e.refresh_from_db()
        self.assertEqual((e.estado_validacion, e.validador_registro), ('VALIDADO', 'CMP 12345'))
        self.assertTrue(sustentos.tiene(e))
        # validado: queda bloqueado
        self.client.post(reverse('sst:registro', args=['emo', e.pk]), {
            'accion': 'datos', 'trabajador': self.t.pk, 'tipo': 'PERIODICO', 'fecha': HOY, 'resultado': 'NO_APTO'})
        e.refresh_from_db()
        self.assertEqual(e.resultado, 'APTO')
        # un cambio es una versión nueva
        self.accion(e, accion='version', motivo='Corrige la fecha de vencimiento')
        nueva = ExamenMedico.objects.get(version=2)
        e.refresh_from_db()
        self.assertEqual((nueva.estado_validacion, nueva.version_anterior, e.vigente),
                         ('BORRADOR', e, False))
        r = self.client.get(reverse('sst:registros') + '?tipo=emo')
        self.assertEqual([o.pk for o, _ in r.context['filas']], [nueva.pk])  # sin la versión anterior
        self.assertContains(self.client.get(reverse('sst:registro', args=['emo', nueva.pk])), 'versión 2')

    def test_regla_sin_validacion_de_especialista(self):
        ReglaValidacion.objects.create(tipo='EMO', requiere=False)
        e = ExamenMedico.objects.create(trabajador=self.t, fecha=HOY, vence=HOY + timedelta(days=10))
        self.accion(e, accion='enviar')
        e.refresh_from_db()
        self.assertEqual(e.estado_validacion, 'VALIDADO')
        self.assertEqual(self.client.get(reverse('validacion_reglas')).status_code, 200)
        venc = srv.vencimientos(30)['Exámenes médicos']
        self.assertEqual([x[0] for x in venc], [e])

    def test_entrega_de_epp_sale_del_almacen(self):
        almacen = Almacen.objects.create(codigo='ALM01', nombre='Almacén central', es_principal=True)
        casco = Producto.objects.create(nombre='Casco de seguridad', clase='SUMINISTRO', es_epp=True)
        casco.mover_stock(D('10'), 'Inicial', costo=D('25'), almacen=almacen, origen='AJUSTE', concepto='INICIAL')
        self.client.post(reverse('sst:registro_nuevo') + '?tipo=epp', {
            'trabajador': self.t.pk, 'fecha': HOY, 'almacen': almacen.pk})
        entrega = EntregaEPP.objects.get()
        url = reverse('sst:registro', args=['epp', entrega.pk])
        self.client.post(url, {'accion': 'linea_epp', 'producto': casco.pk, 'cantidad': '2', 'reposicion_dias': '180'})
        self.client.post(url, {'accion': 'entregar_epp'})
        entrega.refresh_from_db()
        casco.refresh_from_db()
        self.assertEqual((entrega.operacion.estado, entrega.operacion.centro_costo, casco.stock),
                         ('CONFIRMADO', self.centro, D('8')))
        self.assertEqual(entrega.lineas.get().reponer_el, HOY + timedelta(days=180))
        r = self.client.post(url, {'accion': 'entregar_epp'})
        self.assertEqual(casco.stock, D('8'))  # no sale dos veces

    def test_accidentes_plazo_indices_y_alertas(self):
        siguiente = (HOY.replace(day=1) + timedelta(days=32)).replace(day=1)
        incapacitante = Evento.objects.create(tipo='ACC_INCAPACITANTE', fecha=HOY, trabajador=self.t,
                                              descripcion='Caída al mismo nivel', dias_descanso=5)
        self.assertEqual(incapacitante.plazo_notificacion,
                         date(siguiente.year, siguiente.month, monthrange(siguiente.year, siguiente.month)[1]))
        mortal = Evento.objects.create(tipo='ACC_MORTAL', fecha=HOY, descripcion='Caída de altura', dias_descanso=0)
        self.assertEqual(mortal.plazo_notificacion, HOY + timedelta(days=1))
        Evento.objects.create(tipo='INCIDENTE', fecha=HOY, descripcion='Herramienta cayó sin herir a nadie')
        HorasTrabajadas.objects.create(periodo=HOY.strftime('%Y%m'), horas=D('10000'))
        filas, total = srv.indices(HOY.year)
        mes = filas[HOY.month - 1]
        self.assertEqual((mes['accidentes'], mes['dias'], mes['incidentes']), (2, 5, 1))
        self.assertEqual((mes['if'], mes['ig'], mes['ia']), (D('200.00'), D('500.00'), D('100.00')))
        titulos = [a['titulo'] for a in calcular(self.user)]
        self.assertIn('Accidentes por notificar a la autoridad', titulos)
        self.assertContains(self.client.get(reverse('sst:indices')), '200.00')
        url = reverse('sst:registro', args=['eventos', incapacitante.pk])
        self.client.post(reverse('calidad:accion_nueva', args=[
            __import__('django.contrib.contenttypes.models', fromlist=['x']).ContentType.objects.get_for_model(
                Evento).pk, incapacitante.pk]), {'tipo': 'CORRECTIVA', 'descripcion': 'Señalizar el piso mojado',
                                                 'plazo': HOY + timedelta(days=7), 'next': url})
        self.assertEqual(incapacitante.acciones.count(), 1)
        self.assertContains(self.client.get(url), 'Señalizar el piso mojado')

    def test_iperc_y_pantallas(self):
        ip = Iperc.objects.create(proceso='Vaciado de concreto', fecha=HOY)
        url = reverse('sst:registro', args=['iperc', ip.pk])
        self.client.post(url, {'accion': 'linea_iperc', 'tarea': 'Encofrado', 'peligro': 'Trabajo en altura',
                               'riesgo': 'Caída', 'probabilidad': '3', 'severidad': '4', 'controles': 'Arnés',
                               'probabilidad_residual': '1', 'severidad_residual': '4'})
        linea = ip.lineas.get()
        self.assertEqual((linea.nivel, linea.clasificar(linea.nivel)), (12, 'Intolerable'))
        self.assertContains(self.client.get(url), 'Intolerable')
        self.assertEqual(self.client.get(reverse('sst:tablero')).status_code, 200)
        for slug in TIPOS:
            self.assertEqual(self.client.get(reverse('sst:registros') + f'?tipo={slug}').status_code, 200, slug)
            self.assertEqual(self.client.get(reverse('sst:registro_nuevo') + f'?tipo={slug}').status_code, 200, slug)
            self.assertEqual(self.client.get(reverse('sst:registros') + f'?tipo={slug}&formato=excel').status_code,
                             200, slug)
        self.assertEqual(_valor(ip, 'lineas.count'), 1)
