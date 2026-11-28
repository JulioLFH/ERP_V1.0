"""v1.117.17: el QR de la guía electrónica lleva a SUNAT; al escanearlo se descarga la guía y se leen sus datos.
Nunca se consulta SUNAT en las pruebas: la descarga se simula con un PDF armado aquí."""
from decimal import Decimal as D
from unittest.mock import patch

from . import lector_qr
from . import servicios as srv
from .models import GuiaCliente
from .tests import RUC_CLIENTE, Base, xml_guia

URL = 'https://e-factura.sunat.gob.pe/v1/contribuyente/gre/comprobantes/descargaqr?hashqr=QUJDRDEyMzQ%3D'


def pdf_texto(lineas):
    """PDF mínimo con texto (como la representación impresa de SUNAT) para probar la lectura."""
    def esc(s):
        return s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    flujo = ('BT /F1 9 Tf 30 800 Td 12 TL ' + ' '.join(f'({esc(x)}) Tj T*' for x in lineas) + ' ET').encode('latin-1')
    objetos = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R '
               b'/Resources << /Font << /F1 5 0 R >> >> >>',
               b'<< /Length %d >>\nstream\n' % len(flujo) + flujo + b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>']
    salida, posiciones = bytearray(b'%PDF-1.4\n'), []
    for n, obj in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += b'%d 0 obj\n' % n + obj + b'\nendobj\n'
    xref = len(salida)
    salida += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objetos) + 1)
    for pos in posiciones:
        salida += b'%010d 00000 n \n' % pos
    salida += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objetos) + 1, xref)
    return bytes(salida)


def pdf_gre(ruc=RUC_CLIENTE, serie='EG07', numero='00000088'):
    return pdf_texto([
        'GUÍA DE REMISIÓN ELECTRÓNICA REMITENTE',
        'CLIENTE LOGÍSTICO SAC',
        f'RUC N° {ruc}',
        f'{serie} - N° {numero}',
        'Fecha y hora de emisión: 27/11/2026 09:15',
        'Fecha de inicio de Traslado: 27/11/2026',
        'Motivo de Traslado: VENTA',
        'Datos del Destinatario: RUC 20481234567 - DISTRIBUIDORA NORTE PIURA SAC',
        'Punto de Partida: AV. EL SOL 123 PARQUE INDUSTRIAL - LIMA - LIMA - VILLA EL SALVADOR',
        'Punto de Llegada: AA.HH. VILLA PERU CANADA MZ B LT 5 - PIURA - PIURA - VEINTISEIS DE OCTUBRE',
        'Peso Bruto Total de la Carga (KGM): 85.500',
        'Número de Bultos: 6',
        'Bienes por transportar: CAJAS DE REPUESTOS',
    ])


class QRSunatTest(Base):
    def setUp(self):
        super().setUp()
        lector_qr._CACHE.clear()
        self.sol = srv.crear_solicitud(self.cliente, 'MANUAL', mensaje='Bultos: 6')

    def test_lee_el_pdf_de_sunat(self):
        d = lector_qr.leer_documento(pdf_gre())
        self.assertEqual((d['emisor_ruc'], d['serie'], d['numero']), (RUC_CLIENTE, 'EG07', '88'))
        self.assertEqual((d['partida_ubigeo'], d['llegada_ubigeo']), ('150142', '200115'))  # Villa El Salvador · 26 de Octubre
        self.assertIn('AA.HH. VILLA PERU CANADA', d['llegada_direccion'])
        self.assertEqual((d['peso'], d['unidad_peso'], d['bultos'], d['motivo_traslado']), (D('85.500'), 'KGM', 6, '01'))
        self.assertEqual((d['destinatario_doc'], d['destinatario_tipo_doc']), ('20481234567', '6'))
        self.assertEqual(d['destinatario_nombre'], 'DISTRIBUIDORA NORTE PIURA SAC')
        self.assertEqual(str(d['fecha_traslado']), '2026-11-27')
        self.assertTrue(d['completos'])

    def test_solo_descarga_enlaces_https_de_sunat(self):
        self.assertTrue(lector_qr.es_enlace_sunat(URL))
        for otro in ('http://e-factura.sunat.gob.pe/x', 'https://sunat.gob.pe.malo.com/x', 'https://evil.com/?sunat.gob.pe',
                     'https://127.0.0.1/descargaqr'):
            self.assertFalse(lector_qr.es_enlace_sunat(otro), otro)
        with patch.object(lector_qr, '_descargar') as descarga:
            self.assertIsNone(lector_qr.obtener_datos_de_enlace('https://evil.com/descargaqr?hashqr=1'))
            descarga.assert_not_called()

    def test_escanear_el_qr_reconoce_la_guia_que_llego_por_xml(self):
        guia, _ = srv.registrar_guia_xml(xml_guia(serie='EG07', numero=88), self.cliente, self.sol)
        with patch.object(lector_qr, '_descargar', return_value=pdf_gre()) as descarga:
            info = srv.verificar(self.admin, 'RECOJO', URL)
            self.assertIn('EG07-', info['detalle'])
            g = srv.registrar_recojo(self.admin, URL)
        self.assertEqual(descarga.call_count, 1)  # verificar y confirmar: una sola descarga
        self.assertEqual(g.pk, guia.pk)
        self.assertEqual(GuiaCliente.objects.count(), 1)  # sin duplicar la guía
        g.refresh_from_db()
        self.assertEqual((g.estado.codigo, g.qr_texto), ('CONFIRMADO', URL))
        self.assertEqual(srv.buscar_guia(lector_qr.interpretar(URL)).pk, guia.pk)  # el ingreso la reconoce por su QR

    def test_escanear_el_qr_crea_la_guia_con_los_datos_de_sunat(self):
        with patch.object(lector_qr, '_descargar', return_value=pdf_gre(numero='123')):
            g = srv.registrar_recojo(self.admin, URL)
        g.refresh_from_db()
        self.assertEqual((g.serie, g.numero, g.origen_datos, g.datos_completos), ('EG07', '123', 'QR', True))
        self.assertEqual((g.llegada_ubigeo, g.departamento, g.bultos), ('200115', 'PIURA', 6))
        self.assertEqual(g.solicitud, self.sol)

    def test_sin_conexion_a_sunat_la_guia_queda_pendiente(self):
        self.sol.operador = self.admin  # sin RUC en el QR: va a la solicitud asignada al operador
        self.sol.save()
        with patch.object(lector_qr, '_descargar', side_effect=OSError('sin señal')):
            g = srv.registrar_recojo(self.admin, URL)
        g.refresh_from_db()
        self.assertEqual((g.serie, g.datos_completos, g.qr_texto), ('QR', False, URL))

    def test_xml_dentro_de_un_zip(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr(f'{RUC_CLIENTE}-09-EG07-9.xml', xml_guia(serie='EG07', numero=9))
        d = lector_qr.leer_documento(buf.getvalue())
        self.assertEqual((d['serie'], d['numero'], d['completos']), ('EG07', '9', True))
