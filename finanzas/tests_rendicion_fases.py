"""Rendición de gastos, fases 1 (parte móvil), 2 y 3 (v1.28): QR SUNAT, consulta RUC, validación SUNAT, lectura con
IA, app del celular sin internet, kilometraje, proyectos y sitios, tarjetas corporativas, WhatsApp, correo, cruce
SIRE, API y almacenamiento de objetos. Un escenario por clase."""
import hashlib
import hmac
import io
import json
from datetime import date
from decimal import Decimal
from email.message import EmailMessage
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import Sum
from django.urls import reverse

from compras.models import Compra
from compras.tests_xml_sire import xml_factura
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea, CuentaContable
from core.models import ApiToken, Empresa, PerfilUsuario, Tercero

from . import comprobantes as cp
from . import rendiciones as rd
from .models import (CategoriaGasto, ConfigRendicion, EntregaRendir, GastoRendicion, LineaGasto, MovimientoTarjeta,
                     Proyecto, Sitio, TarjetaCorporativa)
from .tests_rendicion import PNG, Base
from django.utils import timezone

D = Decimal
HOY = timezone.localdate()
RUC_PROV = '20100047218'


class QrRucTest(Base):
    def test_qr_y_consulta_ruc(self):
        ruc_empresa = Empresa.actual().ruc
        d = cp.leer_qr(f'{RUC_PROV}|01|F001|00000123|18.00|118.00|{HOY:%Y-%m-%d}|6|{ruc_empresa}|hash')
        self.assertEqual((d['tipo_comprobante'], d['serie'], d['numero'], d['base'], d['a_nombre_empresa']),
                         ('01', 'F001', '123', D('100.00'), True))
        otro = cp.leer_qr(f'{RUC_PROV}|01|F001|124|18.00|118.00|{HOY:%d/%m/%Y}|1|45678912|')
        self.assertFalse(otro['a_nombre_empresa'])
        self.assertIn('no da crédito fiscal', otro['aviso'])
        with self.assertRaises(cp.ErrorLectura):
            cp.leer_qr('texto cualquiera')
        # consulta RUC: sin token usa clientes y proveedores; con token, la API (estado y condición)
        t, _ = Tercero.objects.get_or_create(numero_doc=RUC_PROV, defaults={'tipo': 'PROVEEDOR', 'tipo_doc': '6',
                                                                            'nombre': 'HOTEL SUR SAC'})
        self.client.force_login(self.rendidor)
        r = self.client.get(reverse('rendiciones:ruc', args=[RUC_PROV])).json()
        self.assertEqual(r['nombre'], t.nombre)
        Empresa.objects.update(token_tipo_cambio='tok')
        respuesta = io.BytesIO(json.dumps({'razon_social': 'HOTEL SUR S.A.C.', 'estado': 'ACTIVO',
                                           'condicion': 'NO HABIDO'}).encode())
        from django.core.cache import cache
        cache.delete(f'ruc-{RUC_PROV}')
        with patch('finanzas.comprobantes.urllib.request.urlopen', return_value=respuesta):
            r = cp.consultar_ruc(RUC_PROV)
        self.assertEqual((r['nombre'], r['alerta']), ('HOTEL SUR S.A.C.', 'RUC NO HABIDO'))
        r = self.client.post(reverse('rendiciones:qr'), {'texto': f'{RUC_PROV}|03|B001|9|0|50.00|{HOY:%Y-%m-%d}'})
        self.assertEqual(r.json()['tipo_comprobante'], '03')


class ValidacionSunatTest(Base):
    def test_comprobante_anulado_no_se_envia(self):
        r = self.rendicion()
        linea = self.factura(r, numero='777')
        res = {'codigo': '2', 'estado': 'ANULADO', 'valido': False,
               'detalle': 'Comprobante ANULADO · RUC ACTIVO · HABIDO'}
        with patch('core.sunat_consulta.configurada', return_value=True), \
                patch('core.sunat_consulta.validar', return_value=res):
            with self.assertRaisesMessage(rd.ErrorRendicion, 'SUNAT: Comprobante ANULADO'):
                rd.enviar(r, self.rendidor)
        linea.refresh_from_db()
        self.assertEqual((linea.sunat_valido, linea.ruc_estado), (False, 'RUC ACTIVO · HABIDO'))
        self.assertIsNotNone(linea.validado_en)


