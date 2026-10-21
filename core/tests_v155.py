"""v1.55, correcciones del piloto (etapa 3, estudio contable): panel del estudio con periodo, centralización, SIRE,
vencimiento SUNAT por último dígito y procesos masivos (53); rol «Cliente del estudio» con su módulo Mi empresa (54);
alta de empresa con datos del padrón y copia de series y parámetros de una empresa modelo (52). Un escenario por
clase."""
import io
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from erp.empresas import activar, restaurar

from .models import DocumentoCliente, Empresa, EmpresaGrupo, PerfilAcceso, Serie
from .modulos import GRUPOS

CLAVE = 'Clave-segura-9'
D = Decimal


class Base(TestCase):
    databases = {'default', 'empresa2'}

    def setUp(self):
        cache.clear()
        Empresa.objects.create(ruc='20100000001', razon_social='HOLDING PRINCIPAL S.A.C.')
        self.en_empresa2(lambda: Empresa.objects.create(ruc='20600000005', razon_social='FILIAL NORTE S.A.C.'))

    def en_empresa2(self, funcion):
        token = activar('empresa2')
        try:
            return funcion()
        finally:
            restaurar(token)

    def ingresar(self, usuario):
        return self.client.post(reverse('login'), {'username': usuario, 'password': CLAVE, 'empresa': 'default'})


class CronogramaTest(TestCase):
    def test_vencimiento_por_ultimo_digito(self):
        from .cronograma_sunat import periodo_anterior, vencimiento
        self.assertEqual(vencimiento('20100000001', '202609'), date(2026, 10, 19))
        self.assertEqual(vencimiento('20600000005', '202609'), date(2026, 10, 21))  # grupo 4-5
        self.assertEqual(vencimiento('20600000000', '202612'), date(2027, 1, 18))
        self.assertEqual(vencimiento('20600000005', '202609', buen_contribuyente=True), date(2026, 10, 26))
        self.assertIsNone(vencimiento('20600000005', '202701'))  # año sin cronograma cargado
        self.assertEqual((periodo_anterior(date(2026, 10, 21)), periodo_anterior(date(2027, 1, 5))),
                         ('202609', '202612'))


class PanelEstudioTest(Base):
    def test_panel_con_datos_contables_y_procesos_masivos(self):
        from contabilidad.models import PeriodoContable

        from .cronograma_sunat import periodo_anterior, vencimiento
        User.objects.create_superuser('contador', 'c@estudio.pe', CLAVE)
        self.ingresar('contador')
        periodo = periodo_anterior(timezone.localdate())
        r = self.client.get(reverse('grupo_panel'))
        self.assertContains(r, f'Periodo {periodo[4:]}/{periodo[:4]}')
        self.assertContains(r, 'SIRE ventas: sin traer')
        if vencimiento('20100000001', periodo):
            self.assertContains(r, f"{vencimiento('20100000001', periodo):%d/%m/%Y}")
        self.assertContains(r, 'Procesos de todas las empresas')
        r = self.client.post(reverse('grupo_panel'), {'accion': 'cerrar', 'periodo': f'{periodo[:4]}-{periodo[4:]}',
                                                      'empresas': ['default', 'empresa2']}, follow=True)
        self.assertContains(r, 'FILIAL NORTE S.A.C.:')
        self.assertTrue(PeriodoContable.objects.filter(periodo=periodo, cerrado=True).exists())
        self.assertTrue(self.en_empresa2(lambda: PeriodoContable.objects.filter(periodo=periodo,
                                                                               cerrado=True).exists()))
        r = self.client.post(reverse('grupo_panel'), {'accion': 'ple', 'periodo': periodo,
                                                      'empresas': ['default', 'empresa2']})
        self.assertEqual(r['Content-Type'], 'application/zip')
        nombres = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        self.assertEqual(len(nombres), 4)  # ventas y compras de cada empresa
        self.assertTrue(any(n.startswith('20600000005') for n in nombres))


