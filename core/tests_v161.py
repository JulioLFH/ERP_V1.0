"""v1.61, checklist v1.56 (CAL-01): certificado de análisis por lote con plantilla por producto y liberación del
lote por Calidad: el lote nuevo entra en cuarentena y no sale en una venta hasta aprobar su certificado."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from inventario.cierre import KardexCerrado

from .models import Almacen, Lote, PerfilUsuario, Producto
from .modulos import GRUPOS

D = Decimal


class CertificadoAnalisisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_v161', 'a@a.pe', 'Clave-segura-9')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def test_lote_en_cuarentena_hasta_aprobar_el_certificado(self):
        from produccion import planta
        from produccion.models import CertificadoAnalisis, InspeccionCalidad, ParametroCalidad
        p = Producto.objects.get(codigo='P004')
        p.control = 'LOTE'
        p.save()
        alm = Almacen.principal()
        ParametroCalidad.objects.create(producto=p, nombre='pH', minimo=D('3'), maximo=D('4'))
        ParametroCalidad.objects.create(producto=p, nombre='Uso interno', especificacion='x', en_certificado=False)
        self.client.force_login(self.admin)
        # plantilla desde el plan de calidad del producto
        self.client.post(reverse('manufactura:plan_calidad', args=[p.pk]), {
            'par-TOTAL_FORMS': '0', 'par-INITIAL_FORMS': '0', 'par-MIN_NUM_FORMS': '0', 'par-MAX_NUM_FORMS': '1000',
            'con_certificado': '1', 'coa-titulo': 'CERTIFICADO DE ANÁLISIS', 'coa-norma': 'FT-01',
            'coa-texto': 'Cumple.', 'coa-responsable': 'Jefe de Calidad', 'coa-requiere_liberacion': 'on'})
        self.assertTrue(hasattr(Producto.objects.get(pk=p.pk), 'plantilla_certificado'))
        p.mover_stock(D('10'), 'PT lote L-100', costo=D('5'), almacen=alm, lotes=[('L-100', D('10'), None)])
        lote = Lote.objects.get(producto=p, codigo='L-100')
        self.assertEqual(lote.estado_calidad, 'CUARENTENA')
        # la venta no lo toma (ni por FEFO ni indicándolo); un traslado o consumo sí
        with self.assertRaises(KardexCerrado):
            p.mover_stock(D('-2'), 'F001-1', almacen=alm, origen='VENTA')
        with self.assertRaises(KardexCerrado):
            p.mover_stock(D('-2'), 'F001-1', almacen=alm, origen='VENTA', lotes=[('L-100', D('2'), None)])
        r = self.client.get(reverse('manufactura:certificados'))
        self.assertContains(r, 'L-100')
        # inspección aprobada -> certificado por aprobar -> un usuario de calidad sin «liberar lotes» no lo aprueba
        insp = planta.crear_inspeccion(InspeccionCalidad(tipo='TERMINADO', producto=p, lote='L-100',
                                                         cantidad=D('10'), almacen=alm), self.admin)
        planta.registrar_resultados(insp, {r.pk: (D('3.5'), '', None) for r in insp.resultados.all()}, 'APROBADO',
                                    '', self.admin)
        self.client.post(reverse('manufactura:certificado_nuevo'), {'lote': lote.pk, 'inspeccion': insp.pk,
                                                                    'fecha': '2026-10-21', 'observaciones': ''})
        cert = CertificadoAnalisis.objects.get(lote=lote)
        self.assertEqual(cert.estado, 'BORRADOR')
        self.assertEqual([r['caracteristica'] for r in cert.resultados], ['pH'])
        calidad = User.objects.create_user('calidad_v161', password='x')
        calidad.groups.add(Group.objects.get_or_create(name=GRUPOS['manufactura'])[0])
        PerfilUsuario.objects.create(usuario=calidad, acciones=['manufactura.calidad'])
        self.client.force_login(calidad)
        r = self.client.post(reverse('manufactura:certificado', args=[cert.pk]), {'accion': 'aprobar'})
        self.assertIn(r.status_code, (403, 404))
        self.client.force_login(self.admin)
        r = self.client.get(reverse('manufactura:certificado', args=[cert.pk]))
        self.assertContains(r, 'FT-01')
        self.client.post(reverse('manufactura:certificado', args=[cert.pk]), {'accion': 'aprobar'})
        lote.refresh_from_db()
        self.assertEqual(lote.estado_calidad, 'LIBERADO')
        p.mover_stock(D('-2'), 'F001-1', almacen=alm, origen='VENTA')
        self.assertEqual(lote.stock, D('8'))
