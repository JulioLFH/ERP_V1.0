"""SIR-01 a SIR-03: «Enviar al SIRE» con lo registrado (reemplazo de la propuesta 14.4 / 8.4, no domiciliados 8.5 y
ajustes posteriores), su nombre de archivo, las filas del TXT (notas de crédito en negativo), las validaciones previas,
la carga por TUS con ticket (SUNAT simulado) y la exportación del registro en PLE o SIRE según la empresa."""
import base64
import io
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra
from ventas.models import Venta

from . import sire_envio
from .models import Empresa, EnvioSIRE, Tercero

D = Decimal
PERIODO = '202609'
RUC = '20100000001'


def leer_zip(contenido):
    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        return {n: z.read(n).decode('iso-8859-1') for n in z.namelist()}


class SunatSimulado:
    """API del SIRE simulada: token, carga TUS (POST + PATCH), estado del ticket, reporte y registro del preliminar."""

    def __init__(self, terminado=True, reporte=None):
        self.terminado, self.reporte, self.llamadas, self.metadatos = terminado, reporte, [], {}

    def http(self, metodo, url, datos=None, cabeceras=None, binario=False):
        self.llamadas.append((metodo, url))
        if 'oauth2/token' in url:
            return {'access_token': 'TOKEN', 'expires_in': 3600}
        if 'consultaestadotickets' in url:
            if not self.terminado:
                return {'registros': [{'numTicket': '20260100000555', 'codEstadoProceso': '02',
                                       'desEstadoProceso': 'En proceso'}]}
            r = {'numTicket': '20260100000555', 'perTributario': PERIODO, 'codProceso': '61',
                 'codEstadoProceso': '06', 'desEstadoProceso': 'Terminado'}
            if self.reporte:
                r['archivoReporte'] = [{'codTipoAchivoReporte': '01', 'nomArchivoReporte': 'inconsistencias.zip'}]
            return {'registros': [r]}
        if 'archivoreporte' in url:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as z:
                z.writestr('inconsistencias.txt', self.reporte)
            return buf.getvalue()
        if 'registrapreliminar' in url or 'registrarajustesposteriores' in url:
            return {'numTicket': '20260100000777'}
        raise AssertionError(f'URL no simulada: {url}')

    def crudo(self, metodo, url, datos, cabeceras):
        self.llamadas.append((metodo, url))
        if metodo == 'POST':
            self.metadatos = {k: base64.b64decode(v).decode() for k, v in
                              (x.split(' ') for x in cabeceras['Upload-Metadata'].split(','))}
            self.subido_largo = int(cabeceras['Upload-Length'])
            return {'Location': '/v1/contribuyente/migeigv/libros/rvierce/archivo/abc123'}, b''
        self.subido = datos
        assert cabeceras['Upload-Offset'] == '0'
        return {}, b'{"numTicket": "20260100000555"}'

    def __enter__(self):
        self._p = [patch('core.sire_api._http', self.http), patch('core.sire_api._http_crudo', self.crudo)]
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._p:
            p.stop()


class Base(TestCase):
    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        cache.clear()
        from .cifrado import cifrar
        Empresa.actual()
        Empresa.objects.update(ruc=RUC, razon_social='MI EMPRESA S.A.C.', sire_client_id='cid',
                               sire_client_secret='secreto', sire_usuario_sol='CONTA01', sire_clave_sol=cifrar('Clave'))
        self.empresa = Empresa.actual()
        self.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20600000022',
                                           nombre='ANDINA | SAC')
        self.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20500000011',
                                              nombre='CLIENTE SAC')
        self.exterior = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='0', numero_doc='US-998877',
                                               nombre='CLOUD SOFTWARE INC', pais='Estados Unidos',
                                               direccion='1 Main St, Seattle')
        self.admin = User.objects.create_superuser('admin_envio', 'a@a.pe', 'Clave-segura-9')
        self.client.force_login(self.admin)

    def compra(self, numero, base='100.00', tipo='01', ref=None, tercero=None, **extra):
        base = D(base)
        igv = (base * D('0.18')).quantize(D('0.01'))
        return Compra.objects.create(tercero=tercero or self.prov, tipo_comprobante=tipo, serie='F001', numero=numero,
                                     fecha_emision=date(2026, 9, 10), base_imponible=base, igv=igv, total=base + igv,
                                     doc_referencia=ref, ingresar_almacen=False, **extra)

    def venta(self, numero, base='200.00', tipo='01', ref=None, **extra):
        base = D(base)
        igv = (base * D('0.18')).quantize(D('0.01'))
        return Venta.objects.create(tercero=self.cliente, tipo_comprobante=tipo, serie='F001', numero=numero,
                                    fecha_emision=date(2026, 9, 5), base_imponible=base, igv=igv, total=base + igv,
                                    doc_referencia=ref, descontar_stock=False, **extra)

    def columnas(self, registro, linea):
        valores = linea.split('|')
        self.assertEqual(valores[-1], '')  # cada línea termina en «|»
        return dict(zip(sire_envio.COLUMNAS[registro], valores[:-1]))

    def datos(self, **extra):
        return {'registro': '080400', 'periodo': '2026-09', 'oportunidad': '02', 'ind_operaciones': '1',
                'ind_moneda': '1', **extra}


