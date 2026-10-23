"""Obras, fase 1: APU, presupuesto, explosión de insumos, versiones e importación desde Excel (S10 y plantilla)."""
import io
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from contabilidad.models import CentroCosto

from . import calculos
from . import importar as imp
from .models import Obra, Partida, PartidaBase, PrecioPresupuesto, Presupuesto, Recurso, RecursoAPU


def excel(hojas):
    from openpyxl import Workbook
    libro = Workbook()
    libro.remove(libro.active)
    for nombre, filas in hojas:
        h = libro.create_sheet(nombre)
        for f in filas:
            h.append(f)
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile('presupuesto.xlsx', salida.getvalue())


class ObrasTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.obra = Obra.objects.create(nombre='Edificio Los Pinos', gastos_generales_pct=D('10'),
                                       utilidad_pct=D('10'))
        R = lambda codigo, nombre, unidad, tipo, precio: Recurso.objects.create(
            codigo=codigo, nombre=nombre, unidad=unidad, tipo=tipo, precio=D(precio))
        cls.operario = R('0147010002', 'OPERARIO', 'hh', 'MO', '26.10')
        cls.peon = R('0147010004', 'PEON', 'hh', 'MO', '19.85')
        cls.cemento = R('0221000001', 'CEMENTO PORTLAND TIPO I', 'bls', 'MAT', '28.50')
        cls.hormigon = R('0238000000', 'HORMIGON', 'm3', 'MAT', '65.00')
        cls.herramientas = R('0337010001', 'HERRAMIENTAS MANUALES', '%mo', 'EQ', '0')
        cls.mezcladora = R('0349100007', 'MEZCLADORA 9-11 p3', 'hm', 'EQ', '15.00')

    def setUp(self):
        self.client.force_login(self.user)

    def presupuesto(self):
        p = Presupuesto.objects.create(obra=self.obra, nombre='Oferta')
        Partida.objects.create(presupuesto=p, item='02', descripcion='CONCRETO SIMPLE', es_titulo=True)
        x = Partida.objects.create(presupuesto=p, item='02.01', descripcion="CONCRETO f'c=210", unidad='m3',
                                   metrado=D('35.5'), rendimiento=D('25'), jornada=D('8'))
        for n, (r, cuadrilla, cantidad) in enumerate([(self.operario, '2', '0'), (self.peon, '8', '0'),
                                                      (self.cemento, None, '9.73'), (self.hormigon, None, '1.05'),
                                                      (self.herramientas, None, '3'), (self.mezcladora, '1', '0')]):
            calculos.asegurar_precio(p, r)
            RecursoAPU.objects.create(partida=x, recurso=r, cuadrilla=D(cuadrilla) if cuadrilla else None,
                                      cantidad=D(cantidad), orden=n)
        return calculos.recalcular(p), x

    def test_correlativos_caben_en_la_serie(self):
        """SQLite no controla el largo de los textos (PostgreSQL sí): tipo ≤ 3 y serie ≤ 4."""
        from core.models import Serie
        from finanzas.models import Cuenta, DepositoDetraccion
        Recurso.objects.create(nombre='ARENA', unidad='m3', tipo='MAT')
        PartidaBase.objects.create(descripcion='TRAZO', unidad='m2')
        DepositoDetraccion.objects.create(cuenta=Cuenta.objects.create(nombre='Banco', moneda='PEN'))
        for s in Serie.objects.all():
            self.assertLessEqual(len(s.tipo), 3, s.tipo)
            self.assertLessEqual(len(s.serie), 4, s.serie)

    def test_obra_crea_centro_de_costo_y_almacen(self):
        o = self.obra
        self.assertTrue(o.codigo.startswith('OB'))
        self.assertEqual((o.centro_costo.padre.codigo, o.centro_costo.tipo), ('OBRAS', 'PRODUCCION'))
        self.assertEqual(o.almacen.nombre, 'Obra Edificio Los Pinos')
        otra = Obra.objects.create(nombre='Puente')
        self.assertNotEqual(otra.centro_costo, o.centro_costo)
        self.assertEqual(CentroCosto.objects.filter(codigo='OBRAS').count(), 1)

    def test_apu_presupuesto_y_explosion(self):
        p, x = self.presupuesto()
        x.refresh_from_db()
        a = calculos.analisis(x, calculos.precios_de(p))
        aportes = {f['linea'].recurso.nombre: f['aporte'] for f in a['lineas']}
        self.assertEqual((aportes['OPERARIO'], aportes['PEON'], aportes['MEZCLADORA 9-11 p3']),
                         (D('0.6400'), D('2.5600'), D('0.3200')))
        self.assertEqual(dict(a['subtotales']), {'MO': D('67.52'), 'MAT': D('345.56'), 'EQ': D('6.83'), 'SUB': 0})
        self.assertEqual((x.precio, x.parcial), (D('419.91'), D('14906.81')))
        self.assertEqual(p.partidas.get(item='02').parcial, D('14906.81'))  # el título suma sus partidas
        self.assertEqual((p.costo_directo, p.gastos_generales, p.utilidad, p.subtotal, p.igv, p.total),
                         (D('14906.81'), D('1490.68'), D('1490.68'), D('17888.17'), D('3219.87'), D('21108.04')))
        # un solo precio por recurso: sube el cemento y cambian la partida y el total
        PrecioPresupuesto.objects.filter(presupuesto=p, recurso=self.cemento).update(precio=D('30'))
        calculos.recalcular(p)
        x.refresh_from_db()
        self.assertEqual(x.precio, D('434.50'))  # 9.73 × 30 = 291.90 en lugar de 277.31
        e = calculos.explosion(p)
        cemento = next(t for t in e['filas'] if t['recurso'] == self.cemento)
        self.assertEqual((cemento['cantidad'], cemento['monto']), (D('345.4150'), D('10362.45')))
        self.assertEqual(e['por_tipo']['MO'], D('2396.96'))  # 35.5 × 67.52

    def test_versiones_y_aprobacion(self):
        p, _ = self.presupuesto()
        meta = calculos.copiar(p, 'META')
        self.assertEqual((meta.tipo, meta.version, meta.total, meta.partidas.count()), ('META', 1, p.total, 2))
        PrecioPresupuesto.objects.filter(presupuesto=meta, recurso=self.cemento).update(precio=D('1'))
        calculos.recalcular(meta)
        p.refresh_from_db()
        self.assertEqual(p.total, D('21108.04'))  # la oferta no cambia
        r = self.client.post(reverse('obras:presupuesto', args=[p.pk]), {'accion': 'aprobar'})
        p.refresh_from_db()
        self.assertEqual(p.estado, 'APROBADO')
        self.client.post(reverse('obras:presupuesto', args=[p.pk]), {
            'accion': 'agregar', 'item': '03', 'descripcion': 'OTRA', 'es_titulo': 'on'})
        self.assertFalse(p.partidas.filter(item='03').exists())
        self.assertEqual(calculos.copiar(p, 'OFERTA').version, 2)

    def test_pantallas_y_flujo_de_edicion(self):
        p, x = self.presupuesto()
        url = reverse('obras:presupuesto', args=[p.pk])
        # partida nueva → su análisis
        r = self.client.post(url, {'accion': 'agregar', 'item': '02.02', 'descripcion': 'ENCOFRADO', 'unidad': 'm2',
                                   'metrado': '120'})
        nueva = p.partidas.get(item='02.02')
        self.assertRedirects(r, reverse('obras:apu', args=[nueva.pk]))
        self.client.post(reverse('obras:apu', args=[nueva.pk]), {
            'rendimiento': '12', 'jornada': '8', 'nuevo_recurso': f'{self.operario.codigo} · OPERARIO (hh)',
            'nuevo_cuadrilla': '1', 'nuevo_cantidad': ''})
        self.client.post(reverse('obras:apu', args=[nueva.pk]), {
            'rendimiento': '12', 'jornada': '8', 'nuevo_nombre': 'MADERA TORNILLO', 'nuevo_unidad': 'p2',
            'nuevo_tipo': 'MAT', 'nuevo_cantidad': '4.5', 'nuevo_precio': '6.50'})
        nueva.refresh_from_db()
        self.assertEqual(nueva.precio, D('46.65'))  # 0.6667 × 26.10 = 17.40 + 4.5 × 6.50 = 29.25
        # biblioteca: guardar y usar en otra partida
        self.client.post(reverse('obras:apu', args=[nueva.pk]), {'accion': 'guardar_base'})
        base = PartidaBase.objects.get()
        otra = Partida.objects.create(presupuesto=p, item='02.03', descripcion='ENCOFRADO VIGAS', unidad='m2',
                                      metrado=D('10'))
        self.client.post(reverse('obras:apu', args=[otra.pk]), {'accion': 'usar_base', 'base': base.pk})
        otra.refresh_from_db()
        self.assertEqual(otra.precio, D('46.65'))
        # metrados en la tabla y eliminar
        self.client.post(url, {'accion': 'metrados', f'metrado_{otra.pk}': '20'})
        otra.refresh_from_db()
        self.assertEqual(otra.parcial, D('933.00'))
        self.client.post(url, {'accion': 'metrados', 'eliminar': otra.pk})
        self.assertFalse(Partida.objects.filter(pk=otra.pk).exists())
        for nombre, args in [('lista', []), ('obra', [self.obra.pk]), ('presupuesto', [p.pk]), ('apu', [x.pk]),
                             ('precios', [p.pk]), ('insumos', [p.pk]), ('apu_reporte', [p.pk]),
                             ('importar', [self.obra.pk]), ('recursos', []), ('biblioteca', []),
                             ('obra_nueva', []), ('presupuesto_nuevo', [self.obra.pk])]:
            self.assertEqual(self.client.get(reverse(f'obras:{nombre}', args=args)).status_code, 200, nombre)
        for nombre in ('presupuesto', 'insumos', 'apu_reporte'):
            r = self.client.get(reverse(f'obras:{nombre}', args=[p.pk]), {'formato': 'excel'})
            self.assertIn('spreadsheetml', r['Content-Type'], nombre)
        self.assertIn('spreadsheetml', self.client.get(reverse('obras:plantilla'))['Content-Type'])

    def test_importar_plantilla_de_ceiba(self):
        datos = imp.leer(SimpleUploadedFile('p.xlsx', imp.plantilla()))
        self.assertEqual([x['item'] for x in datos['partidas']], ['01', '01.01', '02', '02.01'])
        self.assertEqual(len(datos['apu']['02.01']['recursos']), 5)
        pres, _ = imp.grabar(datos, self.obra, 'OFERTA', 'Desde plantilla')
        concreto = pres.partidas.get(item='02.01')
        self.assertEqual(concreto.precio, D('417.88'))  # el análisis de la prueba anterior sin las herramientas
        trazo = pres.partidas.get(item='01.01')
        self.assertEqual(trazo.precio, D('1.27'))  # 0.032 × 19.85 + 0.016 × 25.50 + 0.02 × 9.50 + 3 % de 1.05

    def test_importar_formato_s10(self):
        presupuesto = [
            ['S10', None, None, None, None, None], ['Presupuesto', '0101001 EDIFICIO LOS PINOS', None, None, None, None],
            ['Cliente', 'INMOBILIARIA SAC', None, None, None, None], [None] * 6,
            ['Item', 'Descripción', 'Und.', 'Metrado', 'Precio S/.', 'Parcial S/.'],
            ['02', 'CONCRETO SIMPLE', None, None, None, 14906.81],
            ['02.01', "CONCRETO f'c=210 kg/cm2", 'm3', 35.5, 419.91, 14906.81],
            ['03', 'VARIOS', None, None, None, 500],
            ['03.01', 'LIMPIEZA FINAL DE OBRA', 'glb', 1, 500, 500],
            [None, 'COSTO DIRECTO', None, None, None, 15406.81]]
        analisis = [
            ['S10', None, None, None, None, None, None], ['Análisis de precios unitarios', None, None, None, None, None, None],
            ['Partida', '02.01', "CONCRETO f'c=210 kg/cm2", None, None, None, None],
            ['Rendimiento', 'm3/DIA', 'MO. 25.0000', 'EQ. 25.0000', 'Costo unitario directo por : m3', None, 419.91],
            ['Código', 'Descripción Recurso', 'Unidad', 'Cuadrilla', 'Cantidad', 'Precio S/.', 'Parcial S/.'],
            [None, 'Mano de Obra', None, None, None, None, 67.52],
            ['0147010002', 'OPERARIO', 'hh', 2.0, 0.64, 26.10, 16.70],
            ['0147010004', 'PEON', 'hh', 8.0, 2.56, 19.85, 50.82],
            [None, 'Materiales', None, None, None, None, 345.56],
            ['0221000001', 'CEMENTO PORTLAND TIPO I (42.5 kg)', 'bls', None, 9.73, 28.50, 277.31],
            ['0238000000', 'HORMIGON', 'm3', None, 1.05, 65.00, 68.25],
            [None, 'Equipos', None, None, None, None, 6.83],
            ['0337010001', 'HERRAMIENTAS MANUALES', '%mo', None, 3.0, 67.52, 2.03],
            ['0349100007', 'MEZCLADORA DE CONCRETO 9-11 p3', 'hm', 1.0, 0.32, 15.00, 4.80]]
        datos = imp.leer(excel([('Presupuesto', presupuesto), ('APU', analisis)]))
        a = datos['apu']['02.01']
        self.assertEqual((a['rendimiento'], a['unidad'], len(a['recursos'])), ('25.0000', 'm3', 6))
        self.assertEqual([r['tipo'] for r in a['recursos']], ['MO', 'MO', 'MAT', 'MAT', 'EQ', 'EQ'])
        self.assertTrue(any('sin análisis' in x for x in datos['avisos']))  # 03.01 va con su precio
        r = self.client.post(reverse('obras:importar', args=[self.obra.pk]), {
            'archivo': excel([('Presupuesto', presupuesto), ('APU', analisis)]), 'tipo': 'OFERTA', 'nombre': 'S10'})
        self.assertContains(r, 'Vista previa')
        r = self.client.post(reverse('obras:importar', args=[self.obra.pk]), {'accion': 'grabar'})
        pres = Presupuesto.objects.get(nombre='S10')
        self.assertRedirects(r, reverse('obras:presupuesto', args=[pres.pk]))
        self.assertEqual((pres.costo_directo, pres.partidas.get(item='03.01').precio_fijo), (D('15406.81'), True))
        self.assertEqual(pres.partidas.get(item='02.01').precio, D('419.91'))
        self.assertEqual(Recurso.objects.get(codigo='0221000001').nombre, 'CEMENTO PORTLAND TIPO I')  # el existente