class ClienteDelEstudioTest(Base):
    def test_cliente_sube_documentos_ve_sus_reportes_y_nada_mas(self):
        self.assertTrue(PerfilAcceso.objects.filter(nombre='Cliente del estudio', modulos=['cliente']).exists())
        cliente = User.objects.create_user('cliente.holding', 'c@holding.pe', CLAVE)
        cliente.groups.add(Group.objects.get_or_create(name=GRUPOS['cliente'])[0])
        self.ingresar('cliente.holding')
        self.assertEqual(self.client.get(reverse('cliente_subir')).status_code, 200)
        r = self.client.post(reverse('cliente_subir'), {
            'tipo': 'COMPRA', 'periodo': '2026-09', 'descripcion': 'Compras de setiembre',
            'archivos': [SimpleUploadedFile('f001-10.pdf', b'%PDF-1.4 uno', content_type='application/pdf'),
                         SimpleUploadedFile('f001-11.pdf', b'%PDF-1.4 dos', content_type='application/pdf')]},
            follow=True)
        self.assertContains(r, '2 documento(s) enviados a su contador')
        self.assertEqual(DocumentoCliente.objects.filter(periodo='202609', estado='RECIBIDO').count(), 2)
        d = DocumentoCliente.objects.first()
        self.assertEqual(self.client.get(reverse('cliente_archivo', args=[d.pk])).content[:8], b'%PDF-1.4')
        self.assertEqual(self.client.get(reverse('cliente_reportes')).status_code, 200)
        self.assertEqual(self.client.get(reverse('ventas:lista')).status_code, 403)  # no registra operaciones
        self.assertNotContains(self.client.get(reverse('home')), 'Ir a FILIAL NORTE')  # no ve otras empresas
        self.client.post(reverse('cliente_documentos'), {'id': d.pk, 'estado': 'PROCESADO'})  # no es contador
        d.refresh_from_db()
        self.assertEqual(d.estado, 'RECIBIDO')
        # el contador lo marca procesado desde Contabilidad
        self.client.logout()
        User.objects.create_superuser('contador', 'c@estudio.pe', CLAVE)
        self.ingresar('contador')
        self.client.post(reverse('cliente_documentos'), {'id': d.pk, 'estado': 'OBSERVADO',
                                                         'comentario': 'Falta el XML'})
        d.refresh_from_db()
        self.assertEqual((d.estado, d.comentario), ('OBSERVADO', 'Falta el XML'))


class EmpresaModeloTest(Base):
    def test_alta_con_padron_y_copia_de_series_y_parametros(self):
        from . import grupo
        Serie.objects.create(tipo='01', serie='F001', correlativo=120)
        Serie.objects.create(tipo='03', serie='B001', correlativo=55)
        Empresa.objects.update(tolerancia_precio=D('0.50'), alerta_precio_pct=D('7'), clave_largo_minimo=12)
        grupo.copiar_configuracion('default', 'empresa2')
        series = self.en_empresa2(lambda: list(Serie.objects.order_by('serie').values_list('serie', 'correlativo')))
        self.assertIn(('F001', 0), series)
        self.assertIn(('B001', 0), series)
        filial = self.en_empresa2(lambda: Empresa.objects.first())
        self.assertEqual((filial.tolerancia_precio, filial.alerta_precio_pct, filial.clave_largo_minimo,
                          filial.ruc), (D('0.5000'), D('7.00'), 12, '20600000005'))  # su RUC no cambia
        admin = User.objects.create_superuser('dueno', 'd@holding.pe', CLAVE)
        with patch('core.grupo.threading.Thread'):
            r = grupo.crear_empresa('Nueva Sur S.A.C.', '20100066603', admin, direccion='Av. Sol 100 Lima',
                                    ubigeo='150101', modelo='default')
        self.assertEqual(EmpresaGrupo.objects.get(pk=r.pk).datos,
                         {'direccion': 'Av. Sol 100 Lima', 'ubigeo': '150101', 'modelo': 'default'})
        self.ingresar('dueno')
        r = self.client.get(reverse('grupo_panel'))
        self.assertContains(r, 'Copiar series y parámetros de')
        self.assertContains(r, 'consulta_ruc.js')