class NombreArchivoTest(Base):
    def test_nombre_segun_la_norma(self):
        self.assertEqual(sire_envio.nombre_archivo(RUC, PERIODO, '080400'), f'LE{RUC}{PERIODO}00080400021112.txt')
        self.assertEqual(sire_envio.nombre_archivo(RUC, PERIODO, '140400', contenido='0'),
                         f'LE{RUC}{PERIODO}00140400021012.txt')
        # ajuste posterior: oportunidad 03 y el correlativo NN al final (manual RVIE: …1404000311 1201.zip)
        self.assertEqual(sire_envio.nombre_archivo(RUC, PERIODO, '140400', '03', correlativo=1),
                         f'LE{RUC}{PERIODO}0014040003111201.txt')
        self.assertEqual(sire_envio.nombre_archivo(RUC, PERIODO, '080500', '02', '0', '0', '2'),
                         f'LE{RUC}{PERIODO}00080500020022.txt')
        a = sire_envio.generar('080400', PERIODO, docs=[self.compra('1')])
        self.assertEqual(a['nombre_zip'], f'LE{RUC}{PERIODO}00080400021112.zip')
        self.assertEqual(list(leer_zip(a['zip'])), [a['nombre_txt']])


class FilasTest(Base):
    def test_compra_factura_y_nota_de_credito_en_negativo(self):
        f = self.compra('101', '100.00')
        nc = self.compra('5', '20.00', tipo='07', ref=f)
        nc.serie = 'FC01'
        nc.save()
        self.assertEqual(nc.total, D('23.60'))  # en el ERP la nota se guarda en positivo
        txt = sire_envio.generar('080400', PERIODO)['txt']
        lineas = txt.split('\r\n')[:-1]
        self.assertEqual(len(lineas), 2)
        self.assertTrue(txt.endswith('|\r\n'))
        fila = self.columnas('080400', lineas[0])
        self.assertEqual(len(lineas[0].split('|')) - 1, len(sire_envio.COLUMNAS_RCE))
        self.assertEqual((fila['RUC'], fila['Periodo'], fila['Tipo CP/Doc.'], fila['Serie del CDP'],
                          fila['Nro CP o Doc. Nro Inicial (Rango)'], fila['Nro Doc Identidad'],
                          fila['Apellidos Nombres/ Razón Social'], fila['BI Gravado DG'], fila['IGV / IPM DG'],
                          fila['Total CP'], fila['Moneda'], fila['Tipo de Cambio'], fila['Fecha de emisión']),
                         (RUC, PERIODO, '01', 'F001', '101', '20600000022', 'ANDINA SAC', '100.00', '18.00',
                          '118.00', 'PEN', '1.000', '10/09/2026'))
        self.assertEqual(fila['CAR SUNAT'], '2060000002201F0010000000101')
        nota = self.columnas('080400', lineas[1])
        self.assertEqual((nota['Tipo CP/Doc.'], nota['BI Gravado DG'], nota['IGV / IPM DG'], nota['Total CP']),
                         ('07', '-20.00', '-3.60', '-23.60'))
        self.assertEqual((nota['Fecha Emisión Doc Modificado'], nota['Tipo CP Modificado'],
                          nota['Serie CP Modificado'], nota['Nro CP Modificado']), ('10/09/2026', '01', 'F001', '101'))

    def test_compra_destino_comun_y_anulada(self):
        self.compra('7', '50.00', destino_igv='COMUN')
        anulada = self.compra('8', '40.00')
        anulada.estado = 'ANULADO'
        anulada.save()
        docs = sire_envio.documentos('080400', PERIODO)
        self.assertEqual([d.numero for d in docs], ['7'])  # la compra anulada no se anota
        fila = self.columnas('080400', sire_envio.linea('080400', self.empresa, PERIODO, docs[0]))
        self.assertEqual((fila['BI Gravado DG'], fila['BI Gravado DGNG'], fila['IGV / IPM DGNG']),
                         ('0.00', '50.00', '9.00'))

    def test_venta_factura_nota_y_anulada(self):
        f = self.venta('501')
        nc = self.venta('9', '50.00', tipo='07', ref=f, motivo_nota='01')
        nc.serie = 'FC01'
        nc.save()
        anulada = self.venta('502')
        anulada.estado = 'ANULADO'
        anulada.save()
        lineas = sire_envio.generar('140400', PERIODO)['txt'].split('\r\n')[:-1]
        filas = {self.columnas('140400', x)['Nro CP o Doc. Nro Inicial (Rango)']: self.columnas('140400', x)
                 for x in lineas}
        self.assertEqual(len(lineas[0].split('|')) - 1, len(sire_envio.COLUMNAS_RVIE))
        self.assertEqual((filas['501']['BI Gravada'], filas['501']['IGV / IPM'], filas['501']['Total CP'],
                          filas['501']['Tipo Doc Identidad'], filas['501']['Nro Doc Identidad']),
                         ('200.00', '36.00', '236.00', '6', '20500000011'))
        self.assertEqual((filas['9']['BI Gravada'], filas['9']['IGV / IPM'], filas['9']['Total CP'],
                          filas['9']['Tipo CP Modificado'], filas['9']['Nro CP Modificado'], filas['9']['Tipo de Nota']),
                         ('-50.00', '-9.00', '-59.00', '01', '501', '01'))
        self.assertEqual(filas['502']['Total CP'], '0.00')  # anulada: en cero (nota 3 del Anexo N.° 3)


