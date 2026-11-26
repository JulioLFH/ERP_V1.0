"""Operador logístico (Go Just In Time): solicitudes por correo y WhatsApp del remitente autorizado, guías del cliente
desde su XML, recojo por QR, tablero por destino, guía consolidada con sus relacionadas, llegada con foto y guía del
último tramo, entrega con foto y DNI, bitácora de tiempos, app del celular, permisos y costo por guía."""
import io
import json
from datetime import timedelta
from decimal import Decimal as D
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import plataforma
from core.models import Empresa, PerfilUsuario, Tercero
from core.modulos import GRUPOS, modulo_activo, modulos_del_usuario
from logistica.models import GuiaRemision
from ventas.crm_canales import whatsapp_entrante
from ventas.crm_web import procesar_correo
from ventas.models import Oportunidad

from . import servicios as srv
from .lector_qr import interpretar
from .models import (BitacoraEstado, ClienteLogistico, Consolidado, EstadoLogistico, GuiaCliente, RemitenteAutorizado,
                     SolicitudRecojo)

RUC_CLIENTE = '20512345678'


def xml_guia(serie='EG01', numero=1, ruc=RUC_CLIENTE, dest_doc='20100070970', dest_nombre='Destino Arequipa SAC',
             ubigeo='040101', direccion='Av. Ejército 100, Arequipa', peso='12.50', bultos=2):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<DespatchAdvice xmlns="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
