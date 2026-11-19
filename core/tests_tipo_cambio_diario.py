"""v1.116.8: carga diaria del tipo de cambio (servir.py la lanza cada hora).

La API gratuita (apis.net.pe) responde 429 si se le consulta más de una vez cada ~15 segundos: la carga espacia las
consultas, reintenta una vez tras un 429, consulta cada día una sola vez para todas las empresas y, con el token de
Decolecta, trae el mes completo en una consulta."""
import urllib.error
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core import tipo_cambio
from core.models import Empresa, TipoCambio


def _http(codigo):
    return urllib.error.HTTPError('https://api', codigo, 'x', {}, None)


class CargaDiariaTest(TestCase):
    def setUp(self):
        if not Empresa.objects.exists():
            Empresa.objects.create(ruc='20100000001', razon_social='Prueba SAC')
        Empresa.objects.update(fuente_tipo_cambio='SUNAT', token_tipo_cambio='')
        TipoCambio.objects.all().delete()
        self.hoy = timezone.localdate()
        self.desde = min(self.hoy.replace(day=1), self.hoy - timedelta(days=7))
        self.dias = (self.hoy - self.desde).days + 1
        self.pausas = []

    def cargar(self, memo=None):
        return tipo_cambio.cargar_pendientes(memo, pausa=20, dormir=self.pausas.append)

    def test_completa_los_dias_que_faltan_con_pausa_entre_consultas(self):
        TipoCambio.objects.create(fecha=self.hoy - timedelta(days=1), compra=D('3.4'), venta=D('3.41'),
                                  fuente='MANUAL')
        with patch('core.tipo_cambio._consultar', return_value=(D('3.408'), D('3.415'), 'SUNAT')) as consulta:
            r = self.cargar()
        self.assertEqual((r['ok'], r['faltan'], r['error']), (self.dias - 1, self.dias - 1, ''))
        self.assertEqual(consulta.call_count, self.dias - 1)
        self.assertEqual(self.pausas, [20] * (self.dias - 2))  # entre consultas, no antes de la primera
        self.assertEqual(TipoCambio.objects.get(fecha=self.hoy).venta, D('3.415'))
        self.assertEqual(TipoCambio.objects.get(fecha=self.hoy - timedelta(days=1)).fuente, 'MANUAL')  # se respeta
        with patch('core.tipo_cambio._consultar') as consulta:  # la hora siguiente: nada que hacer
            self.assertEqual(self.cargar()['faltan'], 0)
        consulta.assert_not_called()

    def test_el_mismo_dia_se_consulta_una_vez_para_todas_las_empresas(self):
        memo = {}
        with patch('core.tipo_cambio._consultar', return_value=(D('3.408'), D('3.415'), 'SUNAT')) as consulta:
            self.cargar(memo)
            TipoCambio.objects.all().delete()  # «otra empresa» con los mismos días sin cargar
            r = self.cargar(memo)
        self.assertEqual(r['ok'], self.dias)
        self.assertEqual(consulta.call_count, self.dias)

    def test_429_espera_reintenta_y_deja_el_resto_para_la_proxima_hora(self):
        respuestas = [(D('3.4'), D('3.41'), 'SUNAT'), _http(429), (D('3.5'), D('3.51'), 'SUNAT'),
                      _http(429), _http(429)]
        with patch('core.tipo_cambio._consultar', side_effect=respuestas) as consulta:
            r = self.cargar()
        self.assertEqual(r['ok'], 2)
        self.assertIn('429', r['error'])
        self.assertEqual(consulta.call_count, 5)
        self.assertEqual(self.pausas, [20, 60, 20, 60])  # tras el 429 espera más y reintenta una vez
        self.assertEqual(TipoCambio.objects.count(), 2)

    def test_dia_sin_publicar_no_se_guarda_ni_detiene_la_carga(self):
        def consultar(fecha):
            if fecha == self.hoy:
                raise _http(404)  # hoy temprano, aún sin publicar
            return D('3.4'), D('3.41'), 'SUNAT'
        with patch('core.tipo_cambio._consultar', side_effect=consultar):
            r = self.cargar()
        self.assertEqual((r['ok'], r['error']), (self.dias - 1, ''))
        self.assertFalse(TipoCambio.objects.filter(fecha=self.hoy).exists())

    def test_con_token_trae_el_mes_en_una_consulta(self):
        Empresa.objects.update(token_tipo_cambio='tok')
        mes = {self.desde + timedelta(days=i): (D('3.4'), D('3.41')) for i in range(self.dias)}
        with patch('core.tipo_cambio._mes_decolecta', return_value=mes) as del_mes, \
                patch('core.tipo_cambio._consultar') as consulta:
            r = self.cargar()
        self.assertEqual(r['ok'], self.dias)
        consulta.assert_not_called()
        self.assertEqual(del_mes.call_count, len({(f.year, f.month) for f in mes}))
        self.assertEqual(TipoCambio.objects.get(fecha=self.hoy).fuente, 'SUNAT')

    def test_sunat_con_token_usa_decolecta_y_si_falla_la_api_gratuita(self):
        Empresa.objects.update(token_tipo_cambio='tok')
        hoy = self.hoy.isoformat()
        with patch('core.tipo_cambio._get_json',
                   return_value={'buy_price': '3.408', 'sell_price': '3.415', 'date': hoy}) as get:
            self.assertEqual(tipo_cambio._consultar(self.hoy), (D('3.408'), D('3.415'), 'SUNAT'))
        self.assertIn('decolecta', get.call_args[0][0])
        self.assertEqual(get.call_args[0][1], {'Authorization': 'Bearer tok'})
        with patch('core.tipo_cambio._get_json', side_effect=[_http(401), {'compra': 3.4, 'venta': 3.41}]) as get:
            self.assertEqual(tipo_cambio._consultar(self.hoy), (D('3.4'), D('3.41'), 'SUNAT'))
        self.assertIn('apis.net.pe', get.call_args[0][0])

    def test_mes_de_decolecta_lee_la_lista(self):
        filas = [{'buy_price': '3.4', 'sell_price': '3.41', 'date': '2026-11-02'}, {'date': 'mal'}]
        with patch('core.tipo_cambio._get_json', return_value=filas):
            valores = tipo_cambio._mes_decolecta(2026, 11, 'tok')
        self.assertEqual(list(valores.values()), [(D('3.4'), D('3.41'))])

    def test_comando_diario(self):
        salida = StringIO()
        with patch('core.tipo_cambio.PAUSA', 0), \
                patch('core.tipo_cambio._consultar', return_value=(D('3.4'), D('3.41'), 'SUNAT')):
            call_command('cargar_tipo_cambio', '--diario', stdout=salida, stderr=StringIO())
        self.assertIn(f'{self.dias} de {self.dias} día(s) cargados', salida.getvalue())
        salida = StringIO()
        call_command('cargar_tipo_cambio', '--diario', stdout=salida)  # sin nada que hacer: no escribe
        self.assertEqual(salida.getvalue(), '')