class ValidacionTest(Base):
    def test_errores_bloquean_el_archivo(self):
        self.compra('1')
        self.compra('2', tipo='07')  # nota sin el comprobante que modifica
        mal = self.compra('3')
        Compra.objects.filter(pk=mal.pk).update(total=D('999.00'))
        sin_ruc = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='1', numero_doc='44556677', nombre='JUAN PEREZ')
        self.compra('4', tercero=sin_ruc)
        errores, _ = sire_envio.validar('080400', PERIODO, sire_envio.documentos('080400', PERIODO))
        texto = ' '.join(errores)
        self.assertEqual(len(errores), 3)
        self.assertIn('la nota no indica el comprobante que modifica', texto)
        self.assertIn('no es base + IGV + otros', texto)
        self.assertIn('debe tener RUC de 11 dígitos', texto)
        r = self.client.post(reverse('sire_envios'), {**self.datos(), 'accion': 'descargar'})
        self.assertEqual(r.status_code, 200)  # no descarga: muestra los errores
        self.assertContains(r, 'Errores a corregir antes de generar el archivo (3)')
        self.assertFalse(EnvioSIRE.objects.exists())

    def test_otros_cargos_cuadran_el_total(self):
        """v1.116.5: factura de restaurante con recargo al consumo (otros cargos): no es error y va en el TXT."""
        c = self.compra('5', base='139.52', otros_cargos=D('8.37'))
        Compra.objects.filter(pk=c.pk).update(igv=D('25.11'), total=D('173.00'))
        docs = sire_envio.documentos('080400', PERIODO)
        self.assertEqual(sire_envio.validar('080400', PERIODO, docs), ([], []))
        linea = sire_envio.generar('080400', PERIODO, '02', '1', '1', None, docs=docs)['txt'].splitlines()[0]
        self.assertIn('|8.37|', linea)

    def test_validar_sin_errores_y_descargar(self):
        self.compra('1')
        r = self.client.post(reverse('sire_envios'), {**self.datos(), 'accion': 'validar'}, follow=True)
        self.assertContains(r, 'sin errores')
        r = self.client.post(reverse('sire_envios'), {**self.datos(), 'accion': 'descargar'})
        self.assertEqual(r['Content-Type'], 'application/zip')
        self.assertIn(f'LE{RUC}{PERIODO}00080400021112.zip', r['Content-Disposition'])
        self.assertEqual(self.client.get(reverse('sire_envios')).status_code, 200)


