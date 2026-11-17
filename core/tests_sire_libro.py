"""Libro oficial SIRE: el registro generado en SUNAT (RCE 8.4 / RVIE 14.4) se pide con un ticket, se baja el ZIP con el
TXT y la constancia de recepción (PDF), se guardan como sustento y se comparan con el registro del ERP por tipo de
comprobante. SUNAT se simula (no hay llamadas reales)."""
import base64
import io
import json
import urllib.parse
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase
from django.urls import reverse

from compras.models import Compra
from ventas.models import Venta

from . import sire_api, sire_envio, views_sire
from .models import Empresa, PerfilUsuario, RegistroSIRE, Tercero
from .modulos import GRUPOS
from .tests_sire_api import CAB_RCE, PERIODO, Base, zip_txt

D = Decimal
PDF = b'%PDF-1.4 constancia simulada'
TICKET = '20260500000123'


def nombre_txt(ruc, codigo):
    return f'LE{ruc}{PERIODO}00{codigo}0111122.txt'


class SunatLibro:
    """Responde como la API del SIRE para el registro generado; anota cada llamada."""

    def __init__(self, zip_registro, nombre_zip='registro.zip', constancia=PDF, pendientes=0, archivos=True,
                 contenido=''):
        self.zip, self.nombre_zip, self.constancia = zip_registro, nombre_zip, constancia
        self.pendientes, self.archivos, self.contenido, self.llamadas = pendientes, archivos, contenido, []

    def __call__(self, metodo, url, datos=None, cabeceras=None, binario=False):
        self.llamadas.append((metodo, url))
        if 'oauth2/token' in url:
            return {'access_token': 'TOKEN-SIMULADO', 'expires_in': 3600}
        if 'solicitardescarga' in url or 'registroslibros/descarga' in url:
            return {'numTicket': TICKET}
        if 'consultaestadotickets' in url:
            if self.pendientes:
                self.pendientes -= 1
                return {'registros': [{'numTicket': TICKET, 'codEstadoProceso': '05', 'desEstadoProceso': 'En proceso'}]}
            return {'registros': [{
                'numTicket': TICKET, 'perTributario': PERIODO, 'codProceso': '70', 'codEstadoProceso': '06',
                'desEstadoProceso': 'Terminado', 'detalleTicket': {'numTicket': TICKET},
                'archivoReporte': [{'codTipoAchivoReporte': None, 'nomArchivoReporte': self.nombre_zip,
                                    'nomArchivoContenido': self.contenido}] if self.archivos else []}]}
        if 'archivoreporte' in url:
            return self.zip
        if 'constancia' in url:
            return self.constancia
        raise AssertionError(f'URL no simulada: {url}')

    def url(self, texto):
        return next(u for _, u in self.llamadas if texto in u)