class LecturaIATest(Base):
    def test_ticket_leido_con_ia(self):
        ConfigRendicion.objects.update_or_create(pk=ConfigRendicion.actual().pk,
                                                 defaults={'ia_activa': True, 'ia_api_key': 'sk-prueba'})
        leido = {'es_comprobante': True, 'ruc_emisor': RUC_PROV, 'razon_social': 'GRIFO NORTE', 'tipo': '12',
                 'serie': 'T001', 'numero': '000456', 'fecha': HOY.isoformat(), 'moneda': 'PEN', 'base': 0, 'igv': 0,
                 'total': 85.5, 'ruc_cliente': '', 'qr': ''}
        self.client.force_login(self.rendidor)
        with patch('finanzas.comprobantes._llamar_claude', return_value=leido) as llamada:
            r = self.client.post(reverse('rendiciones:leer_ia'),
                                 {'foto': SimpleUploadedFile('t.png', PNG, content_type='image/png')}).json()
        self.assertEqual((r['numero'], r['total'], r['base'], r['tipo_comprobante']), ('456', '85.50', '85.50', '12'))
        self.assertEqual(llamada.call_args.args[1], 'image/png')
        with patch('finanzas.comprobantes._llamar_claude', return_value={**leido, 'es_comprobante': False}):
            r = self.client.post(reverse('rendiciones:leer_ia'),
                                 {'foto': SimpleUploadedFile('t.png', PNG, content_type='image/png')})
        self.assertEqual(r.status_code, 400)


class MovilTest(Base):
    def test_app_del_celular_sin_duplicar(self):
        self.client.force_login(self.rendidor)
        r = self.rendicion()
        pagina = self.client.get(reverse('rendiciones:movil'))
        self.assertContains(pagina, 'datos-app')
        self.assertContains(pagina, r.numero)
        datos = {'rendicion': r.pk, 'cliente_id': 'abc123', 'fecha': HOY.isoformat(), 'tipo_comprobante': '12',
                 'ruc': RUC_PROV, 'serie': 'T1', 'numero': '55', 'moneda': 'PEN', 'total': '42.50',
                 'categoria': self.cat.pk, 'centro_costo': self.cc.pk, 'glosa': 'Almuerzo de trabajo',
                 'a_nombre_empresa': '', 'indicador': rd.sugerir_indicador('12').pk}
        for _ in range(2):  # la app reintenta: no se duplica
            resp = self.client.post(reverse('rendiciones:movil_gasto'),
                                    {**datos, 'fotos': SimpleUploadedFile('c.jpg', PNG, content_type='image/jpeg')})
            self.assertTrue(resp.json()['ok'], resp.content)
        self.assertEqual(r.lineas.count(), 1)
        linea = r.lineas.get()
        self.assertEqual((linea.origen, linea.total), ('MOVIL', D('42.50')))
        from core.sustentos import de
        self.assertEqual(de(linea).count(), 1)
        self.assertEqual(self.client.get(reverse('rendiciones:sw')).headers['Service-Worker-Allowed'], '/rendiciones/')
        self.assertEqual(self.client.get(reverse('rendiciones:manifest')).json()['display'], 'standalone')
        nueva = self.client.post(reverse('rendiciones:movil_nueva'), {'entrega': r.entrega_id, 'descripcion': 'Lima'})
        self.assertIn('Lima', nueva.json()['texto'])


class KilometrajeProyectoTest(Base):
    def test_km_y_proyecto_con_su_centro(self):
        from .forms_rendicion import LineaGastoForm
        km = CategoriaGasto.objects.create(codigo='KM', nombre='Kilometraje', cuenta_contable=self.cat.cuenta_contable,
                                           exige_comprobante=False, tarifa_km=D('0.80'), exige_proyecto=True)
        proyecto = Proyecto.objects.create(codigo='OB-01', nombre='Obra Arequipa', centro_costo=self.cc)
        sitio = Sitio.objects.create(proyecto=proyecto, codigo='S1', nombre='Torre 1')
        r = self.rendicion()
        ng = rd.sugerir_indicador('KM').pk
        form = LineaGastoForm({'fecha': HOY.isoformat(), 'tipo_comprobante': 'KM', 'moneda': 'PEN', 'kilometros': '125',
                               'categoria': km.pk, 'sitio': sitio.pk, 'glosa': 'Visita a obra', 'indicador': ng})
        self.assertTrue(form.is_valid(), form.errors)
        linea = rd.guardar_linea(r, form.save(commit=False), self.rendidor)
        self.assertEqual((linea.total, linea.proyecto_id), (D('100.00'), proyecto.pk))
        sin = LineaGastoForm({'fecha': HOY.isoformat(), 'tipo_comprobante': 'KM', 'moneda': 'PEN', 'kilometros': '0',
                              'categoria': self.cat.pk, 'glosa': 'x', 'indicador': ng})
        self.assertFalse(sin.is_valid())
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.gerente)
        rd.contabilizar(r, self.contador)
        g = GastoRendicion.objects.get(entrega=r.entrega)
        self.assertEqual(g.centro_costo, self.cc)
        self.assertIn('125', g.descripcion)
        self.assertEqual(rd.tablero(HOY, HOY)['por_proyecto'][0][0], str(proyecto))