class EnvioReemplazoTest(Base):
    def test_sube_por_tus_consulta_ticket_e_inconsistencias(self):
        self.compra('1')
        reporte = 'Fila 1|01|F001|1|1044 Error en el tipo de cambio'
        with SunatSimulado(reporte=reporte) as sunat:
            r = self.client.post(reverse('sire_envios'), {**self.datos(), 'accion': 'enviar'})
            e = EnvioSIRE.objects.get()
            self.assertRedirects(r, reverse('sire_envio', args=[e.pk]), fetch_redirect_response=False)
            self.assertEqual((e.ticket, e.estado, e.filas, e.libro), ('20260100000555', 'OBSERVADO', 1, 'RCE'))
            self.assertEqual(e.inconsistencias, [reporte])
            m = sunat.metadatos
            self.assertEqual((m['codProceso'], m['codLibro'], m['perTributario'], m['numRuc'], m['codOrigenEnvio'],
                              m['codTipoCorrelativo'], m['filetype']),
                             ('61', '080000', PERIODO, RUC, '2', '01', 'application/zip'))
            self.assertEqual(m['nomArchivoImportacion'], f'LE{RUC}{PERIODO}00080400021112.zip')
            subida = next(u for metodo, u in sunat.llamadas if metodo == 'POST' and 'upload' in u)
            self.assertTrue(subida.endswith('/rvierce/receptorpropuesta/web/propuesta/upload'))
            self.assertEqual(bytes(e.archivo), sunat.subido)
            self.assertEqual(sunat.subido_largo, len(sunat.subido))
            self.assertContains(self.client.get(reverse('sire_envio', args=[e.pk])), '1044 Error en el tipo de cambio')
            self.client.post(reverse('sire_envio', args=[e.pk]), {'accion': 'registrar'})
        e.refresh_from_db()
        self.assertEqual(e.ticket_registro, '20260100000777')
        self.assertIsNotNone(e.registrado_en)
        self.assertTrue(sunat.llamadas[-1][1].endswith('/rce/preliminar/web/registroslibros/202609/registrapreliminares'))

    def test_ventas_ticket_en_proceso_y_consulta(self):
        self.venta('1')
        sunat = SunatSimulado(terminado=False)
        with sunat:
            self.client.post(reverse('sire_envios'), {**self.datos(registro='140400'), 'accion': 'enviar'})
            e = EnvioSIRE.objects.get()
            self.assertEqual((e.estado, e.libro, sunat.metadatos['codProceso'], sunat.metadatos['codLibro']),
                             ('ENVIADO', 'RVIE', '3', '140000'))
            sunat.terminado = True
            self.client.post(reverse('sire_envio', args=[e.pk]), {'accion': 'consultar'})
        e.refresh_from_db()
        self.assertEqual((e.estado, e.inconsistencias), ('TERMINADO', []))

    def test_sin_permiso_de_cierre_no_envia(self):
        from django.contrib.auth.models import Group

        from .models import PerfilUsuario
        from .modulos import GRUPOS
        self.compra('1')
        tributario = User.objects.create_user('tributario', password='Clave-segura-9')
        tributario.groups.add(Group.objects.get_or_create(name=GRUPOS['sunat'])[0])
        PerfilUsuario.objects.create(usuario=tributario, acciones=['sunat.sire'])
        self.client.force_login(tributario)
        with SunatSimulado() as sunat:
            r = self.client.post(reverse('sire_envios'), {**self.datos(), 'accion': 'enviar'}, follow=True)
        self.assertFalse(EnvioSIRE.objects.exists())
        self.assertFalse(sunat.llamadas)
        self.assertContains(r, 'exige el permiso de cerrar periodos contables')