class RegistroComprasTest(Base):
    def test_descarga_guarda_sustento_y_compara(self):
        self.compra('101', '100')  # 118.00: coincide
        self.compra('102', '50')  # 59.00: solo en el ERP
        ruc = Empresa.actual().ruc
        txt = self.rce(('101', '20600000022', 'ANDINA SAC', '100.00', '18.00', '118.00'),
                       ('205', '20611111111', 'PROVEEDOR NUEVO SAC', '50.00', '9.00', '59.00'))
        nombre = nombre_txt(ruc, '080400')
        sunat = SunatLibro(zip_txt(nombre, txt), nombre_zip=nombre[:-4] + '.zip')
        with patch('core.sire_api._http', sunat):
            r = self.client.post(reverse('sire_libro_oficial'), {'libro': 'RCE', 'periodo': '2026-09'})
        x = RegistroSIRE.objects.get()
        self.assertRedirects(r, reverse('sire_libro_oficial_detalle', args=[x.pk]), fetch_redirect_response=False)
        self.assertEqual((x.estado, x.ticket, len(x.registros)), ('DESCARGADO', TICKET, 2))
        # servicio 5.51 del manual de compras: registro por periodo con ticket
        url = urllib.parse.urlsplit(sunat.url('solicitardescarga'))
        self.assertTrue(url.path.endswith('/rce/ajustesposteriores/web/ajustesposteriores/202609/solicitardescarga'))
        q = dict(urllib.parse.parse_qsl(url.query))
        self.assertEqual((q['codProceso'], q['codOrigen'], q['lisPeriodos'], q['codTipoArchivo'], q['codMoneda']),
                         (sire_api.PROCESO_REGISTRO['RCE'], '2', PERIODO, '0', 'PEN'))
        self.assertIn('codLibro=080000', sunat.url('archivoreporte'))
        # sustento: el ZIP de SUNAT y la constancia 5.49 con el nombre del TXT
        self.assertEqual(bytes(x.archivo), sunat.zip)
        self.assertEqual(x.nombre_constancia, nombre[:-4] + '.pdf')
        self.assertIn('constanciarecepcion?nomConstanciaRecepcion=' + x.nombre_constancia, sunat.url('constancia'))
        self.assertEqual(bytes(x.constancia), PDF)
        self.assertIsNotNone(x.constancia_en)
        # comparación: mismos totales por tipo pero dos comprobantes distintos
        c = x.comparacion
        t = c['tipos'][0]
        self.assertEqual((t['tipo'], t['sunat_n'], D(t['sunat_total']), t['erp_n'], D(t['erp_total']), t['dif_n']),
                         ('01', 2, D('177.00'), 2, D('177.00'), 0))
        self.assertEqual((c['coinciden'], c['con_diferencia'], c['solo_sunat'], c['solo_erp']), (1, 0, 1, 1))
        self.assertEqual(sorted((d['resultado'], d['numero']) for d in c['diferencias']),
                         [('Solo en SUNAT', '205'), ('Solo en el ERP', '102')])
        # pantalla, descargas y Excel
        r = self.client.get(reverse('sire_libro_oficial_detalle', args=[x.pk]))
        self.assertContains(r, 'Solo en el ERP')
        self.assertContains(r, '177.00')
        r = self.client.get(reverse('sire_libro_oficial_detalle', args=[x.pk]) + '?descargar=constancia')
        self.assertEqual((r['Content-Type'], r.content), ('application/pdf', PDF))
        r = self.client.get(reverse('sire_libro_oficial_detalle', args=[x.pk]) + '?descargar=archivo')
        self.assertEqual(r.content, sunat.zip)
        self.assertIn(nombre[:-4] + '.zip', r['Content-Disposition'])
        r = self.client.get(reverse('sire_libro_oficial_detalle', args=[x.pk]) + '?formato=excel')
        self.assertEqual(r.content[:2], b'PK')
        self.assertContains(self.client.get(reverse('sire_libro_oficial')), '2 diferencia(s)')

    def test_diferencia_de_importe_y_comparar_de_nuevo(self):
        c = self.compra('101', '100')
        sunat = SunatLibro(zip_txt('registro.txt', self.rce(('101', '20600000022', 'ANDINA SAC', '100.00', '18.00',
                                                            '120.00'))))
        with patch('core.sire_api._http', sunat):
            self.client.post(reverse('sire_libro_oficial'), {'libro': 'RCE', 'periodo': '2026-09'})
        x = RegistroSIRE.objects.get()
        d = x.comparacion['diferencias'][0]
        self.assertEqual((d['resultado'], D(d['sunat_total']), D(d['erp_total']), D(d['dif']), d['id']),
                         ('Diferencia de importe', D('120.00'), D('118.00'), D('-2.00'), c.pk))
        # sin nombre LE… no se pide la constancia: se indica a mano
        self.assertEqual((x.nombre_constancia, x.constancia), ('', None))
        self.assertFalse(any('constancia' in u for _, u in sunat.llamadas))
        # se corrige la compra en el ERP y se vuelve a comparar con lo guardado (sin llamar a SUNAT)
        Compra.objects.filter(pk=c.pk).update(total=D('120.00'))
        self.client.post(reverse('sire_libro_oficial_detalle', args=[x.pk]), {'accion': 'comparar'})
        x.refresh_from_db()
        self.assertEqual((x.comparacion['coinciden'], x.comparacion['diferencias']), (1, []))
        with patch('core.sire_api._http', sunat):
            self.client.post(reverse('sire_libro_oficial_detalle', args=[x.pk]),
                             {'accion': 'constancia', 'nombre_constancia': 'LE2010000000120260900080400011112.pdf'})
        x.refresh_from_db()
        self.assertEqual((bytes(x.constancia), x.nombre_constancia), (PDF, 'LE2010000000120260900080400011112.pdf'))


