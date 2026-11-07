"""v1.106 (verificación del 06/11/2026 sobre la v1.103.0): ciclo de venta en la fecha de Lima y etapa inicial de la
oportunidad automática (VEN-03), WhatsApp del CRM solo por POST (SEG-03) y secretos cifrados en la base (SEG-04).
COS-01 está en core/tests_v188.py (reparto con otra orden del mismo día)."""
from datetime import date, datetime, timezone as tz
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.urls import reverse

from core.cifrado import PREFIJO, cifrar_guardados
from core.models import CorreoConfig, Tercero
from ventas.models import ActividadCRM, CambioEtapa, ConfigCRM, Cotizacion, Oportunidad

from .tests_modulo_tesoreria import Base


def _crudo(tabla, campo, pk):
    with connection.cursor() as c:
        c.execute(f'SELECT {campo} FROM {tabla} WHERE id = %s', [pk])
        return c.fetchone()[0]


class CicloDeVentaTest(Base):
    def setUp(self):
        super().setUp()
        self.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970', nombre='UNICON')

    def test_ciclo_en_la_fecha_de_lima(self):
        """Creada el 24/10 a las 20:21 en Lima (25/10 01:21 UTC) y ganada el 24/10: ciclo 0, no −1."""
        from ventas.crm_reportes import ciclo_dias
        o = Oportunidad.objects.create(nombre='Venta nocturna', tercero=self.cliente, etapa='GANADA',
                                       cerrada_en=date(2026, 10, 24))
        Oportunidad.objects.filter(pk=o.pk).update(creado=datetime(2026, 10, 25, 1, 21, tzinfo=tz.utc))
        o.refresh_from_db()
        self.assertEqual(ciclo_dias(o), 0)

    def test_oportunidad_automatica_deja_su_etapa_inicial(self):
        from ventas import crm_servicios as srv
        cot = Cotizacion.objects.create(tipo='COT', numero='CT01-00000090', tercero=self.cliente, total=D('5000'))
        with patch('ventas.crm_servicios.parametro', return_value=D('1000')):
            o = srv.asegurar_oportunidad(cot)
        self.assertEqual(list(CambioEtapa.objects.filter(oportunidad=o).values_list('de', 'a')), [('', 'PROPUESTA')])


class WhatsappPorPostTest(Base):
    def test_get_no_registra_y_post_si(self):
        cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970', nombre='UNICON')
        o = Oportunidad.objects.create(nombre='Mesas', tercero=cliente, telefono='987654321')
        self.client.force_login(self.usuario('vende', ['crm'], acciones=['crm.registrar']))
        r = self.client.get(reverse('crm:whatsapp', args=[o.pk]))  # un robot o una vista previa del enlace
        self.assertRedirects(r, reverse('crm:oportunidad', args=[o.pk]), fetch_redirect_response=False)
        self.assertFalse(ActividadCRM.objects.exists())
        r = self.client.post(reverse('crm:whatsapp', args=[o.pk]), {'numero': '987654321'})
        self.assertTrue(r['Location'].startswith('https://wa.me/51987654321'))
        self.assertEqual(ActividadCRM.objects.get().descripcion, 'WhatsApp a 51987654321')
        pagina = self.client.get(reverse('crm:oportunidad', args=[o.pk])).content.decode()
        self.assertIn(f'method="post" action="{reverse("crm:whatsapp", args=[o.pk])}"', pagina)


class DepuracionDeDatosTest(Base):
    def test_revisar_datos_corrige_lo_que_no_necesita_decision(self):
        """MAN-05 y SEG-02 (verificación del 06/11): revisar_datos --corregir lleva a 0 la tarifa negativa y quita el
        HTML del nombre del almacén, con registro en la auditoría; sin --corregir no cambia nada."""
        import io

        from django.core.management import call_command

        from core.models import Almacen, Bitacora
        from produccion.models import CentroTrabajo
        c = CentroTrabajo.objects.create(codigo='BAT-M29', nombre='Prensa', costo_hora_mo=D('5'))
        CentroTrabajo.objects.filter(pk=c.pk).update(costo_hora_mo=D('-10'))
        a = Almacen.objects.create(codigo='OB9', nombre='Obra <script>alert(1)</script>')
        call_command('revisar_datos', stdout=io.StringIO())
        c.refresh_from_db()
        self.assertEqual(c.costo_hora_mo, D('-10'))
        salida = io.StringIO()
        call_command('revisar_datos', corregir=True, stdout=salida)
        c.refresh_from_db()
        a.refresh_from_db()
        self.assertEqual((c.costo_hora_mo, a.nombre), (D('0'), 'Obra alert(1)'))
        self.assertIn('2 corrección(es)', salida.getvalue())
        self.assertTrue(Bitacora.objects.filter(motivo__startswith='SEG-02').exists())


class SecretosCifradosTest(Base):
    def test_se_guarda_cifrado_y_se_lee_en_claro(self):
        cfg = ConfigCRM.actual()
        cfg.correo_clave = 'Clave-IMAP-9'
        cfg.save()
        crudo = _crudo('ventas_configcrm', 'correo_clave', cfg.pk)
        self.assertTrue(crudo.startswith(PREFIJO))
        self.assertNotIn('Clave-IMAP-9', crudo)
        self.assertEqual(ConfigCRM.objects.get(pk=cfg.pk).correo_clave, 'Clave-IMAP-9')
        self.assertEqual(ConfigCRM.objects.values_list('correo_clave', flat=True).get(pk=cfg.pk), 'Clave-IMAP-9')
        ConfigCRM.objects.filter(pk=cfg.pk).update(correo_clave='Otra-clave-1')  # update() también cifra
        self.assertTrue(_crudo('ventas_configcrm', 'correo_clave', cfg.pk).startswith(PREFIJO))

    def test_lo_antiguo_en_claro_se_lee_y_la_migracion_lo_cifra(self):
        smtp = CorreoConfig.objects.create(servidor='smtp.gmail.com', usuario='ventas@empresa.pe')
        with connection.cursor() as c:  # como quedó antes de la v1.106
            c.execute('UPDATE core_correoconfig SET clave = %s WHERE id = %s', ['clave-vieja', smtp.pk])
        self.assertEqual(CorreoConfig.objects.get(pk=smtp.pk).clave, 'clave-vieja')
        cifrar_guardados('core', 'CorreoConfig', ['clave'])(apps, SimpleNamespace(connection=connection))
        crudo = _crudo('core_correoconfig', 'clave', smtp.pk)
        self.assertTrue(crudo.startswith(PREFIJO))
        self.assertEqual(CorreoConfig.objects.get(pk=smtp.pk).clave, 'clave-vieja')

    def test_formulario_y_respaldo(self):
        from django.core import serializers
        from agentes.forms import ConfigForm
        from agentes.models import ConfigAgentes
        form = ConfigForm({'activo': 'on', 'api_key': 'sk-ant-prueba', 'modelo': 'claude-opus-5-5', 'esfuerzo': 'low',
                           'tope_mensual_usd': '10', 'max_herramientas': '5'}, instance=ConfigAgentes.actual())
        self.assertTrue(form.is_valid(), form.errors)
        cfg = form.save()
        self.assertTrue(_crudo('agentes_configagentes', 'api_key', cfg.pk).startswith(PREFIJO))
        texto = serializers.serialize('json', [ConfigAgentes.objects.get(pk=cfg.pk)])
        self.assertNotIn('sk-ant-prueba', texto)  # la copia de respaldo lo lleva cifrado
        restaurado = next(serializers.deserialize('json', texto)).object
        self.assertEqual(restaurado.api_key, 'sk-ant-prueba')