class NoDomiciliadosTest(Base):
    def test_91_va_al_8_5_y_no_al_8_4(self):
        self.compra('1')
        nd = self.compra('3001', '1000.00', tipo='91', tercero=self.exterior, moneda='USD', tipo_cambio=D('3.750'))
        Compra.objects.filter(pk=nd.pk).update(igv=0, total=D('1000.00'), serie='')
        self.assertEqual([d.numero for d in sire_envio.documentos('080400', PERIODO)], ['1'])
        docs = sire_envio.documentos('080500', PERIODO)
        self.assertEqual([d.numero for d in docs], ['3001'])
        self.assertEqual(sire_envio.validar('080500', PERIODO, docs), ([], []))
        fila = self.columnas('080500', sire_envio.linea('080500', self.empresa, PERIODO, docs[0]))
        self.assertEqual((fila['Tipo CP/Doc.'], fila['Valor de las adquisiciones'], fila['Importe total'],
                          fila['Moneda'], fila['Tipo de Cambio'], fila['País de residencia del no domiciliado'],
                          fila['Apellidos y nombres o razón social del no domiciliado'],
                          fila['Nro identificación del no domiciliado']),
                         ('91', '1000.00', '1000.00', 'USD', '3.750', '9249', 'CLOUD SOFTWARE INC', 'US-998877'))
        with SunatSimulado() as sunat:
            self.client.post(reverse('sire_envios'), {**self.datos(registro='080500'), 'accion': 'enviar'})
        e = EnvioSIRE.objects.get()
        self.assertEqual((e.registro, e.nombre_archivo, e.filas),
                         ('080500', f'LE{RUC}{PERIODO}00080500021112.zip', 1))
        self.assertEqual(sunat.metadatos['codProceso'], '56')
        self.assertTrue(any(u.endswith('/rvierce/receptorpreliminar/web/preliminar/upload') for _, u in sunat.llamadas))

    def test_pais_sin_codigo_es_aviso(self):
        self.exterior.pais = 'Narnia'
        self.exterior.save()
        nd = self.compra('3002', '10.00', tipo='91', tercero=self.exterior)
        _, avisos = sire_envio.validar('080500', PERIODO, [nd])
        self.assertIn('no tiene código SUNAT', avisos[0])


class AjustePosteriorTest(Base):
    def test_correlativo_y_solo_los_nuevos(self):
        self.compra('1')
        with SunatSimulado():
            self.client.post(reverse('sire_envios'), {**self.datos(), 'accion': 'enviar'})
            self.assertEqual(EnvioSIRE.objects.get().estado, 'TERMINADO')
            nueva = self.compra('2')
            self.client.post(reverse('sire_envios'), {**self.datos(oportunidad='03', solo_nuevos='on'),
                                                      'accion': 'enviar'})
            primero = EnvioSIRE.objects.filter(oportunidad='03').get()
            self.assertEqual((primero.correlativo, primero.filas, primero.nombre_archivo),
                             (1, 1, f'LE{RUC}{PERIODO}0008040003111201.zip'))
            self.assertIn(nueva.numero, leer_zip(bytes(primero.archivo))[primero.nombre_archivo[:-4] + '.txt'])
            self.client.post(reverse('sire_envios'), {**self.datos(oportunidad='03'), 'accion': 'enviar'})
            segundo = EnvioSIRE.objects.filter(oportunidad='03').order_by('-pk').first()
            self.assertEqual((segundo.correlativo, segundo.filas), (2, 2))
            self.assertTrue(segundo.nombre_archivo.endswith('03111202.zip'))
            self.client.post(reverse('sire_envio', args=[segundo.pk]), {'accion': 'registrar'})
        segundo.refresh_from_db()
        self.assertEqual(segundo.ticket_registro, '20260100000777')

    def test_ajuste_sin_comprobantes_es_error(self):
        r = self.client.post(reverse('sire_envios'), {**self.datos(oportunidad='03'), 'accion': 'descargar'})
        self.assertContains(r, 'No hay comprobantes para el ajuste posterior')


