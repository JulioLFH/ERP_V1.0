"""Servidor con varios procesos (servir.py, v1.110): si otro proceso cambia el plan de cuentas, el índice de subcuentas
armado en este proceso se rearma (la versión va en la caché compartida)."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from . import pcge
from .models import CuentaContable


class IndiceEntreProcesosTest(TestCase):
    def setUp(self):
        cache.clear()
        pcge.limpiar_detalle()
        self.general = CuentaContable.objects.create(codigo='9911', nombre='General', imputable=True)
        CuentaContable.objects.create(codigo='991101', nombre='Detalle 1', imputable=True)

    def test_otro_proceso_agrega_una_subcuenta(self):
        self.assertEqual(pcge.al_detalle(self.general).codigo, '991101')
        # otro proceso cambia el plan: en este proceso no corre la señal (update), solo cambia la versión compartida
        CuentaContable.objects.filter(codigo='991101').update(codigo='9911001')
        self.assertEqual(pcge.al_detalle(self.general).codigo, '991101')  # índice viejo de este proceso
        cache.set(pcge._clave_version('default'), 'otro-proceso', None)
        with patch.object(pcge, 'REVISAR_CADA', 0):
            self.assertEqual(pcge.al_detalle(self.general).codigo, '9911001')
