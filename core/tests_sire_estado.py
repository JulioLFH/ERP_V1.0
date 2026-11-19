"""v1.116.9: estado del ticket del SIRE. Los envíos (reemplazo de la propuesta, 8.5, ajustes) terminan en «Procesado»
o «Procesado con Errores», no en «Terminado» como la propuesta: «Procesado» quedaba para siempre en «SUNAT lo procesa»."""
from unittest.mock import patch

from django.test import SimpleTestCase

from core import sire_api


def _estado(codigo, descripcion):
    resp = {'registros': [{'numTicket': '20260100000022', 'codEstadoProceso': codigo,
                           'desEstadoProceso': descripcion}]}
    with patch('core.sire_api._api', return_value=resp):
        e = sire_api.estado_ticket('RCE', '202610', '20260100000022')
    return e['terminado'], e['error']


class EstadoTicketTest(SimpleTestCase):
    def test_procesado_es_terminado_sin_error(self):
        self.assertEqual(_estado('07', 'Procesado'), (True, False))

    def test_procesado_con_errores_es_terminado_con_error(self):
        self.assertEqual(_estado('08', 'Procesado con Errores'), (True, True))

    def test_terminado_por_codigo_o_descripcion(self):
        self.assertEqual(_estado('06', 'Terminado'), (True, False))
        self.assertEqual(_estado('', 'Terminado'), (True, False))

    def test_en_proceso_sigue_pendiente(self):
        self.assertEqual(_estado('02', 'En proceso'), (False, False))
        self.assertEqual(_estado('01', 'Pendiente'), (False, False))
