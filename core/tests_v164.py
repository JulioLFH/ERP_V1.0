"""v1.64, checklist v1.56 12:53: portal de proveedores en la pantalla de entrada (PORT-08), maestro único de equipos y
flota (FLO-01) y documentos con vencimiento que bloquean la guía (FLO-04)."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from flota.models import DocumentoFlota
from logistica.models import Conductor, Vehiculo
from produccion.models import Equipo

from .models import Almacen, Producto

D = Decimal
HOY = timezone.localdate()
ITEMS = {'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '1000'}


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v164', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.admin)


class PortalEnInicioTest(Base):
    def test_icono_con_contadores_y_tablero(self):
        r = self.client.get(reverse('home'))
        self.assertContains(r, 'Portal de proveedores')
        r = self.client.get(reverse('compras:portal_tablero'))
        self.assertContains(r, 'Facturas por revisar')
        self.assertContains(r, 'HES por liberar')
        self.assertContains(r, 'OC enviadas sin confirmar')


class FlotaTest(Base):
    def test_vehiculo_es_equipo_y_documentos_vencidos_bloquean_la_guia(self):
        # el vehículo nuevo de Logística es un equipo del maestro único
        self.client.post(reverse('logistica:vehiculo_nuevo'), {'placa': 'F1L-064', 'marca': 'Volvo', 'modelo': 'FH',
                                                               'certificado': '', 'activo': 'on'})
        v = Vehiculo.objects.get(placa='F1L-064')
        self.assertEqual((v.equipo.tipo, v.equipo.placa, v.equipo.medidor), ('VEHICULO', 'F1L-064', 'KM'))
        # y un equipo de tipo vehículo creado en la flota aparece en Logística para las guías
        self.client.post(reverse('flota:equipo_nuevo'), {
            'accion': 'equipo', 'codigo': 'MC-01', 'nombre': 'Camión 2', 'tipo': 'VEHICULO', 'estado': 'OPERATIVO',
            'placa': 'z9y 864', 'marca_modelo': 'Isuzu NPR', 'medidor': 'KM', 'activo': 'on'})
        self.assertTrue(Vehiculo.objects.filter(placa='Z9Y864', equipo__codigo='MC-01').exists())
        for nombre in ('flota:equipo', 'manufactura:equipo'):  # la misma ficha desde Logística y Manufactura
            self.assertContains(self.client.get(reverse(nombre, args=[v.equipo_id])), 'Documentos (SOAT')
        # SOAT vencido registrado en la ficha
        self.client.post(reverse('flota:equipo', args=[v.equipo_id]), {
            'accion': 'documento', 'doc-tipo': 'SOAT', 'doc-numero': 'S-1', 'doc-emisor': 'Rímac',
            'doc-emision': (HOY - timedelta(days=400)).isoformat(),
            'doc-vencimiento': (HOY - timedelta(days=35)).isoformat(), 'doc-costo': '450', 'doc-bloquea': 'on'})
        self.assertEqual(DocumentoFlota.objects.get(equipo=v.equipo).tipo, 'SOAT')
        conductor = Conductor.objects.first()
        conductor.licencia_vencimiento = HOY - timedelta(days=1)
        conductor.save()
        p = Producto.objects.get(codigo='P003')
        datos = {'serie': '', 'motivo_traslado': '04', 'descripcion_motivo': '', 'modalidad': '02', 'venta': '',
                 'compra': '', 'transportista': '', 'efecto_stock': 'NINGUNO', 'almacen_origen': Almacen.principal().pk,
                 'almacen_destino': '', 'fecha_emision': HOY.isoformat(), 'fecha_traslado': HOY.isoformat(),
                 'destinatario': '', 'partida_ubigeo': '150131', 'partida_direccion': 'Av. Javier Prado 123',
                 'llegada_ubigeo': '070101', 'llegada_direccion': 'Av. Argentina 2450', 'vehiculo': v.pk,
                 'conductor': conductor.pk, 'peso_bruto': '10', 'unidad_peso': 'KGM', 'numero_bultos': '1',
                 'doc_relacionado': '', 'observaciones': '', 'items-TOTAL_FORMS': '1', 'items-0-producto': p.pk,
                 'items-0-descripcion': p.nombre, 'items-0-unidad': 'NIU', 'items-0-cantidad': '1', **ITEMS}
        r = self.client.post(reverse('logistica:nueva') + '?tipo=09', datos)
        self.assertContains(r, 'SOAT vencido')
        self.assertContains(r, 'La licencia de')
        # renovado el SOAT y la licencia (desde la pantalla de documentos), la guía pasa el control
        self.client.post(reverse('flota:equipo', args=[v.equipo_id]), {
            'accion': 'documento', 'doc-tipo': 'SOAT', 'doc-numero': 'S-2', 'doc-emisor': 'Rímac',
            'doc-vencimiento': (HOY + timedelta(days=330)).isoformat(), 'doc-costo': '480', 'doc-bloquea': 'on'})
        self.client.post(reverse('flota:documentos'), {
            'conductor': conductor.pk, 'doc-tipo': 'LICENCIA', 'doc-numero': 'Q41234567', 'doc-categoria': 'A-IIIc',
            'doc-vencimiento': (HOY + timedelta(days=20)).isoformat(), 'doc-costo': '0', 'doc-bloquea': 'on'})
        conductor.refresh_from_db()
        self.assertEqual(conductor.licencia_categoria, 'A-IIIc')
        self.assertTrue(DocumentoFlota.objects.get(numero='S-1').renovado)
        from flota.servicios import bloqueos
        self.assertEqual(bloqueos(v, conductor), [])
        # la licencia vence en 20 días: aparece por vencer y en la campana
        self.assertContains(self.client.get(reverse('flota:documentos')), 'Q41234567')
        from core.alertas import calcular
        self.assertIn('Documentos de flota por vencer', [a['titulo'] for a in calcular(self.admin)])
        self.assertContains(self.client.get(reverse('flota:equipos') + '?tipo=VEHICULO'), 'F1L-064')
        self.assertTrue(Equipo.objects.filter(tipo='VEHICULO').count() >= 3)  # el de la demo también migra