<cbc:ID>{serie}-{numero}</cbc:ID><cbc:IssueDate>2026-11-18</cbc:IssueDate>
<cbc:DespatchAdviceTypeCode>09</cbc:DespatchAdviceTypeCode>
<cac:DespatchSupplierParty><cac:Party><cac:PartyIdentification><cbc:ID schemeID="6">{ruc}</cbc:ID>
</cac:PartyIdentification><cac:PartyLegalEntity><cbc:RegistrationName>Cliente Logístico SAC</cbc:RegistrationName>
</cac:PartyLegalEntity></cac:Party></cac:DespatchSupplierParty>
<cac:DeliveryCustomerParty><cac:Party><cac:PartyIdentification><cbc:ID schemeID="6">{dest_doc}</cbc:ID>
</cac:PartyIdentification><cac:PartyLegalEntity><cbc:RegistrationName>{dest_nombre}</cbc:RegistrationName>
</cac:PartyLegalEntity></cac:Party></cac:DeliveryCustomerParty>
<cac:Shipment><cbc:ID>SUNAT_Envio</cbc:ID><cbc:HandlingCode>01</cbc:HandlingCode>
<cbc:GrossWeightMeasure unitCode="KGM">{peso}</cbc:GrossWeightMeasure>
<cbc:TotalTransportHandlingUnitQuantity>{bultos}</cbc:TotalTransportHandlingUnitQuantity>
<cac:ShipmentStage><cac:TransitPeriod><cbc:StartDate>2026-11-18</cbc:StartDate></cac:TransitPeriod></cac:ShipmentStage>
<cac:Delivery><cac:DeliveryAddress><cbc:ID>{ubigeo}</cbc:ID><cac:AddressLine><cbc:Line>{direccion}</cbc:Line>
</cac:AddressLine></cac:DeliveryAddress><cac:Despatch><cac:DespatchAddress><cbc:ID>150101</cbc:ID><cac:AddressLine>
<cbc:Line>Av. Argentina 123, Lima</cbc:Line></cac:AddressLine></cac:DespatchAddress></cac:Despatch></cac:Delivery>
</cac:Shipment>
<cac:DespatchLine><cbc:ID>1</cbc:ID><cbc:DeliveredQuantity unitCode="NIU">2</cbc:DeliveredQuantity>
<cac:Item><cbc:Description>Cajas de repuestos</cbc:Description></cac:Item></cac:DespatchLine>
</DespatchAdvice>'''.encode('utf-8')


def foto(nombre='foto.jpg'):
    """Cabecera JPEG real: el sistema valida el contenido, no solo el nombre."""
    return SimpleUploadedFile(nombre, b'\xff\xd8\xff\xe0\x00\x10JFIF\x00' + b'\x00' * 64 + b'\xff\xd9',
                              content_type='image/jpeg')


def correo(desde, asunto='Recojo', cuerpo='Hola, tenemos carga.', adjuntos=()):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'] = desde, 'ventas@empresa.pe', asunto
    m.set_content(cuerpo)
    for nombre, datos, tipo in adjuntos:
        principal, sub = tipo.split('/')
        m.add_attachment(datos, maintype=principal, subtype=sub, filename=nombre)
    return m


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('jefe', 'j@j.com', 'x')
        Empresa.objects.update(giros=['OPERADOR_LOGISTICO'])
        cls.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc=RUC_CLIENTE,
                                             nombre='Cliente Logístico SAC', direccion='Av. Argentina 123, Lima',
                                             ubigeo='150101', dias_credito=30)
        cls.perfil = ClienteLogistico.objects.create(tercero=cls.cliente, almacen_direccion='Almacén Callao, Av. Gambetta 5',
                                                     almacen_ubigeo='070101', contacto='Rosa')
        RemitenteAutorizado.objects.create(cliente=cls.perfil, tipo='CORREO', valor='Despachos@Cliente.pe')
        RemitenteAutorizado.objects.create(cliente=cls.perfil, tipo='CELULAR', valor='987 654 321')

    def setUp(self):
        plataforma.invalidar()
        self.addCleanup(plataforma.invalidar)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        EstadoLogistico.cargar_base()

    def usuario(self, nombre, modulos=(), acciones=()):
        u = User.objects.create_user(nombre, password='x')
        for m in modulos:
            u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
        PerfilUsuario.objects.create(usuario=u, acciones=list(acciones))
        return u

    def solicitud_con_guias(self, *guias):
        sol = srv.crear_solicitud(self.cliente, 'MANUAL', mensaje='Recojo de prueba')
        for kw in guias:
            srv.registrar_guia_xml(xml_guia(**kw), self.cliente, sol)
        return sol


class ActivacionTest(Base):
    def test_solo_aparece_con_el_giro(self):
        self.assertTrue(modulo_activo('operador_logistico'))
        self.assertIn('operador_logistico', modulos_del_usuario(self.admin))
        Empresa.objects.update(giros=['COMERCIAL'])
        self.assertFalse(modulo_activo('operador_logistico'))
        self.assertNotIn('operador_logistico', modulos_del_usuario(User.objects.get(pk=self.admin.pk)))

    def test_sin_el_giro_el_correo_autorizado_sigue_al_crm(self):
        Empresa.objects.update(giros=['COMERCIAL'])
        antes = Oportunidad.objects.count()
        r = procesar_correo(correo('despachos@cliente.pe'))
        self.assertIsInstance(r, Oportunidad)
        self.assertEqual(Oportunidad.objects.count(), antes + 1)
        self.assertFalse(SolicitudRecojo.objects.exists())

    def test_ficha_del_cliente_muestra_el_servicio(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('tercero_editar', args=[self.cliente.pk]))
        self.assertContains(r, 'Servicio logístico')
        Empresa.objects.update(giros=['COMERCIAL'])
        plataforma.invalidar()
        r = self.client.get(reverse('tercero_editar', args=[self.cliente.pk]))
        self.assertNotContains(r, 'Correos, celulares y almacén de recojo')


class CanalesTest(Base):
    def test_correo_autorizado_crea_solicitud_con_guia_y_sustento(self):
        cuerpo = 'Buenos días\nBultos: 3\nContacto: Jorge 999111222\nFecha: mañana'
        m = correo('Despachos <DESPACHOS@cliente.pe>', 'Recojo urgente', cuerpo,
                   [('EG01-7.xml', xml_guia(numero=7), 'application/xml'),
                    ('guia.pdf', b'%PDF-1.4 prueba', 'application/pdf')])
        sol = procesar_correo(m)
        self.assertIsInstance(sol, SolicitudRecojo)
        self.assertEqual((sol.canal, sol.estado, sol.bultos), ('CORREO', 'ASIGNADO', 3))
        self.assertEqual(sol.contacto, 'Jorge 999111222')
        self.assertEqual(sol.fecha_recojo, timezone.localdate() + timedelta(days=1))
        self.assertEqual(sol.almacen, 'Almacén Callao, Av. Gambetta 5')  # de la ficha del cliente
        g = sol.guias.get()
        self.assertEqual((g.serie, g.numero, g.destino, g.peso), ('EG01', '7', 'AREQUIPA', D('12.50')))
        self.assertTrue(g.datos_completos)
        self.assertIn('guia.pdf guardado como sustento', sol.avisos)
        from core.sustentos import de as sustentos_de
        self.assertEqual(len(sustentos_de(sol)), 1)

    def test_zip_con_xml(self):
        import zipfile
        z = io.BytesIO()
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('EG01-8.xml', xml_guia(numero=8))
            f.writestr('EG01-9.xml', xml_guia(numero=9))
        sol = procesar_correo(correo('despachos@cliente.pe', adjuntos=[('guias.zip', z.getvalue(), 'application/zip')]))
        self.assertEqual(sorted(sol.guias.values_list('numero', flat=True)), ['8', '9'])

    def test_sin_texto_basta_y_segundo_mensaje_se_une(self):
        sol = procesar_correo(correo('despachos@cliente.pe', asunto='', cuerpo=''))
        self.assertIsInstance(sol, SolicitudRecojo)
        self.assertIsNone(sol.bultos)
        otra = procesar_correo(correo('despachos@cliente.pe', cuerpo='Bultos: 4'))
        self.assertEqual(otra.pk, sol.pk)
        self.assertEqual(SolicitudRecojo.objects.get(pk=sol.pk).bultos, 4)

    def test_correo_no_autorizado_va_al_crm(self):
        r = procesar_correo(correo('otro@cliente.pe'))
        self.assertIsInstance(r, Oportunidad)
        self.assertFalse(SolicitudRecojo.objects.exists())

    def test_cliente_sin_credito_ni_casilla_va_al_crm(self):
        Tercero.objects.filter(pk=self.cliente.pk).update(dias_credito=0, limite_credito=0)
        self.assertIsInstance(procesar_correo(correo('despachos@cliente.pe')), Oportunidad)
        ClienteLogistico.objects.filter(pk=self.perfil.pk).update(servicio_logistico=True)
        self.assertIsInstance(procesar_correo(correo('despachos@cliente.pe')), SolicitudRecojo)

    def test_whatsapp_por_los_ultimos_9_digitos(self):
        sol = whatsapp_entrante({'from': '51987654321', 'type': 'image', 'image': {'caption': 'Bultos: 2'}})
        self.assertIsInstance(sol, SolicitudRecojo)
        self.assertEqual((sol.canal, sol.remitente, sol.bultos), ('WHATSAPP', '+51987654321', 2))
        self.assertIn('Envió una imagen', sol.avisos)
        from ventas.models import ConversacionCanal
        whatsapp_entrante({'from': '51900000000', 'type': 'text', 'text': {'body': 'Hola, precios?'}})
        self.assertTrue(ConversacionCanal.objects.filter(telefono__endswith='900000000').exists())
        self.assertEqual(SolicitudRecojo.objects.count(), 1)

    def test_lineas_opcionales(self):
        d = srv.leer_campos('*Almacén:* Planta Ate\nbultos = 12 cajas\nFecha: 05/12/2026\nOtra cosa: x')
        self.assertEqual(d['almacen'], 'Planta Ate')
        self.assertEqual(d['bultos'], 12)
        self.assertEqual(str(d['fecha']), '2026-12-05')
        self.assertNotIn('contacto', d)

    def test_autorizados_unicos_entre_clientes(self):
        from .forms import ClienteLogisticoForm
        otro = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20999999991', nombre='Otro')
        f = ClienteLogisticoForm({'correos': 'despachos@cliente.pe', 'celulares': '+51 987654321'},
                                 instance=ClienteLogistico(tercero=otro))
        self.assertFalse(f.is_valid())
        self.assertIn('correos', f.errors)
        self.assertIn('celulares', f.errors)


class QrTest(TestCase):
    def test_formatos(self):
        a = interpretar(f'{RUC_CLIENTE}|09|EG01|00000123|2026-11-18')
        self.assertEqual((a.clase, a.ruc, a.serie, a.numero), ('GUIA', RUC_CLIENTE, 'EG01', '123'))
        self.assertEqual(interpretar('CEIBA-OL|CONSOLIDADO|CL-000001|abc').clase, 'CONSOLIDADO')
        e = interpretar('https://e-factura.sunat.gob.pe/x?serie=T001&numero=45&ruc=20100070970')
        self.assertEqual((e.clase, e.serie, e.numero), ('ENLACE', 'T001', '45'))
        self.assertFalse(interpretar('https://e-factura.sunat.gob.pe/descarga/abc').identifica_guia)
        self.assertEqual(interpretar('hola').clase, 'DESCONOCIDO')
        self.assertEqual(interpretar('').clase, 'VACIO')


class FlujoTest(Base):
    def test_flujo_completo(self):
        sol = self.solicitud_con_guias({'numero': 1}, {'numero': 2, 'dest_doc': '20100070971', 'dest_nombre': 'Otro AQP'},
                                       {'numero': 3, 'ubigeo': '130101', 'direccion': 'Jr. Pizarro 1, Trujillo'})
        self.assertEqual(sol.guias.filter(estado__codigo='ATENDIDO').count(), 3)
        # recojo: escanea una guía y se confirma toda la solicitud (no hay recojo parcial)
        operador = self.usuario('op', ['operador_logistico'], ['operador_logistico.operar'])
        g1 = srv.registrar_recojo(operador, f'{RUC_CLIENTE}|09|EG01|1', latitud='-12.05', longitud='-77.04')
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'ATENDIDO')
        self.assertEqual(GuiaCliente.objects.filter(estado__codigo='CONFIRMADO').count(), 3)
        self.assertEqual(g1.recojo_latitud, D('-12.050000'))
        with self.assertRaises(srv.ErrorOperador) as e:
            srv.registrar_recojo(operador, f'{RUC_CLIENTE}|09|EG01|1')
        self.assertTrue(e.exception.repetida)
        # tablero: dos grupos por destino, de cualquier cliente
        grupos, sin_destino = srv.tablero()
        destinos = {g.destino: len(gs) for g, gs in grupos}
        self.assertEqual(destinos, {'AREQUIPA': 2, 'TRUJILLO (LA LIBERTAD)': 1})
        aqp = Consolidado.objects.get(departamento='AREQUIPA')
        tru = Consolidado.objects.get(departamento='LA LIBERTAD')
        g3 = GuiaCliente.objects.get(numero='3')
        srv.mover_guia(g3, aqp, self.admin)  # logística decide moverla
        self.assertEqual(GuiaCliente.objects.get(numero='3').consolidado, aqp)
        srv.mover_guia(GuiaCliente.objects.get(numero='3'), tru, self.admin)
        # ingreso al almacén por escaneo
        srv.registrar_ingreso(operador, f'{RUC_CLIENTE}|09|EG01|2')
        # consolidada con sus guías como relacionadas
        with self.assertRaises(srv.ErrorOperador):
            srv.emitir_consolidada(aqp, self.admin, llegada_ubigeo='040101', llegada_direccion='Agencia AQP')
        srv.confirmar_grupo(aqp, self.admin)
        with self.assertRaises(srv.ErrorOperador):
            srv.emitir_consolidada(aqp, self.admin)  # falta la llegada
        gr = srv.emitir_consolidada(aqp, self.admin, llegada_ubigeo='040101',
                                    llegada_direccion='Agencia Arequipa, Calle Mercaderes 10')
        self.assertEqual(sorted(gr.relacionados.values_list('numero', flat=True)), ['EG01-1', 'EG01-2'])
        self.assertTrue(all(r.emisor_ruc == RUC_CLIENTE and r.tipo == '09' for r in gr.relacionados.all()))
        self.assertEqual((gr.peso_bruto, gr.numero_bultos), (D('25.00'), 4))
        self.assertEqual(GuiaCliente.objects.filter(consolidado=aqp, estado__codigo='EN_TRAYECTO').count(), 2)
        # la GRE directa a SUNAT lleva las relacionadas (no se envía nada)
        from core.sunat_gre import documentos_relacionados
        self.assertTrue({'EG01-1', 'EG01-2'} <= {d[1] for d in documentos_relacionados(gr)})
        # documento del transportista
        transportista = Tercero.objects.filter(tipo='PROVEEDOR', tipo_doc='6').first()
        srv.registrar_transportista(aqp, self.admin, transportista, '31', 'V001-555')
        # llegada: sin foto no; con foto, Por entregar y guía del último tramo con 3 relacionadas
        aqp.refresh_from_db()
        with self.assertRaises(srv.ErrorOperador):
            srv.registrar_llegada(operador, aqp.qr_texto)
        srv.registrar_llegada(operador, aqp.qr_texto, foto=foto())
        aqp.refresh_from_db()
        self.assertEqual(aqp.estado, 'EN_DESTINO')
        ultimos = GuiaRemision.objects.filter(guias_cliente__consolidado=aqp).distinct()
        self.assertEqual(ultimos.count(), 2)  # uno por destinatario
        u = GuiaCliente.objects.get(numero='1').ultimo_tramo
        rel = sorted(u.relacionados.values_list('tipo', 'numero'))
        self.assertEqual(rel, [('09', 'EG01-1'), ('09', f'{gr.serie}-{int(gr.numero)}'), ('31', 'V001-555')])
        self.assertEqual(GuiaCliente.objects.get(numero='1').estado.codigo, 'EN_REPARTO')
        # entrega: foto y DNI obligatorios
        with self.assertRaises(srv.ErrorOperador):
            srv.registrar_entrega(operador, f'{RUC_CLIENTE}|09|EG01|1', foto=foto(), dni='')
        srv.registrar_entrega(operador, f'{RUC_CLIENTE}|09|EG01|1', foto=foto(), dni='45678912', nombre='Ana')
        g = GuiaCliente.objects.get(numero='1')
        self.assertEqual((g.estado.codigo, g.receptor_dni), ('ENTREGADO', '45678912'))
        self.assertEqual(Consolidado.objects.get(pk=aqp.pk).estado, 'EN_DESTINO')
        srv.registrar_entrega(operador, '', GuiaCliente.objects.get(numero='2').pk, foto=foto(), dni='45678913')
        self.assertEqual(Consolidado.objects.get(pk=aqp.pk).estado, 'CERRADO')
        # bitácora: quién, cuándo, cómo y cuánto estuvo en el estado anterior
        b = BitacoraEstado.objects.filter(guia=g).order_by('fecha', 'id')
        self.assertEqual([x.estado.codigo for x in b],
                         ['ATENDIDO', 'CONFIRMADO', 'EN_CUSTODIA', 'EN_TRAYECTO', 'POR_ENTREGAR', 'EN_REPARTO',
                          'ENTREGADO'])
        self.assertEqual(b.filter(estado__codigo='CONFIRMADO').get().origen, 'ESCANEO')
        self.assertIsNotNone(b.last().segundos_anterior)

    def test_guia_escaneada_sin_xml_queda_pendiente(self):
        sol = srv.crear_solicitud(self.cliente, 'MANUAL')
        g = srv.registrar_recojo(self.admin, f'{RUC_CLIENTE}|09|EG02|50')
        self.assertFalse(g.datos_completos)
        self.assertEqual((g.solicitud, g.estado.codigo, g.consolidado), (sol, 'CONFIRMADO', None))
        # el XML que llega después la completa y entra a su grupo
        g2, creada = srv.registrar_guia_xml(xml_guia(serie='EG02', numero=50), self.cliente)
        self.assertFalse(creada)
        self.assertEqual(g2.pk, g.pk)
        self.assertTrue(g2.datos_completos)
        self.assertEqual(GuiaCliente.objects.get(pk=g.pk).consolidado.departamento, 'AREQUIPA')

    def test_estado_inicial_unico(self):
        self.assertEqual(EstadoLogistico.objects.filter(inicial=True).count(), 1)
        self.assertEqual(EstadoLogistico.el_inicial().codigo, 'ATENDIDO')

    def test_costo_por_guia_por_peso(self):
        self.solicitud_con_guias({'numero': 1, 'peso': '30'}, {'numero': 2, 'peso': '10', 'dest_doc': '20100070971'})
        srv.registrar_recojo(self.admin, f'{RUC_CLIENTE}|09|EG01|1')
        grupo = Consolidado.objects.get()
        srv.confirmar_grupo(grupo)
        srv.emitir_consolidada(grupo, llegada_ubigeo='040101', llegada_direccion='Agencia')
        lineas = [SimpleNamespace(total=D('100'), moneda='PEN', tipo_cambio=D('1'))]
        with patch('finanzas.models_rendicion.LineaGasto.objects') as m:
            m.filter.return_value.exclude.return_value = lineas
            total, filas = srv.costos(grupo)
        self.assertEqual(total, D('100'))
        self.assertEqual(sorted(c for _, c in filas), [D('25.00'), D('75.00')])


class PantallasTest(Base):
    def test_pantallas_de_escritorio(self):
        sol = self.solicitud_con_guias({'numero': 1})
        srv.registrar_recojo(self.admin, f'{RUC_CLIENTE}|09|EG01|1')
        grupo = Consolidado.objects.get()
        srv.crear_solicitud(self.cliente, 'WHATSAPP', '+51987654321', unir=False)  # sin operador asignado
        self.client.force_login(self.admin)
        for nombre, args in (('tablero', []), ('solicitudes', []), ('solicitud_nueva', []), ('solicitud', [sol.pk]),
                             ('guias', []), ('guia', [GuiaCliente.objects.get().pk]), ('planificacion', []),
                             ('consolidados', []), ('consolidado', [grupo.pk]), ('bitacora', []), ('costos', []),
                             ('clientes', []), ('cliente', [self.cliente.pk]), ('estados', []), ('configuracion', []),
                             ('movil', [])):
            r = self.client.get(reverse(f'operador_logistico:{nombre}', args=args))
            self.assertEqual(r.status_code, 200, nombre)
        srv.confirmar_grupo(grupo)
        r = self.client.post(reverse('operador_logistico:consolidado', args=[grupo.pk]),
                             {'accion': 'emitir', 'llegada_ubigeo': '040101', 'llegada_direccion': 'Agencia AQP',
                              'fecha': timezone.localdate().isoformat()})
        self.assertEqual(r.status_code, 302)
        grupo.refresh_from_db()
        self.assertEqual(grupo.estado, 'EN_TRAYECTO')
        r = self.client.get(reverse('operador_logistico:hoja_despacho', args=[grupo.pk]))
        self.assertContains(r, grupo.qr_texto)

    def test_cliente_guarda_autorizados(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('operador_logistico:cliente', args=[self.cliente.pk]), {
            'servicio_logistico': 'on', 'correos': 'despachos@cliente.pe\nlogistica@cliente.pe',
            'celulares': '+51 912 345 678', 'almacen_direccion': 'Callao', 'almacen_ubigeo': '070101',
            'almacen_referencia': '', 'contacto': 'Rosa'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sorted(self.perfil.autorizados.values_list('clave', flat=True)),
                         ['912345678', 'despachos@cliente.pe', 'logistica@cliente.pe'])

    def test_planificar_y_configurar_exigen_permiso(self):
        u = self.usuario('mirada', ['operador_logistico'], [])
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('operador_logistico:tablero')).status_code, 200)
        self.assertEqual(self.client.get(reverse('operador_logistico:solicitud_nueva')).status_code, 403)
        self.assertEqual(self.client.post(reverse('operador_logistico:configuracion'), {}).status_code, 403)
        otro = self.usuario('ventas1', ['ventas'], ['ventas.emitir'])
        self.client.force_login(otro)
        self.assertNotEqual(self.client.get(reverse('operador_logistico:tablero')).status_code, 200)


class CelularTest(Base):
    def test_verificar_y_registrar(self):
        self.solicitud_con_guias({'numero': 1})
        op = self.usuario('op', ['operador_logistico'], ['operador_logistico.operar'])
        self.client.force_login(op)
        r = self.client.get(reverse('operador_logistico:movil_pendientes'), {'paso': 'RECOJO'})
        self.assertEqual(len(r.json()['items']), 1)
        r = self.client.post(reverse('operador_logistico:movil_verificar'),
                             {'paso': 'RECOJO', 'qr': f'{RUC_CLIENTE}|09|EG01|1'})
        self.assertTrue(r.json()['ok'])
        datos = {'paso': 'RECOJO', 'qr': f'{RUC_CLIENTE}|09|EG01|1', 'latitud': '-12.1', 'longitud': '-77.0',
                 'id_local': 'a1', 'cuando': (timezone.now() - timedelta(minutes=5)).isoformat()}
        r = self.client.post(reverse('operador_logistico:movil_registrar'), datos)
        self.assertEqual(r.json(), {'ok': True, 'mensaje': 'Recogida: guía EG01-1', 'id_local': 'a1'})
        g = GuiaCliente.objects.get()
        self.assertLess(g.recogida_en, timezone.now() - timedelta(minutes=4))  # la hora real del escaneo sin señal
        r = self.client.post(reverse('operador_logistico:movil_registrar'), datos)  # reintento de la cola
        self.assertTrue(r.json()['ok'] and r.json()['repetida'])
        r = self.client.post(reverse('operador_logistico:movil_verificar'), {'paso': 'RECOJO', 'qr': 'basura'})
        self.assertFalse(r.json()['ok'])

    def test_registrar_exige_permiso_operar(self):
        u = self.usuario('mirada', ['operador_logistico'], [])
        self.client.force_login(u)
        r = self.client.post(reverse('operador_logistico:movil_registrar'), {'paso': 'RECOJO', 'qr': 'x'})
        self.assertEqual(r.status_code, 403)