class RegistroVentasTest(Base):
    def test_txt_sin_titulos_en_dolares_y_constancia_en_base64(self):
        cliente = Tercero.objects.filter(tipo='CLIENTE').first()
        Venta.objects.create(tercero=cliente, tipo_comprobante='01', serie='F001', numero='501',
                                 fecha_emision=date(2026, 9, 5), total=D('118'), igv=D('18'), base_imponible=D('100'))
        empresa = Empresa.actual()

        def linea(numero, total, moneda='PEN', tc=''):
            fila = {'Ruc': empresa.ruc, 'Periodo': PERIODO, 'Fecha de emisión': '05/09/2026', 'Tipo CP/Doc.': '01',
                    'Serie del CDP': 'F001', 'Nro CP o Doc. Nro Inicial (Rango)': numero, 'Tipo Doc Identidad': '6',
                    'Nro Doc Identidad': cliente.numero_doc, 'Apellidos Nombres/ Razón Social': cliente.nombre,
                    'Total CP': total, 'Moneda': moneda, 'Tipo Cambio': tc}
            return '|'.join(str(fila.get(c, '')) for c in sire_envio.COLUMNAS_RVIE) + '|'
        txt = '\n'.join([linea('501', '118.00'), linea('502', '100.00', 'USD', '3.750')])
        nombre = nombre_txt(empresa.ruc, '140400')
        constancia = json.dumps({'archivoPdf': base64.b64encode(PDF).decode()}).encode()
        sunat = SunatLibro(zip_txt(nombre, txt), constancia=constancia, pendientes=1)
        with patch('core.sire_api._http', sunat):
            self.client.post(reverse('sire_libro_oficial'), {'libro': 'RVIE', 'periodo': '2026-09'})
            x = RegistroSIRE.objects.get()
            self.assertEqual(x.estado, 'SOLICITADO')
            self.assertContains(self.client.get(reverse('sire_libro_oficial_detalle', args=[x.pk])),
                                'Consultar a SUNAT')
            self.client.post(reverse('sire_libro_oficial_detalle', args=[x.pk]), {'accion': 'consultar'})
        x.refresh_from_db()
        self.assertEqual((x.estado, len(x.registros)), ('DESCARGADO', 2))
        # servicio 5.27 del manual de ventas
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(sunat.url('registroslibros/descarga')).query))
        self.assertEqual((q['perTributario'], q['codProceso'], q['codLibro'], q['codOrigenEnvio']),
                         (PERIODO, sire_api.PROCESO_REGISTRO['RVIE'], '140000', '2'))
        self.assertIn('constancia/archivo?nomArchivo=' + nombre[:-4] + '.pdf', sunat.url('constancia'))
        self.assertEqual(bytes(x.constancia), PDF)
        t = x.comparacion['tipos'][0]
        # la factura en dólares va en soles con el tipo de cambio de SUNAT: 118 + 100 × 3.75
        self.assertEqual((t['sunat_n'], D(t['sunat_total']), t['erp_n'], D(t['erp_total'])),
                         (2, D('493.00'), 1, D('118.00')))
        self.assertEqual([(d['resultado'], d['numero'], D(d['sunat_total'])) for d in x.comparacion['diferencias']],
                         [('Solo en SUNAT', '502', D('375.00'))])

    def test_sin_archivo_queda_en_error(self):
        sunat = SunatLibro(b'', archivos=False)
        with patch('core.sire_api._http', sunat):
            self.client.post(reverse('sire_libro_oficial'), {'libro': 'RVIE', 'periodo': '2026-09'})
        x = RegistroSIRE.objects.get()
        self.assertEqual(x.estado, 'ERROR')
        self.assertIn('portal SIRE', x.mensaje)


