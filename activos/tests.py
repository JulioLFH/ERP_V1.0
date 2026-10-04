"""Pruebas de activos fijos (v1.12): depreciación lineal, proceso mensual en orden, bajas con sustento,
registro desde la compra, contabilidad (68/39, 655, reclasificación), formato 7.1 y cuadre."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra, CompraItem
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import Asiento
from core.models import Producto, Tercero
from core.sustentos import tiene

from . import servicios
from .models import ActivoFijo, CategoriaActivo, Depreciacion

D = Decimal
HOY = timezone.localdate()
INICIO_MES = HOY.replace(day=1)


def pdf(nombre='acta.pdf'):
    return SimpleUploadedFile(nombre, b'%PDF-1.4 sustento', content_type='application/pdf')


def mes_anterior(fecha, n=1):
    for _ in range(n):
        fecha = (fecha.replace(day=1) - date.resolution).replace(day=1)
    return fecha


class ActivosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.client.force_login(self.user)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.cpu = CategoriaActivo.objects.get(codigo='CPU')

    def activo(self, valor='12000', vida=48, uso=None, **kw):
        uso = uso or INICIO_MES
        kw.setdefault('origen', 'OTRO')
        return ActivoFijo.objects.create(nombre='Servidor', categoria=kw.pop('categoria', self.cpu),
                                         fecha_adquisicion=uso, fecha_uso=uso, valor=D(valor),
                                         vida_util_meses=vida, **kw)

    # ---------------------------------------------------------------- cálculo
    def test_categorias_con_tasas_sunat(self):
        self.assertEqual(CategoriaActivo.objects.get(codigo='VEH').tasa_anual, 20)
        self.assertFalse(CategoriaActivo.objects.get(codigo='TER').deprecia)
        self.assertEqual(CategoriaActivo.objects.get(codigo='SOF').cuenta_depreciacion.codigo, '3921')

    def test_lineal_y_el_ultimo_mes_cierra_el_redondeo(self):
        a = self.activo('1000', vida=3, uso=date(2025, 1, 20))
        filas = servicios.cuotas(a, '202512')
        self.assertEqual([f[1] for f in filas], [D('333.33'), D('333.33'), D('333.34')])
        self.assertEqual([f[0] for f in filas], ['202501', '202502', '202503'])

    def test_saldo_inicial_continua_con_lo_que_falta(self):
        a = self.activo('12000', vida=48, uso=date(2023, 1, 1), origen='SALDO_INICIAL', dep_inicial=D('6000'),
                        dep_inicial_hasta=date(2024, 12, 31))
        filas = servicios.cuotas(a, '202502')
        self.assertEqual([(f[0], f[1]) for f in filas], [('202501', D('250')), ('202502', D('250'))])
        self.assertEqual(filas[-1][2], D('6500'))

    def test_terreno_no_se_deprecia(self):
        a = self.activo('50000', vida=0, categoria=CategoriaActivo.objects.get(codigo='TER'))
        self.assertEqual(servicios.cuotas(a, '209912'), [])

    # ---------------------------------------------------------------- proceso mensual
    def test_proceso_en_orden_con_meses_pendientes_y_reversion(self):
        hace2 = mes_anterior(HOY, 2)
        a = self.activo('4800', vida=48, uso=hace2)
        primero = servicios.periodo_de(mes_anterior(HOY))
        p = servicios.depreciar(primero, self.user)  # primer cálculo: mes libre, acumula el mes anterior
        d = Depreciacion.objects.get(activo=a)
        self.assertEqual((d.meses, d.cuota, p.total), (2, D('200'), D('200')))
        with self.assertRaises(servicios.ErrorActivo):
            servicios.depreciar(primero, self.user)  # ya calculado
        actual = servicios.periodo_de(HOY)
        self.assertEqual(servicios.periodo_siguiente(), actual)
        p2 = servicios.depreciar(actual, self.user)
        self.assertEqual(p2.total, D('100'))
        with self.assertRaises(servicios.ErrorActivo):
            servicios.revertir(p, self.user, 'No es el último mes calculado')
        with self.assertRaises(servicios.ErrorActivo):
            servicios.revertir(p2, self.user, 'corto')
        servicios.revertir(p2, self.user, 'Faltaba registrar activos del mes')
        self.assertEqual(a.depreciaciones.count(), 1)
        with self.assertRaises(servicios.ErrorActivo):
            servicios.depreciar(servicios.periodo_de(date(HOY.year + 1, 1, 1)), self.user)

    # ---------------------------------------------------------------- baja y anulación
    def test_baja_exige_sustento_y_contabiliza(self):
        a = self.activo('4800', vida=48, uso=mes_anterior(HOY))
        servicios.depreciar(servicios.periodo_de(mes_anterior(HOY)), self.user)
        with self.assertRaises(servicios.ErrorActivo):
            servicios.dar_de_baja(a, self.user, HOY, 'SINIESTRO', 'Robo denunciado en comisaría', None)
        servicios.dar_de_baja(a, self.user, HOY, 'SINIESTRO', 'Robo denunciado en comisaría', pdf('denuncia.pdf'))
        a.refresh_from_db()
        self.assertEqual(a.estado, 'BAJA')
        self.assertTrue(tiene(a))
        self.assertEqual(a.depreciacion_registrada, D('200'))  # mes anterior + mes de la baja
        periodo = servicios.periodo_de(HOY)
        centralizar_periodo(periodo)
        asiento = Asiento.objects.get(periodo=periodo, origen='ACTIVOS')
        self.assertEqual(asiento.lineas.get(cuenta__codigo='6551', es_destino=False).debe, D('4600'))
        self.assertEqual(asiento.lineas.get(cuenta__codigo='3361').haber, D('4800'))
        self.assertEqual(sum(l.debe for l in asiento.lineas.filter(cuenta__codigo='3913')), D('200'))
        for x in Asiento.objects.all():
            self.assertTrue(x.cuadrado, f'{x} no cuadra')
        # el mes de la baja ya no se vuelve a depreciar
        servicios.depreciar(periodo, self.user)
        self.assertEqual(a.depreciaciones.count(), 2)

    def test_anular_solo_sin_depreciacion(self):
        a = self.activo(uso=mes_anterior(HOY))
        servicios.depreciar(servicios.periodo_de(mes_anterior(HOY)), self.user)
        with self.assertRaises(servicios.ErrorActivo):
            servicios.anular(a, self.user, 'Registrado por error de digitación')
        b = self.activo()
        servicios.anular(b, self.user, 'Registrado por error de digitación')
        self.assertEqual(ActivoFijo.objects.get(pk=b.pk).estado, 'ANULADO')

    # ---------------------------------------------------------------- compras
    def compra_activo(self, cantidad='2'):
        prov = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS']).first()
        laptop = Producto.objects.create(nombre='Laptop para oficina', clase='ACTIVO')
        compra = Compra.objects.create(tercero=prov, serie='F001', numero='777', fecha_emision=HOY,
                                       clasificacion='ACTIVO_FIJO', ingresar_almacen=False)
        item = CompraItem.objects.create(documento=compra, producto=laptop, descripcion=laptop.nombre,
                                         cantidad=D(cantidad), precio_unitario=D('3000'))
        compra.calcular_totales()
        compra.save()
        return compra, item

    def test_registro_desde_la_compra_con_reclasificacion(self):
        compra, item = self.compra_activo('2')
        [(fila, pendientes, valor)] = servicios.items_compra(compra)
        self.assertEqual((pendientes, valor), (2, D('3000.00')))
        url = reverse('activos:nuevo') + f'?compra={compra.pk}&item={item.pk}'
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {
            'nombre': 'Laptop para oficina', 'categoria': self.cpu.pk, 'origen': 'COMPRA',
            'fecha_adquisicion': HOY.isoformat(), 'fecha_uso': HOY.isoformat(), 'valor': '3000',
            'valor_residual': '0', 'vida_util_meses': '48', 'dep_inicial': '0', 'unidades': '2'})
        activos = ActivoFijo.objects.filter(compra=compra)
        self.assertEqual(activos.count(), 2, r.content[:3000] if r.status_code == 200 else '')
        self.assertEqual(servicios.items_compra(compra), [])
        a = activos.first()
        self.assertEqual(a.cuenta_origen.codigo, '3369')  # cuenta de compra de los productos "activo"
        periodo = servicios.periodo_de(HOY)
        centralizar_periodo(periodo)
        asiento = Asiento.objects.get(periodo=periodo, origen='ACTIVOS')
        self.assertEqual(sum(l.debe for l in asiento.lineas.filter(cuenta__codigo='3361')), D('6000'))
        self.assertEqual(sum(l.haber for l in asiento.lineas.filter(cuenta__codigo='3369')), D('6000'))
        # la compra no se anula mientras tenga activos
        r = self.client.post(reverse('compras:anular', args=[compra.pk]), {'motivo': 'Error en la factura'})
        compra.refresh_from_db()
        self.assertEqual(compra.estado, 'REGISTRADO')

    def test_alta_sin_compra_exige_sustento(self):
        datos = {'nombre': 'Camioneta', 'categoria': CategoriaActivo.objects.get(codigo='VEH').pk,
                 'origen': 'SALDO_INICIAL', 'fecha_adquisicion': '2022-03-10', 'fecha_uso': '2022-03-15',
                 'valor': '90000', 'valor_residual': '0', 'vida_util_meses': '60', 'dep_inicial': '40000',
                 'dep_inicial_hasta': '2024-12-31'}
        r = self.client.post(reverse('activos:nuevo'), datos)
        self.assertFalse(ActivoFijo.objects.filter(nombre='Camioneta').exists())
        self.assertContains(r, 'Adjunte el documento')
        r = self.client.post(reverse('activos:nuevo'), dict(datos, sustento=pdf('tarjeta.pdf')))
        a = ActivoFijo.objects.get(nombre='Camioneta')
        self.assertRedirects(r, reverse('activos:detalle', args=[a.pk]))
        self.assertTrue(a.codigo.startswith('AF'))
        self.assertTrue(tiene(a))

    # ---------------------------------------------------------------- reportes y pantallas
    def test_registro_71_y_pantallas(self):
        a = self.activo('12000', vida=48, uso=date(HOY.year - 1, 1, 1), origen='SALDO_INICIAL',
                        dep_inicial=D('3000'), dep_inicial_hasta=date(HOY.year - 1, 12, 31))
        servicios.depreciar(servicios.periodo_de(HOY), self.user)
        filas, totales = servicios.registro(HOY.year)
        f = next(x for x in filas if x['a'] == a)
        self.assertEqual((f['saldo_inicial'], f['dep_anterior']), (D('12000'), D('3000')))
        self.assertEqual(f['dep_ejercicio'], a.depreciacion_registrada - D('3000'))
        for url in [reverse('activos:lista'), reverse('activos:nuevo'), reverse('activos:detalle', args=[a.pk]),
                    reverse('activos:editar', args=[a.pk]), reverse('activos:depreciacion'),
                    reverse('activos:proceso', args=[a.depreciaciones.first().proceso_id]),
                    reverse('activos:registro'), reverse('activos:cuadre'), reverse('activos:categorias'),
                    reverse('activos:registro') + '?formato=excel', reverse('activos:lista') + '?formato=excel']:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        # con depreciación registrada el valor ya no se modifica
        self.assertTrue(a.bloqueado)