class TarjetaTest(Base):
    def test_estado_de_cuenta_conciliacion_y_pago(self):
        pasivo = CuentaContable.objects.filter(codigo__startswith='46', imputable=True).order_by('codigo').first()
        t = rd.crear_tarjeta(TarjetaCorporativa(nombre='Visa BCP', banco='BCP', ultimos4='4321', titular=self.trabajador,
                                                cuenta_contable=pasivo, categoria_defecto=self.cat,
                                                centro_costo=self.cc), self.admin)
        self.assertEqual((t.entrega.tipo, t.entrega.cuenta_contable), ('TARJETA', pasivo))
        csv = (f'Fecha;Descripción;Referencia;Monto\n{HOY:%d/%m/%Y};HOTEL SUR;R1;236.00\n'
               f'{HOY:%d/%m/%Y};GRIFO NORTE;R2;100.00\n{HOY:%d/%m/%Y};PAGO RECIBIDO;R3;-500.00\n')
        r, n = rd.importar_estado_cuenta(t, SimpleUploadedFile('ec.csv', csv.encode()), self.admin)
        self.assertEqual((n, r.rendidor, r.lineas.count()), (2, self.rendidor, 2))
        _, n2 = rd.importar_estado_cuenta(t, SimpleUploadedFile('ec.csv', csv.encode()), self.admin)
        self.assertEqual(n2, 0)  # ya importados
        self.assertEqual(len(rd.sin_comprobante()), 2)
        # el titular sustenta el hotel con su factura; el grifo queda sin comprobante (alerta de política)
        hotel = LineaGasto.objects.get(rendicion=r, total=D('236.00'))
        hotel.tipo_comprobante, hotel.ruc, hotel.razon_social = '01', RUC_PROV, 'HOTEL SUR SAC'
        hotel.serie, hotel.numero, hotel.base, hotel.igv = 'F001', '901', D('200'), D('36')
        hotel.indicador = rd.sugerir_indicador('01')
        rd.guardar_linea(r, hotel, self.rendidor)
        self.assertEqual(len(rd.sin_comprobante()), 1)
        LineaGasto.objects.filter(rendicion=r, tipo_comprobante='SC').update(
            justificacion='Grifo sin factura', indicador=rd.sugerir_indicador('SC'))
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.gerente)
        rd.contabilizar(r, self.contador)
        e = EntregaRendir.objects.get(pk=t.entrega_id)
        self.assertEqual(e.saldo, D('-336.00'))  # deuda con el banco
        rd.pagar_tarjeta(t, self.banco, D('336.00'), HOY, self.contador)
        self.assertEqual(EntregaRendir.objects.get(pk=e.pk).saldo, D('0'))
        self.assertTrue(Compra.objects.filter(serie='F001', numero='901').exists())
        res = centralizar_periodo(HOY.strftime('%Y%m'))
        self.assertEqual(res['errores'], [])
        saldo = AsientoLinea.objects.filter(cuenta=pasivo).aggregate(d=Sum('debe'), h=Sum('haber'))
        self.assertEqual(saldo['d'] - saldo['h'], D('0'))
        self.client.force_login(self.admin)
        for nombre, args in (('tarjetas', []), ('tarjeta', [t.pk]), ('sin_comprobante', [])):
            self.assertEqual(self.client.get(reverse(f'rendiciones:{nombre}', args=args)).status_code, 200)


def firmar(secreto, cuerpo):
    return 'sha256=' + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