class PermisosLibroTest(Base):
    def test_solo_los_registros_de_su_modulo(self):
        x = RegistroSIRE.objects.create(libro='RCE', periodo=PERIODO, estado='DESCARGADO', archivo=b'PK',
                                        nombre_archivo='r.zip')
        vendedor = User.objects.create_user('vendedor_libro', password='Clave-segura-9')
        vendedor.groups.add(Group.objects.get_or_create(name=GRUPOS['ventas'])[0])
        PerfilUsuario.objects.create(usuario=vendedor, acciones=['ventas.emitir'])
        self.client.force_login(vendedor)
        r = self.client.get(reverse('sire_libro_oficial_detalle', args=[x.pk]) + '?descargar=archivo')
        self.assertRedirects(r, reverse('sire_libro_oficial'), fetch_redirect_response=False)
        r = self.client.get(reverse('sire_libro_oficial'))
        self.assertNotContains(r, reverse('sire_libro_oficial_detalle', args=[x.pk]))
        with patch('core.sire_api._http', SunatLibro(b'')):
            self.client.post(reverse('sire_libro_oficial'), {'libro': 'RCE', 'periodo': '2026-09'})
        self.assertEqual(RegistroSIRE.objects.count(), 1)  # no puede pedir el registro de compras

    def test_sin_credenciales(self):
        Empresa.objects.update(sire_client_id='', sire_client_secret='', sire_usuario_sol='', sunat_client_id='')
        with patch('core.sire_api._http', side_effect=AssertionError('no debe llamar a SUNAT')):
            r = self.client.post(reverse('sire_libro_oficial'), {'libro': 'RCE', 'periodo': '2026-09'}, follow=True)
        self.assertContains(r, 'Credenciales SUNAT y SIRE')
        self.assertFalse(RegistroSIRE.objects.exists())


class LecturaTest(SimpleTestCase):
    def test_pdf_en_sus_formas(self):
        self.assertEqual(sire_api._pdf(PDF), PDF)
        self.assertEqual(sire_api._pdf(json.dumps(list(PDF)).encode()), PDF)
        self.assertEqual(sire_api._pdf(json.dumps({'archivoPdf': list(PDF)}).encode()), PDF)
        self.assertEqual(sire_api._pdf(base64.b64encode(PDF)), PDF)
        self.assertEqual(sire_api._pdf(json.dumps(base64.b64encode(PDF).decode()).encode()), PDF)
        with self.assertRaises(sire_api.ErrorSIRE):
            sire_api._pdf(b'{"cod": "422", "msg": "error"}')

    def test_nombre_de_la_constancia(self):
        self.assertEqual(sire_api.nombre_constancia('', 'x.zip', 'LE20100000001202609000804000111122.txt'),
                         'LE20100000001202609000804000111122.pdf')
        self.assertEqual(sire_api.nombre_constancia('registro.zip', None), '')

    def test_zip_con_varios_txt_toma_el_del_registro(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('LE20100000001202609000805000111122.txt', 'no domiciliados')
            z.writestr('LE20100000001202609000804000111122.txt', CAB_RCE + '\n20100000001|MI EMPRESA|202609|X|'
                       '10/09/2026||01|F001||7||6|20600000022|ANDINA SAC|10.00|1.80|11.80|PEN')
        registros, nombre = views_sire.leer_registro('RCE', buf.getvalue())
        self.assertEqual(nombre, 'LE20100000001202609000804000111122.txt')
        self.assertEqual([(r['serie'], r['numero'], r['total']) for r in registros], [('F001', '7', D('11.80'))])

    def test_txt_sin_titulos_rce(self):
        fila = {'RUC': '20100000001', 'Periodo': PERIODO, 'Fecha de emisión': '10/09/2026', 'Tipo CP/Doc.': '07',
                'Serie del CDP': 'F001', 'Nro CP o Doc. Nro Inicial (Rango)': '00000009', 'Nro Doc Identidad':
                '20600000022', 'BI Gravado DG': '-10.00', 'IGV / IPM DG': '-1.80', 'Total CP': '-11.80',
                'Moneda': 'PEN'}
        txt = '|'.join(str(fila.get(c, '')) for c in sire_envio.COLUMNAS_RCE) + '|'
        registros, _ = views_sire.leer_registro('RCE', txt.encode('latin-1'), 'LE.txt')
        self.assertEqual([(r['tipo'], r['numero'], r['total'], r['ruc']) for r in registros],
                         [('07', '9', D('11.80'), '20600000022')])

    def test_zip_sin_txt(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('leame.pdf', 'x')
        from compras import sire
        with self.assertRaises(sire.ErrorSIRE):
            views_sire.leer_registro('RVIE', buf.getvalue())