class ExportarRegistroTest(Base):
    """SIR-03: PLE (8.1 / 14.1) para la empresa no obligada al SIRE; formato SIRE para la obligada."""

    def test_ple_o_sire_segun_la_empresa(self):
        self.compra('1')
        self.compra('3001', '10.00', tipo='91', tercero=self.exterior)
        self.venta('1')
        r = self.client.get(reverse('compras:registro'), {'periodo': PERIODO, 'formato': 'ple'})
        self.assertIn('text/plain', r['Content-Type'])
        self.assertIn(f'LE{RUC}{PERIODO}00080100', r['Content-Disposition'])
        self.assertContains(self.client.get(reverse('compras:registro'), {'periodo': PERIODO}), 'TXT PLE')
        Empresa.objects.update(obligado_sire=True)
        self.assertContains(self.client.get(reverse('compras:registro'), {'periodo': PERIODO}), 'TXT SIRE')
        r = self.client.get(reverse('compras:registro'), {'periodo': PERIODO, 'formato': 'ple'})
        self.assertEqual(r['Content-Type'], 'application/zip')
        archivos = leer_zip(r.content)
        self.assertEqual(sorted(archivos), [f'LE{RUC}{PERIODO}00080400021112.txt',
                                            f'LE{RUC}{PERIODO}00080500021112.txt'])
        r = self.client.get(reverse('ventas:registro'), {'periodo': PERIODO, 'formato': 'ple'})
        self.assertEqual(list(leer_zip(r.content)), [f'LE{RUC}{PERIODO}00140400021112.txt'])
        from . import estudio
        datos, notas = estudio.ple_zip(['default'], PERIODO)
        nombres = zipfile.ZipFile(io.BytesIO(datos)).namelist()
        self.assertEqual(sorted(n.split('/')[-1][21:27] for n in nombres), ['080400', '080500', '140400'])
        self.assertIn('SIRE', notas[0][1])
        Empresa.objects.update(obligado_sire=False)
        nombres = zipfile.ZipFile(io.BytesIO(estudio.ple_zip(['default'], PERIODO)[0])).namelist()
        self.assertEqual(sorted(n.split('/')[-1][21:27] for n in nombres), ['080100', '140100'])

    def test_obligado_editable_en_ajustes_empresa(self):
        from .forms import EmpresaForm
        self.assertIn('obligado_sire', EmpresaForm().fields)


class CodificacionTest(Base):
    def test_txt_en_iso_8859_1_con_tildes(self):
        """v1.115.1: «BANCO BBVA PERÚ» iba en UTF-8 y SUNAT rechazaba la fila (número de campos incorrecto)."""
        self.prov.nombre = 'BANCO BBVA PERÚ'
        self.prov.save()
        self.compra('77')
        arch = sire_envio.generar('080400', PERIODO)
        crudo = next(iter(leer_zip_bytes(arch['zip']).values()))
        self.assertIn(b'BANCO BBVA PERU|', crudo)  # v1.115.2: sin tildes (SUNAT rechaza la fila)
        self.assertTrue(all(c < 128 for c in crudo))
        self.assertEqual(crudo.decode('iso-8859-1').count('|'), arch['txt'].count('|'))


def leer_zip_bytes(datos):
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        return {n: z.read(n) for n in z.namelist()}


class VencimientoDeSunatTest(Base):
    def test_toma_la_fecha_vcto_de_la_propuesta(self):
        """v1.115.2: si la propuesta de SUNAT trae «Fecha Vcto/Pago» para el comprobante, el archivo la lleva (sin ella
        SUNAT rechaza la fila por «número de campos incorrecto», como los recibos del BBVA)."""
        from datetime import date

        from compras import sire as sire_compras
        from core.models import PropuestaSIRE
        c1, c2 = self.compra('501'), self.compra('502')
        PropuestaSIRE.objects.create(libro='RCE', periodo=PERIODO, origen='API', estado='LISTA', registros=sire_compras.a_json([
            {'ruc': self.prov.numero_doc, 'tipo': '01', 'serie': c1.serie, 'numero': '501', 'fecha': c1.fecha_emision,
             'vencimiento': date(2026, 10, 12), 'base': c1.base_imponible, 'igv': c1.igv, 'total': c1.total}]))
        filas = {l.split('|')[9]: l.split('|') for l in sire_envio.generar('080400', PERIODO)['txt'].splitlines()}
        self.assertEqual(filas['501'][5], '12/10/2026')
        self.assertEqual(filas['502'][5], '')  # SUNAT no la tiene: vacía, como antes