class WhatsAppTest(Base):
    def test_foto_por_whatsapp_crea_borrador(self):
        cfg = ConfigRendicion.actual()
        ConfigRendicion.objects.filter(pk=cfg.pk).update(
            whatsapp_activo=True, whatsapp_token='tok', whatsapp_phone_id='123', whatsapp_verify_token='verif',
            whatsapp_app_secret='secreto', categoria_defecto=self.cat)
        PerfilUsuario.objects.filter(usuario=self.rendidor).update(celular='+51 987 654 321')
        self.rendicion()
        url = reverse('rendiciones:whatsapp')
        r = self.client.get(url, {'hub.mode': 'subscribe', 'hub.verify_token': 'verif', 'hub.challenge': '42'})
        self.assertEqual(r.content, b'42')
        cuerpo = json.dumps({'entry': [{'changes': [{'value': {'messages': [{
            'from': '51987654321', 'type': 'image', 'image': {'id': 'm1', 'caption': 'Taxi aeropuerto'}}]}}]}]}).encode()
        self.assertEqual(self.client.post(url, cuerpo, content_type='application/json',
                                          HTTP_X_HUB_SIGNATURE_256='sha256=mala').status_code, 403)
        with patch('finanzas.views_rendicion_movil._descargar_media', return_value=(PNG, 'image/png')), \
                patch('finanzas.views_rendicion_movil._responder') as respuesta:
            r = self.client.post(url, cuerpo, content_type='application/json',
                                 HTTP_X_HUB_SIGNATURE_256=firmar('secreto', cuerpo))
        self.assertEqual(r.status_code, 200)
        linea = LineaGasto.objects.get(origen='WHATSAPP')
        self.assertEqual((linea.glosa, linea.rendicion.rendidor), ('Taxi aeropuerto', self.rendidor))
        self.assertIn('borrador', respuesta.call_args.args[2])


class CorreoTest(Base):
    def test_xml_reenviado_por_correo(self):
        from .management.commands.leer_correo_rendiciones import procesar
        self.rendicion()
        ruc_empresa = Empresa.actual().ruc
        xml = xml_factura(RUC_PROV, ruc_empresa, [('Hospedaje', 'H1', '1', '200.00', '10')], numero='F002-00000321')
        msg = EmailMessage()
        msg['From'], msg['Subject'] = f'Rendidor <{self.rendidor.email}>', 'Factura hotel'
        msg.set_content('Adjunto factura')
        msg.add_attachment(xml.read(), maintype='text', subtype='xml', filename='F002-321.xml')
        user, lineas = procesar(msg)
        self.assertEqual(user, self.rendidor)
        l = lineas[0]
        self.assertEqual((l.tipo_comprobante, l.serie, l.numero, l.base, l.igv, l.total, l.origen),
                         ('01', 'F002', '321', D('200.00'), D('36.00'), D('236.00'), 'CORREO'))
        extrano = EmailMessage()
        extrano['From'] = 'nadie@otro.com'
        self.assertEqual(procesar(extrano), (None, []))


class SireTest(Base):
    def test_cruce_marca_rendidas_y_no_rendidas(self):
        from compras import sire
        r = self.rendicion()
        self.factura(r, numero='601')
        rd.enviar(r, self.rendidor)
        periodo = HOY.strftime('%Y%m')
        registros = [{'ruc': RUC_PROV, 'nombre': 'HOTEL', 'tipo': '01', 'serie': 'F001', 'numero': '601',
                      'fecha': HOY, 'base': D('500'), 'igv': D('90'), 'total': D('590')},
                     {'ruc': '20100070970', 'nombre': 'OTRO', 'tipo': '01', 'serie': 'F009', 'numero': '1',
                      'fecha': HOY, 'base': D('10'), 'igv': D('1.8'), 'total': D('11.8')}]
        res = sire.comparar(registros, periodo)
        rendida = next(x for x in res['solo_sunat'] if x['numero'] == '601')
        self.assertEqual(rendida['rendicion'].rendicion, r)
        self.assertEqual([x['numero'] for x in res['nadie_rindio']], ['1'])
        rd.aprobar(r, self.gerente)
        rd.contabilizar(r, self.contador)
        res = sire.comparar(registros, periodo)
        self.assertEqual(res['por_rendicion'], 1)


