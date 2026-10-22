"""v1.69, checklist v1.66.1 21:35: grupos de compras y compradores (COM-09) y su desempeño (COM-10). La línea
liberada va sola al comprador del grupo; se reasigna con historial; la OC lleva el comprador; el tablero mide plazo,
atraso y monto por comprador."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from . import compradores
from . import solped as srv
from .models import AsignacionCompra, GrupoCompras, LineaSolicitud
from .tests_solped import Base

D = Decimal
HOY = timezone.localdate()


class CompradoresTest(Base):
    def test_asignacion_reasignacion_oc_y_tablero(self):
        ana = User.objects.create_user('ana_compras', password='x', first_name='Ana')
        luis = User.objects.create_user('luis_compras', password='x', first_name='Luis')
        repuestos = GrupoCompras.objects.create(codigo='REP', nombre='Repuestos', comprador=ana, jefe=self.admin,
                                                tipos=['SUMINISTRO'], plazo_dias=3, plazo_urgente=1)
        self.repuesto.familia = 'Rodamientos'
        self.repuesto.save()
        GrupoCompras.objects.create(codigo='GEN', nombre='General', comprador=luis, prioridad=20)
        sp = self.solicitud('10', '25')
        srv.liberar(sp, self.jefe)  # al liberarse, la línea va al comprador del grupo
        linea = sp.lineas.get()
        self.assertEqual((linea.comprador, linea.grupo), (ana, repuestos))
        self.assertTrue(AsignacionCompra.objects.filter(linea=linea, comprador=ana).exists())
        # vencida: liberada hace dos semanas con plazo de 3 días hábiles
        LineaSolicitud.objects.filter(pk=linea.pk).update(asignada_en=timezone.now() - timedelta(days=14))
        self.assertEqual([l.pk for l in compradores.vencidas(usuario=ana)], [linea.pk])
        from core.alertas import calcular
        self.assertIn('Líneas vencidas de sus compradores', [a['titulo'] for a in calcular(self.admin)])
        # bandeja: «Mis líneas» de Ana; reasignar a Luis con motivo e historial
        self.client.force_login(self.admin)
        r = self.client.get(reverse('compras:solped_bandeja') + f'?comprador={ana.pk}')
        self.assertContains(r, 'Vencida')
        self.client.post(reverse('compras:solped_bandeja'), {'linea': [linea.pk], 'convertir': 'reasignar',
                                                             'nuevo_comprador': luis.pk,
                                                             'motivo_reasignacion': 'Ana de vacaciones'})
        linea.refresh_from_db()
        self.assertEqual(linea.comprador, luis)
        self.assertEqual(linea.asignaciones.count(), 2)
        # la OC creada desde la bandeja lleva al comprador que la convierte
        self.client.force_login(luis)
        oc = srv.a_orden_compra([linea.pk], luis)[0]
        self.assertEqual(oc.comprador, luis)
        # tablero del mes por comprador
        filas = {f['comprador']: f for f in compradores.desempeno(HOY.replace(day=1), HOY)}
        self.assertEqual((filas[luis]['asignadas'], filas[luis]['atendidas'], filas[luis]['ocs']), (1, 1, 1))
        self.assertEqual(filas[luis]['sla'], D('100.00'))
        self.client.force_login(self.admin)
        r = self.client.get(reverse('compras:desempeno'))
        self.assertContains(r, 'Luis')
        self.assertEqual(self.client.get(reverse('compras:desempeno') + '?formato=excel').status_code, 200)
        self.assertContains(self.client.get(reverse('compras:grupos')), 'Repuestos')

    def test_bandeja_linea_sin_proveedor(self):
        """COM-11: una línea liberada sin proveedor sugerido ni proveedor del producto no rompe la bandeja."""
        self.repuesto.proveedor = None
        self.repuesto.save()
        sp = self.solicitud('4', '25')
        srv.liberar(sp, self.jefe)
        self.assertIsNone(sp.lineas.get().proveedor_sugerido)
        self.client.force_login(self.admin)
        for agrupar in ('proveedor', 'producto', 'fecha', 'comprador'):
            r = self.client.get(reverse('compras:solped_bandeja') + f'?comprador=todas&agrupar={agrupar}')
            self.assertContains(r, 'Rodamiento 6205')
        self.assertContains(r, 'Sin comprador')

    def test_dias_habiles(self):
        lunes = HOY - timedelta(days=HOY.weekday())
        self.assertEqual(compradores.dias_habiles(lunes, lunes + timedelta(days=7)), 5)