class ApiTest(Base):
    def test_api_rendiciones(self):
        r = self.rendicion()
        _, clave = ApiToken.crear(self.rendidor, 'App', solo_lectura=False)
        cab = {'HTTP_AUTHORIZATION': f'Bearer {clave}'}
        resp = self.client.post(f'/api/v1/rendiciones/{r.pk}/gastos/', json.dumps({
            'fecha': HOY.isoformat(), 'tipo_comprobante': '12', 'ruc': RUC_PROV, 'serie': 'T1', 'numero': '8',
            'total': '25.00', 'categoria': 'VIA', 'centro_costo': self.cc.codigo, 'glosa': 'Taxi',
            'indicador_impuesto': 'S1',
            'foto_base64': __import__('base64').b64encode(PNG).decode(), 'foto_nombre': 'taxi.png',
            'foto_tipo': 'image/png'}), content_type='application/json', **cab)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['origen'], 'API')
        lista = self.client.get('/api/v1/rendiciones/', **cab).json()
        self.assertEqual(lista['results'][0]['numero'], r.numero)
        detalle = self.client.get(f'/api/v1/rendiciones/{r.pk}/', **cab).json()
        self.assertEqual(detalle['gastos'][0]['glosa'], 'Taxi')
        self.assertEqual(self.client.get('/api/v1/rendiciones/categorias/', **cab).status_code, 200)
        malo = self.client.post(f'/api/v1/rendiciones/{r.pk}/gastos/', json.dumps({'categoria': 'NO'}),
                                content_type='application/json', **cab)
        self.assertEqual(malo.status_code, 422)


class ObjetosTest(Base):
    def test_fotos_en_almacenamiento_de_objetos(self):
        from core import almacen_objetos
        from core.models import ArchivoSustento
        guardados = {}

        class Falso:
            def put_object(self, Bucket, Key, Body, ContentType):
                guardados[Key] = Body

            def get_object(self, Bucket, Key):
                return {'Body': io.BytesIO(guardados[Key])}

        # un archivo previo en la base de datos se traslada con el comando
        from core.sustentos import guardar_archivo
        previo = guardar_archivo(None, datos=b'antes', nombre='antes.pdf', tipo='application/pdf')
        entorno = {'OBJETOS_BUCKET': 'b', 'OBJETOS_ACCESS_KEY': 'k', 'OBJETOS_SECRET_KEY': 's'}
        with patch.dict('os.environ', entorno), patch.object(almacen_objetos, 'cliente', return_value=Falso()):
            nuevo = guardar_archivo(None, datos=PNG, nombre='foto.png', tipo='image/png')
            self.assertEqual((bytes(nuevo.datos), nuevo.contenido), (b'', PNG))
            self.assertTrue(nuevo.ubicacion.startswith('default/') or '/' in nuevo.ubicacion)
            call_command('mover_sustentos', stdout=io.StringIO())
            previo = ArchivoSustento.objects.get(pk=previo.pk)
            self.assertEqual((bytes(previo.datos), previo.contenido), (b'', b'antes'))


class PantallasTest(Base):
    def test_planilla_movilidad_e_integraciones(self):
        from planillas.models import Parametro
        Parametro.objects.update_or_create(anio=HOY.year, defaults={'rmv': D('1130'), 'uit': D('5350')})
        r = self.rendicion()
        self.linea(r, '20', tipo='PM', categoria=self.movilidad, numero='', destino='Cliente San Isidro')
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('rendiciones:movilidad', args=[r.pk]))
        self.assertContains(resp, 'PLANILLA DE GASTOS DE MOVILIDAD')
        self.assertContains(resp, 'Cliente San Isidro')
        self.assertContains(resp, '45.20')  # 4% de la RMV
        url = reverse('rendiciones:integraciones')
        self.client.post(url, {'ia_activa': 'on', 'ia_api_key': 'sk-1', 'ia_modelo': 'claude-opus-5-5',
                               'correo_puerto': '993'})
        self.client.post(url, {'ia_activa': 'on', 'ia_api_key': '', 'ia_modelo': 'claude-opus-5-5',
                               'correo_puerto': '993'})
        self.assertEqual(ConfigRendicion.actual().ia_api_key, 'sk-1')  # vacío conserva la clave
        self.assertNotContains(self.client.get(url), 'sk-1')
        for nombre in ('proyectos', 'movil'):
            self.assertEqual(self.client.get(reverse(f'rendiciones:{nombre}')).status_code, 200)
        self.assertEqual(self.client.get(reverse('rendiciones:proyecto_nuevo')).status_code, 200)
        self.assertTrue(MovimientoTarjeta.objects.count() == 0 and User.objects.exists())
